"""Start managed application services with explicit database and secret configuration."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
from urllib.parse import quote

from .artifact_s3 import repository_from_environment
from .owned_staging import linked

MAX_SECRET = 65536
FILES = {
    "operator_keys": ("operator-keys.json", "DMT_KEYS_FILE"),
    "sso": ("sso.json", "DMT_SSO_FILE"),
    "decision_profiles": ("decision-profiles.json", "DMT_DECISION_PROFILES_FILE"),
    "cloud_profiles": ("cloud-profiles.json", "DMT_CLOUD_PROFILES_FILE"),
    "taxonomy_profiles": ("taxonomy-profiles.json", "DMT_TAXONOMY_PROFILES_FILE"),
    "gcp_credentials": ("gcp-credentials.json", "DMT_CLOUD_GCP_CREDENTIALS_FILE"),
    "semantic_profile": ("semantic-profile.json", "DMT_SEMANTIC_PROFILE_FILE"),
}
PROVIDER_ENV = re.compile(
    r"DMT_(?:(?:PROVIDER|CLOUD|TEACHER|GENERATION|SCHEMA|TAXONOMY|SEMANTIC)_[A-Z0-9_]+|(?:MODEL|WORKER|LIQUID|LIQUID_UNSLOTH|OMNI|D3|PERPLEXITY)_PYTHON|MODEL_CACHE)\Z"
)
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def browser_environment(environment):
    prefixes = (
        "DMT_PROVIDER_",
        "DMT_CLOUD_",
        "DMT_DATABASE_",
        "DMT_ARTIFACT_",
        "DMT_BACKUP_",
        "DMT_TEACHER_",
        "DMT_TAXONOMY_",
        "DMT_AZURE_",
        "DMT_GCP_",
        "AWS_ACCESS_",
        "AWS_SECRET_",
        "AWS_SESSION_",
    )
    names = {
        "DMT_KEYS_FILE",
        "DMT_SSO_FILE",
        "DMT_CONFIG_SECRET_ID",
        "DMT_DECISION_PROFILES_FILE",
        "TEACHER_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_FEDERATED_TOKEN_FILE",
        "IDENTITY_ENDPOINT",
        "IDENTITY_HEADER",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
    }
    return {
        k: v
        for k, v in environment.items()
        if k not in names and not k.startswith(prefixes)
    }


def read_secret(client, identifier):
    """Read a bounded JSON cloud secret; never include its contents in errors."""
    try:
        response = client.get_secret_value(SecretId=identifier)
        text = response["SecretString"]
        if not isinstance(text, str) or len(text.encode()) > MAX_SECRET:
            raise ValueError()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise ValueError(
            "Deployment secret is unavailable or is not a bounded JSON object"
        ) from None


def secret_client(environment):
    provider = environment.get("DMT_DEPLOYMENT_PROVIDER", "aws")
    if provider == "azure":
        from .deployment_azure import AzureSecrets

        return AzureSecrets.from_environment(environment)
    if provider == "gcp":
        from .deployment_gcp import GCPSecrets

        return GCPSecrets.from_environment(environment)
    if provider != "aws":
        raise ValueError("Choose a supported managed deployment provider")
    import boto3

    return boto3.client("secretsmanager", region_name=environment.get("AWS_REGION"))


def private_file(folder, name, content):
    folder = Path(folder).absolute()
    if any(linked(p) for p in (folder, *folder.parents)):
        raise ValueError("Deployment secret directory must not contain links")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    path = folder / name
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    return path


def backend_configuration(value, folder, environment):
    if not isinstance(value, dict) or set(value) - {*FILES, "environment"}:
        raise ValueError("Unsupported backend deployment secret fields")
    if "operator_keys" not in value and "sso" not in value:
        raise ValueError("Backend configuration requires workspace keys or trusted SSO")
    variables = value.get("environment", {})
    if not isinstance(variables, dict) or any(
        not isinstance(key, str)
        or not PROVIDER_ENV.fullmatch(key)
        or not isinstance(item, str)
        or len(item) > 32768
        or "\0" in item
        for key, item in variables.items()
    ):
        raise ValueError("Invalid provider environment configuration")
    # Provider variables may not redirect profile files outside the private directory.

    forbidden = {variable for _, variable in FILES.values()}
    if forbidden & set(variables):
        raise ValueError("Use a private mounted file for provider credential documents")
    for key, (name, variable) in FILES.items():
        if key in value:
            path = private_file(folder, name, json.dumps(value[key], allow_nan=False))
            environment[variable] = str(path)
    environment.update(variables)


def database_url(secret, environment):
    if not isinstance(secret, dict) or any(
        not isinstance(secret.get(k), str) or not secret[k] or "\0" in secret[k]
        for k in ("username", "password")
    ):
        raise ValueError("Database secret needs username and password")
    if environment.get("DMT_DEPLOYMENT_PROVIDER") == "gcp" and environment.get(
        "DMT_GCP_CLOUD_SQL_CONNECTION_NAME"
    ):
        from .deployment_gcp import socket_database_url

        return socket_database_url(secret, environment)
    host = environment.get("DMT_DATABASE_HOST", "")
    name = environment.get("DMT_DATABASE_NAME", "workbench")
    ca = environment.get("DMT_DATABASE_CA", "/app/certificates/rds-global.pem")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or not NAME.fullmatch(name):
        raise ValueError(
            "Set the managed database hostname and canonical database name"
        )
    if not Path(ca).is_absolute() or not Path(ca).is_file():
        raise ValueError("Mount the managed database CA bundle before startup")
    return (
        "postgresql://"
        + quote(secret["username"], safe="")
        + ":"
        + quote(secret["password"], safe="")
        + "@"
        + host
        + ":5432/"
        + name
        + "?sslmode=verify-full&sslrootcert="
        + quote(ca, safe="")
    )


def initialize_database(admin_url, application_secret, environment):
    """Create only a fresh application login/schema, without changing existing passwords."""
    import psycopg
    from psycopg import sql

    role = application_secret.get("username")
    password = application_secret.get("password")
    schema = environment.get("DMT_DATABASE_SCHEMA", "decision_workbench")
    if (
        not isinstance(role, str)
        or not NAME.fullmatch(role)
        or role.startswith("pg_")
        or role in {"postgres", "public"}
    ):
        raise ValueError("Use a dedicated canonical application role")
    if (
        not NAME.fullmatch(schema)
        or schema.startswith("pg_")
        or schema in {"public", "information_schema"}
    ):
        raise ValueError("Use a dedicated canonical workspace schema")
    if not isinstance(password, str) or not password:
        raise ValueError("Application database password is missing")
    with psycopg.connect(admin_url, connect_timeout=10) as db:
        db.execute("SELECT pg_catalog.set_config('statement_timeout','60000',true)")
        db.execute("SELECT pg_catalog.set_config('lock_timeout','10000',true)")
        db.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(hashtext(%s))",
            ("dmt-bootstrap:" + schema,),
        )
        existing = db.execute(
            "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)
        ).fetchone()
        if not existing:
            db.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            # Managed database administrators have CREATEROLE, not SUPERUSER.
            # Grant the creator permission to own/manage the new role's schema.

            creator = db.execute("SELECT current_user").fetchone()[0]
            db.execute(
                sql.SQL("GRANT {} TO {} WITH SET TRUE").format(
                    sql.Identifier(role), sql.Identifier(creator)
                )
            )
        owner = db.execute(
            "SELECT pg_catalog.pg_get_userbyid(nspowner) FROM pg_catalog.pg_namespace WHERE nspname=%s",
            (schema,),
        ).fetchone()
        if owner and owner[0] != role:
            raise ValueError("Existing workspace schema belongs to another role")
        if not owner:
            db.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
        db.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(environment.get("DMT_DATABASE_NAME", "workbench")),
                sql.Identifier(role),
            )
        )
    # Do not rotate an existing role implicitly; verify the supplied app credential.

    with psycopg.connect(
        database_url(application_secret, environment), connect_timeout=10
    ) as db:
        db.execute("SELECT 1").fetchone()


def run_backend(role, environment, stop=None):
    if role not in {"api", "worker"}:
        raise ValueError("Choose the API or worker backend role")
    from .api import create_app
    from .store import Store
    from .worker import Worker

    url = environment.get("DMT_DATABASE_URL")
    if not url:
        raise ValueError("Managed services require DMT_DATABASE_URL")
    artifacts = repository_from_environment()
    if artifacts is None:
        raise ValueError("Managed services require shared artifact storage")
    home = environment.get("DMT_HOME", "/app/data/workspace")
    schema = environment.get("DMT_DATABASE_SCHEMA", "decision_workbench")
    if role == "api":
        import uvicorn
        from .shared_authentication import SharedAuthentication

        store = Store(
            home, database_url=url, database_schema=schema, artifact_store=artifacts
        )
        app = create_app(
            home,
            database_url=url,
            database_schema=schema,
            artifact_store=artifacts,
            authentication=SharedAuthentication(store),
        )
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8080,
            access_log=False,
            proxy_headers=True,
            forwarded_allow_ips=environment.get("DMT_TRUSTED_PROXY_IPS", "127.0.0.1"),
        )
        return
    stop = stop or threading.Event()
    worker = Worker(
        Store(home, database_url=url, database_schema=schema, artifact_store=artifacts)
    )
    try:
        with worker.presence():
            while not stop.is_set():
                if not worker.once():
                    stop.wait(1)
    finally:
        worker.service.runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "role", choices=("api", "worker", "web", "bootstrap", "publish-auth", "status")
    )
    parser.add_argument("--expected-auth-revision", type=int)
    args = parser.parse_args()
    # Use a fresh task-owned directory; do not overwrite a previous secret file.

    import tempfile

    try:
        client = (
            None
            if args.role == "web" and not os.environ.get("DMT_UI_SECRET_ID")
            else secret_client(os.environ)
        )
        with tempfile.TemporaryDirectory(prefix="dmt-service-") as temporary:
            private = Path(temporary)
            if args.role == "web":
                identifier = os.environ.get("DMT_UI_SECRET_ID")
                if identifier:
                    import tomllib

                    value = read_secret(client, identifier)
                    if set(value) != {"secrets_toml"} or not isinstance(
                        value["secrets_toml"], str
                    ):
                        raise ValueError("UI secret must contain only secrets_toml")
                    if "auth" not in tomllib.loads(value["secrets_toml"]):
                        raise ValueError(
                            "UI secret requires Streamlit auth configuration"
                        )
                    private_file(
                        private / ".streamlit", "secrets.toml", value["secrets_toml"]
                    )
                elif os.environ.get("DMT_UI_AUTH_MODE") == "oidc":
                    raise ValueError("OIDC mode requires DMT_UI_SECRET_ID")
                app = os.environ.get("DMT_APP_PATH", "/app/app.py")
                os.chdir(private)
                # Replace the parent so container signals reach Streamlit directly.
                # The private directory lives only in this task's ephemeral filesystem.

                os.execve(
                    sys.executable,
                    [
                        sys.executable,
                        "-m",
                        "streamlit",
                        "run",
                        app,
                        "--server.address",
                        "0.0.0.0",
                        "--server.port",
                        "8501",
                        "--server.headless",
                        "true",
                        "--browser.gatherUsageStats",
                        "false",
                    ],
                    browser_environment(os.environ),
                )
                return
            app_secret = read_secret(client, os.environ["DMT_DATABASE_SECRET_ID"])
            os.environ["DMT_DATABASE_URL"] = database_url(app_secret, os.environ)
            if args.role == "bootstrap":
                master = read_secret(client, os.environ["DMT_DATABASE_ADMIN_SECRET_ID"])
                initialize_database(
                    database_url(master, os.environ), app_secret, os.environ
                )
                print("Application database role and workspace schema are ready.")
                return
            if args.role in {"publish-auth", "status"}:
                from .store import Store
                from .shared_authentication import publish, SharedAuthentication

                store = Store(
                    os.environ.get("DMT_HOME", "/app/data/workspace"),
                    database_url=os.environ["DMT_DATABASE_URL"],
                    database_schema=os.environ.get(
                        "DMT_DATABASE_SCHEMA", "decision_workbench"
                    ),
                    artifact_store=repository_from_environment(),
                )
                if args.role == "publish-auth":
                    result = publish(
                        store,
                        read_secret(client, os.environ["DMT_CONFIG_SECRET_ID"]),
                        args.expected_auth_revision,
                    )
                else:
                    with store.transaction() as db:
                        pending = db.execute(
                            "SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancel_requested')"
                        ).fetchone()[0]
                    result = {
                        "pending_jobs": pending,
                        "authentication_revision": SharedAuthentication(store).read()[
                            "revision"
                        ],
                    }
                print(json.dumps(result))
                return
            backend_configuration(
                read_secret(client, os.environ["DMT_CONFIG_SECRET_ID"]),
                private,
                os.environ,
            )
            stop = threading.Event()
            if args.role == "worker":
                signal.signal(signal.SIGTERM, lambda *_: stop.set())
                signal.signal(signal.SIGINT, lambda *_: stop.set())
            run_backend(args.role, os.environ, stop)
    except (ValueError, KeyError, OSError, RuntimeError, ImportError):
        parser.error(
            "Managed service startup failed; check role-scoped secret access, configuration, TLS and database initialization"
        )
    except Exception as exc:
        state = getattr(exc, "sqlstate", None)
        if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state):
            parser.error(
                "Managed database startup failed (SQLSTATE "
                + state
                + "); check bootstrap privileges and schema ownership"
            )
        parser.error(
            "Managed service startup failed; inspect dependency connectivity and configuration without logging secret values"
        )


if __name__ == "__main__":
    main()
