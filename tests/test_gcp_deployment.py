"""Google managed startup contracts without credentials, network or cloud resources."""

import base64
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.platform.deployment import (
    database_url,
    read_secret,
    secret_client,
    browser_environment,
)
from decision_workbench.platform.deployment_gcp import GCPSecrets, prepare_configuration
import test_platform as fixtures


@unittest.skipUnless(
    importlib.util.find_spec("google_crc32c") and importlib.util.find_spec("psycopg"),
    "Install GCP artifact and PostgreSQL profiles for managed startup contracts",
)
class GCPStartupTests(unittest.TestCase):
    def test_native_authorized_session_uses_pinned_secret_and_integrity_check(self):
        import google_crc32c
        from google.auth.credentials import AnonymousCredentials
        from google.auth.transport.requests import AuthorizedSession
        from requests import Response
        from requests.adapters import BaseAdapter

        raw = json.dumps({"fictional": "private-value"}).encode()
        records = []
        payload = {
            "name": "projects/123456789/secrets/backend/versions/7",
            "payload": {
                "data": base64.b64encode(raw).decode(),
                "dataCrc32c": str(google_crc32c.value(raw)),
            },
        }

        class Adapter(BaseAdapter):
            def send(self, request, **kwargs):
                records.append((request, kwargs))
                response = Response()
                response.status_code = 200
                response.url = request.url
                response._content = json.dumps(payload).encode()
                response._content_consumed = True
                return response

            def close(self):
                pass

        session = AuthorizedSession(AnonymousCredentials())
        session.mount("https://", Adapter())
        client = GCPSecrets(session)
        identifier = "projects/fictional-project/secrets/backend/versions/7"
        self.assertEqual(
            read_secret(client, identifier), {"fictional": "private-value"}
        )
        self.assertEqual(
            records[0][0].url,
            "https://secretmanager.googleapis.com/v1/" + identifier + ":access",
        )
        self.assertEqual(records[0][1]["timeout"], (10, 30))
        payload["payload"]["dataCrc32c"] = "0"
        with self.assertRaisesRegex(ValueError, "bounded JSON") as caught:
            read_secret(client, identifier)
        self.assertNotIn("private-value", str(caught.exception))
        before = len(records)
        for invalid in [
            "https://foreign.example/secrets/x",
            "projects/fictional-project/secrets/../versions/7",
            "projects/fictional-project/secrets/backend/versions/0",
        ]:
            with self.assertRaises(ValueError):
                client.get_secret_value(SecretId=invalid)
        self.assertEqual(len(records), before)

    def test_socket_uri_is_accepted_by_psycopg_and_never_disables_tcp_tls(self):
        from psycopg.conninfo import conninfo_to_dict

        secret = {"username": "workbench_app", "password": "fictional:@/ value"}
        env = {
            "DMT_DEPLOYMENT_PROVIDER": "gcp",
            "DMT_GCP_CLOUD_SQL_CONNECTION_NAME": "fictional-project:us-central1:workbench-db",
        }
        uri = database_url(secret, env)
        parsed = conninfo_to_dict(uri)
        self.assertEqual(
            parsed["host"], "/cloudsql/" + env["DMT_GCP_CLOUD_SQL_CONNECTION_NAME"]
        )
        self.assertEqual(parsed["password"], secret["password"])
        self.assertEqual(parsed["sslmode"], "disable")
        for name in [
            "127.0.0.1",
            "../socket",
            "fictional-project:us-central1:" + "x" * 98,
        ]:
            with self.assertRaises(ValueError):
                database_url(secret, dict(env, DMT_GCP_CLOUD_SQL_CONNECTION_NAME=name))
        with self.assertRaises(ValueError):
            database_url(secret, dict(env, DMT_DEPLOYMENT_PROVIDER="aws"))

    def test_google_secret_factory_requests_adc_and_browser_loses_operator_settings(
        self,
    ):
        with patch(
            "google.auth.default", return_value=(object(), "fictional-project")
        ) as default, patch(
            "google.auth.transport.requests.AuthorizedSession"
        ) as session:
            secret_client({"DMT_DEPLOYMENT_PROVIDER": "gcp"})
            self.assertEqual(
                default.call_args.kwargs["scopes"],
                ["https://www.googleapis.com/auth/cloud-platform"],
            )
            self.assertEqual(session.call_args.kwargs["refresh_timeout"], 30)
        self.assertEqual(
            browser_environment(
                {
                    "DMT_GCP_CLOUD_SQL_CONNECTION_NAME": "private",
                    "GOOGLE_APPLICATION_CREDENTIALS": "private",
                    "DMT_API_URL": "https://api.example",
                }
            ),
            {"DMT_API_URL": "https://api.example"},
        )

    def test_private_initial_bundle_keeps_credentials_out_of_output_and_refuses_overwrite(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keys = root / "keys.json"
            keys.write_text(json.dumps(fixtures.keys()))
            result = prepare_configuration(keys, root / "private")
            admin = json.loads((root / "private/database-admin.json").read_text())
            app = json.loads((root / "private/database-app.json").read_text())
            self.assertEqual(admin["username"], "postgres")
            self.assertNotEqual(admin["password"], app["password"])
            self.assertNotIn(admin["password"], json.dumps(result))
            with self.assertRaises(FileExistsError):
                prepare_configuration(keys, root / "private")
