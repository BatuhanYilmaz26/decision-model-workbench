"""Secret Manager startup configuration and encrypted Cloud SQL connector sockets."""

import argparse
import base64
import json
from pathlib import Path
import re
from urllib.parse import quote

SECRET = re.compile(
    r"projects/(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})/secrets/[a-zA-Z0-9_-]{1,255}/versions/(?:latest|[1-9][0-9]{0,18})\Z"
)
CONNECTION = re.compile(
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]:[a-z]+[a-z0-9-]+[0-9]:[a-z][a-z0-9-]{0,96}[a-z0-9]\Z"
)


class GCPSecrets:
    def __init__(self, session):
        self.session = session

    @classmethod
    def from_environment(cls, environment):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return cls(AuthorizedSession(credentials, refresh_timeout=30))

    def get_secret_value(self, *, SecretId):
        if not isinstance(SecretId, str) or not SECRET.fullmatch(SecretId):
            raise ValueError(
                "Use a complete Google Secret Manager version resource name"
            )
        try:
            import google_crc32c

            response = self.session.get(
                "https://secretmanager.googleapis.com/v1/" + SecretId + ":access",
                timeout=(10, 30),
                allow_redirects=False,
                stream=True,
            )
            # Do not follow redirects with the workload identity, or buffer an unbounded body.

            try:
                if response.status_code != 200:
                    raise ValueError()
                body = bytearray()
                for chunk in response.iter_content(8192):
                    if len(body) + len(chunk) > 100000:
                        raise ValueError()
                    body.extend(chunk)
                value = json.loads(body)
            finally:
                response.close()
            resolved = value["name"]
            if (
                not isinstance(resolved, str)
                or not SECRET.fullmatch(resolved)
                or resolved.endswith("/latest")
            ):
                raise ValueError()
            if resolved.split("/")[3] != SecretId.split("/")[3] or (
                not SecretId.endswith("/latest")
                and resolved.split("/")[-1] != SecretId.split("/")[-1]
            ):
                raise ValueError()
            payload = value["payload"]
            if not isinstance(payload["data"], str) or len(payload["data"]) > 87384:
                raise ValueError()
            raw = base64.b64decode(payload["data"], validate=True)
            if len(raw) > 65536 or str(google_crc32c.value(raw)) != str(
                payload["dataCrc32c"]
            ):
                raise ValueError()
            return {"SecretString": raw.decode("utf-8")}
        except Exception:
            raise ValueError(
                "Google deployment secret is unavailable or failed integrity validation"
            ) from None


def socket_database_url(secret, environment):
    connection = environment.get("DMT_GCP_CLOUD_SQL_CONNECTION_NAME", "")
    name = environment.get("DMT_DATABASE_NAME", "workbench")
    if not CONNECTION.fullmatch(connection) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,62}", name
    ):
        raise ValueError("Set the canonical Cloud SQL connection name and database")
    socket = "/cloudsql/" + connection
    if len((socket + "/.s.PGSQL.5432").encode()) > 107:
        raise ValueError("Cloud SQL connection name exceeds the Unix socket path limit")
    # TLS and server identity are supplied by Cloud Run's managed Cloud SQL connector.
    # This disable flag applies only to the local Unix-domain socket, never TCP.

    return (
        "postgresql://"
        + quote(secret["username"], safe="")
        + ":"
        + quote(secret["password"], safe="")
        + "@/"
        + name
        + "?host="
        + quote(socket, safe="")
        + "&sslmode=disable"
    )


def prepare_configuration(keys_file, destination):
    from .auth import AuthenticationUnavailable, read_config, validate_keys
    from .deployment import private_file
    from .deployment_azure import database_password

    try:
        keys = validate_keys(read_config(keys_file))
    except AuthenticationUnavailable:
        raise ValueError("Supply a readable approved workspace-key file") from None
    if not keys:
        raise ValueError("Supply at least one approved initial workspace key")
    values = {
        "backend.json": {"operator_keys": keys, "environment": {}},
        "database-admin.json": {
            "username": "postgres",
            "password": database_password("postgres"),
        },
        "database-app.json": {
            "username": "workbench_app",
            "password": database_password("workbench_app"),
        },
    }
    bodies = {k: json.dumps(v, allow_nan=False) for k, v in values.items()}
    if any(len(b.encode()) > 65536 for b in bodies.values()):
        raise ValueError(
            "Initial configuration exceeds the Secret Manager payload limit"
        )
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, body in bodies.items():
        private_file(destination, name, body)
    return {"files": [str(destination / name) for name in values]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys-file", required=True)
    parser.add_argument("--output", required=True, help="New private output directory")
    args = parser.parse_args()
    try:
        result = prepare_configuration(args.keys_file, args.output)
    except (ValueError, OSError, RuntimeError):
        parser.error(
            "Cannot create private Google configuration; check keys and choose a new output directory"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
