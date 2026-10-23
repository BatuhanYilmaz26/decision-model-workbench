"""CloudFormation shape, secret boundaries and real disposable database bootstrap."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import uuid

from decision_workbench.platform import aws_deployment, deployment
from decision_workbench.platform.auth import validate_keys
import test_platform as fixtures
import test_postgres as pg


class AwsTemplateTests(unittest.TestCase):
    def test_private_services_tls_persistence_and_two_phase_startup(self):
        value = aws_deployment.template()
        self.assertLess(len(json.dumps(value, indent=2).encode()), 51200)
        resources = value["Resources"]
        self.assertFalse(resources["Database"]["Properties"]["PubliclyAccessible"])
        self.assertTrue(resources["Database"]["Properties"]["StorageEncrypted"])
        self.assertTrue(resources["Database"]["Properties"]["DeletionProtection"])
        self.assertEqual(resources["Artifacts"]["DeletionPolicy"], "Retain")
        self.assertEqual(
            resources["Artifacts"]["Properties"]["VersioningConfiguration"]["Status"],
            "Enabled",
        )
        self.assertEqual(resources["Https"]["Properties"]["Port"], 443)
        for name in ("ApiService", "WorkerService", "UiService"):
            config = resources[name]["Properties"]
            self.assertEqual(config["DesiredCount"]["Fn::If"][0], "RunServices")
            self.assertEqual(config["DesiredCount"]["Fn::If"][2], 0)
            self.assertEqual(
                config["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"],
                "DISABLED",
            )
            self.assertTrue(
                config["DeploymentConfiguration"]["DeploymentCircuitBreaker"][
                    "Rollback"
                ]
            )
        self.assertEqual(
            resources["ApiTarget"]["Properties"]["HealthCheckPath"], "/ready"
        )
        self.assertEqual(value["Parameters"]["ServicesEnabled"]["Default"], "false")

    def test_browser_and_backend_roles_cannot_read_database_admin_secret(self):
        resources = aws_deployment.template()["Resources"]
        for name in ("BackendRole", "UiRole", "ApiTask", "WorkerTask", "UiTask"):
            text = json.dumps(resources[name])
            self.assertNotIn("MasterUserSecret", text)
            self.assertNotIn("DMT_DATABASE_ADMIN_SECRET_ID", text)
        ui = json.dumps(resources["UiTask"])
        for field in (
            "DMT_CONFIG_SECRET_ID",
            "DMT_DATABASE_SECRET_ID",
            "DMT_ARTIFACT_S3_BUCKET",
        ):
            self.assertNotIn(field, ui)
        self.assertIn("MasterUserSecret", json.dumps(resources["BootstrapRole"]))
        actions = json.dumps(resources["BackendRole"])
        self.assertNotIn("s3:Delete", actions)
        self.assertNotIn('"Action": "*"', actions)
        pattern = aws_deployment.template()["Parameters"]["ImageUri"]["AllowedPattern"]
        digest = (
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/workbench@sha256:" + "a" * 64
        )
        self.assertIsNotNone(re.fullmatch(pattern, digest))
        self.assertIsNone(re.fullmatch(pattern, digest.split("@")[0] + ":latest"))


class DeploymentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()

    def test_private_files_are_exclusive_and_provider_variables_are_scoped(self):
        environment = {}
        deployment.backend_configuration(
            {
                "operator_keys": fixtures.keys(),
                "gcp_credentials": {"type": "external_account", "fictional": True},
                "environment": {"DMT_PROVIDER_LIQUID_API_KEY": "fictional-private-key"},
            },
            self.home,
            environment,
        )
        keys = json.loads(Path(environment["DMT_KEYS_FILE"]).read_text())
        self.assertEqual(len(validate_keys(keys)), len(fixtures.keys()))
        self.assertTrue(Path(environment["DMT_CLOUD_GCP_CREDENTIALS_FILE"]).is_file())
        self.assertEqual(
            environment["DMT_PROVIDER_LIQUID_API_KEY"], "fictional-private-key"
        )
        with self.assertRaises(FileExistsError):
            deployment.backend_configuration(
                {"operator_keys": fixtures.keys()}, self.home, {}
            )
        for value in (
            {"environment": {}},
            {"operator_keys": [], "environment": {"PATH": "unsafe"}},
            {
                "operator_keys": fixtures.keys(),
                "environment": {"DMT_CLOUD_GCP_CREDENTIALS_FILE": "/outside/file"},
            },
        ):
            with self.assertRaises(ValueError):
                deployment.backend_configuration(value, self.home / "bad", {})

    def test_browser_environment_excludes_all_backend_and_provider_credentials(self):
        secret = "fictional-private-value"
        names = [
            "DMT_KEYS_FILE",
            "DMT_SSO_FILE",
            "DMT_CONFIG_SECRET_ID",
            "DMT_DATABASE_URL",
            "DMT_ARTIFACT_S3_BUCKET",
            "DMT_PROVIDER_LIQUID_API_KEY",
            "DMT_CLOUD_MODAL_TOKEN_SECRET",
            "DMT_TEACHER_API_KEY",
            "DMT_TAXONOMY_TYPESAFE_API_KEY",
            "TEACHER_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ]
        result = deployment.browser_environment(
            {
                **dict.fromkeys(names, secret),
                "DMT_API_URL": "https://workbench.example",
                "DMT_UI_AUTH_MODE": "oidc",
            }
        )
        self.assertNotIn(secret, result.values())
        self.assertEqual(result["DMT_UI_AUTH_MODE"], "oidc")
        self.assertEqual(result["DMT_API_URL"], "https://workbench.example")

    def test_database_uri_encodes_credentials_and_requires_hostname_verification(self):
        ca = self.home / "ca.pem"
        ca.write_text("fictional fixture certificate")
        result = deployment.database_url(
            {"username": "app", "password": "p@ss:/?#"},
            {"DMT_DATABASE_HOST": "database.example", "DMT_DATABASE_CA": str(ca)},
        )
        self.assertIn("p%40ss%3A%2F%3F%23", result)
        self.assertIn("sslmode=verify-full", result)
        with self.assertRaises(ValueError):
            deployment.database_url(
                {"username": "app", "password": "fictional"},
                {"DMT_DATABASE_HOST": "host/other", "DMT_DATABASE_CA": str(ca)},
            )


try:
    import boto3
    from botocore.stub import Stubber
except ImportError:
    boto3 = None


@unittest.skipUnless(boto3, "Install requirements/aws-sdk.txt for secret API contracts")
class SecretManagerTests(unittest.TestCase):
    def test_sdk_request_and_secret_errors_are_bounded_and_sanitized(self):
        client = boto3.client(
            "secretsmanager",
            region_name="us-east-1",
            aws_access_key_id="fictional",
            aws_secret_access_key="fictional",
        )
        with Stubber(client) as stub:
            stub.add_response(
                "get_secret_value",
                {"SecretString": '{"operator_keys":[]}'},
                {"SecretId": "fictional-backend"},
            )
            self.assertEqual(
                deployment.read_secret(client, "fictional-backend"),
                {"operator_keys": []},
            )
            stub.add_client_error(
                "get_secret_value",
                "AccessDeniedException",
                "private information",
                expected_params={"SecretId": "fictional-backend"},
            )
            with self.assertRaises(ValueError) as error:
                deployment.read_secret(client, "fictional-backend")
            self.assertNotIn("private information", str(error.exception))
            stub.add_response(
                "get_secret_value",
                {"SecretString": "{" * (deployment.MAX_SECRET + 1)},
                {"SecretId": "fictional-backend"},
            )
            with self.assertRaises(ValueError):
                deployment.read_secret(client, "fictional-backend")
            stub.assert_no_pending_responses()


@unittest.skipUnless(
    pg.URL, "Set DMT_TEST_POSTGRES_URL for real database-role bootstrap"
)
class DeploymentDatabaseTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        # Own a fresh role and replace only this case's empty test schema.
        self.role = "dmt_app_test_" + uuid.uuid4().hex
        self.admin_role = None
        self.addCleanup(self.cleanup_role)

    def database_cleanup(self):
        import psycopg
        from psycopg import sql

        assert self.schema.startswith("dmt_test_") and len(self.schema) == 41
        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(self.schema)
                )
            )

    def cleanup_role(self):
        import psycopg
        from psycopg import sql

        assert self.role.startswith("dmt_app_test_") and len(self.role) == 45
        assert self.schema.startswith("dmt_test_") and len(self.schema) == 41
        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(self.schema)
                )
            )
            if db.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (self.role,)
            ).fetchone():
                if self.admin_role:
                    db.execute(
                        sql.SQL("SET ROLE {}").format(sql.Identifier(self.admin_role))
                    )
                    try:
                        db.execute(
                            sql.SQL(
                                "REVOKE CONNECT ON DATABASE dmt_test FROM {}"
                            ).format(sql.Identifier(self.role))
                        )
                    finally:
                        db.execute("RESET ROLE")
                db.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE dmt_test FROM {}").format(
                        sql.Identifier(self.role)
                    )
                )
                db.execute(
                    sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.role))
                )
                db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.role)))
            if self.admin_role:
                assert (
                    self.admin_role.startswith("dmt_admin_test_")
                    and len(self.admin_role) == 47
                )
                db.execute(
                    sql.SQL(
                        "REVOKE CONNECT,CREATE ON DATABASE dmt_test FROM {}"
                    ).format(sql.Identifier(self.admin_role))
                )
                db.execute(
                    sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.admin_role))
                )
                db.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(self.admin_role))
                )

    def test_bootstrap_works_with_create_role_admin_without_superuser(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        self.admin_role = "dmt_admin_test_" + uuid.uuid4().hex
        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {}").format(sql.Identifier(self.schema)))
            db.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN CREATEROLE PASSWORD 'fictional-admin'"
                ).format(sql.Identifier(self.admin_role))
            )
            db.execute(
                sql.SQL(
                    "GRANT CONNECT,CREATE ON DATABASE dmt_test TO {} WITH GRANT OPTION"
                ).format(sql.Identifier(self.admin_role))
            )
        options = conninfo_to_dict(pg.URL)
        options.update(user=self.admin_role, password="fictional-admin")
        admin_url = make_conninfo(**options)
        options.update(user=self.role, password="fictional-app")
        app_url = make_conninfo(**options)
        with patch.object(deployment, "database_url", return_value=app_url):
            deployment.initialize_database(
                admin_url,
                {"username": self.role, "password": "fictional-app"},
                {"DMT_DATABASE_SCHEMA": self.schema, "DMT_DATABASE_NAME": "dmt_test"},
            )
        with psycopg.connect(admin_url) as db:
            self.assertFalse(
                db.execute(
                    "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
                ).fetchone()[0]
            )

    def test_bootstrap_creates_only_an_application_role_and_owned_schema(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from decision_workbench.platform.store import Store

        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {}").format(sql.Identifier(self.schema)))
        env = {"DMT_DATABASE_SCHEMA": self.schema, "DMT_DATABASE_NAME": "dmt_test"}
        secret = {"username": self.role, "password": "fictional-app-password"}
        options = conninfo_to_dict(pg.URL)
        options.update(user=self.role, password=secret["password"])
        app_url = make_conninfo(**options)
        # The local fixture is plaintext; TLS URI construction is checked separately.
        with patch.object(deployment, "database_url", return_value=app_url):
            deployment.initialize_database(pg.URL, secret, env)
            deployment.initialize_database(pg.URL, secret, env)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder, database_url=app_url, database_schema=self.schema)
            self.assertEqual(store.backend, "postgresql")
        with psycopg.connect(pg.URL) as db:
            flags = db.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s",
                (self.role,),
            ).fetchone()
            self.assertEqual(flags, (False, False, False))


if __name__ == "__main__":
    unittest.main()
