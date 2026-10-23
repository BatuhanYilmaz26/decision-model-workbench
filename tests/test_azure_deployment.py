"""Azure resource contracts, private setup and installed Key Vault SDK; no account calls."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.platform import azure_deployment as stack
from decision_workbench.platform.deployment import (
    browser_environment,
    read_secret,
    secret_client,
)
from decision_workbench.platform.deployment_azure import (
    AzureSecrets,
    database_password,
    prepare_configuration,
)
import test_platform as fixtures


class AzureTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = stack.template()

    def resources(self, kind):
        return [r for r in self.template["resources"] if r["type"] == kind]

    def test_private_database_versioned_objects_and_retention_guards(self):
        db = self.resources("Microsoft.DBforPostgreSQL/flexibleServers")[0]
        self.assertEqual(db["properties"]["network"]["publicNetworkAccess"], "Disabled")
        self.assertEqual(db["properties"]["version"], "18")
        self.assertEqual(
            self.template["parameters"]["databaseAdministratorConfiguration"]["type"],
            "securestring",
        )
        self.assertIn(
            "json(parameters(", db["properties"]["administratorLoginPassword"]
        )
        account = self.resources("Microsoft.Storage/storageAccounts")[0]
        self.assertFalse(account["properties"]["allowBlobPublicAccess"])
        self.assertFalse(account["properties"]["allowSharedKeyAccess"])
        self.assertTrue(account["properties"]["supportsHttpsTrafficOnly"])
        self.assertTrue(
            self.resources("Microsoft.Storage/storageAccounts/blobServices")[0][
                "properties"
            ]["isVersioningEnabled"]
        )
        self.assertEqual(len(self.resources("Microsoft.Authorization/locks")), 2)
        self.assertNotIn("password", json.dumps(self.template["outputs"]).lower())

    def test_browser_and_backend_have_separate_identities_and_secret_grants(self):
        grants = self.resources("Microsoft.Authorization/roleAssignments")
        browser = next(r for r in grants if "browser-login" in r["name"])
        self.assertIn("uiSecretName", browser["scope"])
        self.assertIn("webIdentity", browser["properties"]["principalId"])
        self.assertIn("uiAuthMode", browser["condition"])
        for grant in grants:
            if "webIdentity" in grant["properties"]["principalId"]:
                self.assertEqual(grant, browser)
        writer = self.resources("Microsoft.Authorization/roleDefinitions")[0]
        self.assertNotIn(
            "delete", json.dumps(writer["properties"]["permissions"]).lower()
        )
        self.assertNotIn("*", json.dumps(writer["properties"]["permissions"]))
        self.assertTrue(any("registry-pull" in r["name"] for r in grants))

    def test_two_phase_creation_healthy_https_apps_and_persistent_worker(self):
        self.assertFalse(self.template["parameters"]["deployServices"]["defaultValue"])
        apps = self.resources("Microsoft.App/containerApps")
        self.assertEqual(len(apps), 3)
        for app in apps:
            self.assertEqual(app["condition"], "[parameters('deployServices')]")
            container = app["properties"]["template"]["containers"][0]
            values = {v["name"]: v["value"] for v in container["env"]}
            self.assertEqual(values["DMT_DEPLOYMENT_PROVIDER"], "azure")
            self.assertEqual(
                app["properties"]["template"]["terminationGracePeriodSeconds"], 120
            )
            if container["command"][-1] == "worker":
                self.assertNotIn("ingress", app["properties"]["configuration"])
                self.assertEqual(
                    app["properties"]["template"]["scale"]["minReplicas"],
                    "[parameters('workerReplicas')]",
                )
                self.assertEqual(
                    self.template["parameters"]["workerReplicas"]["minValue"], 1
                )
            else:
                self.assertFalse(
                    app["properties"]["configuration"]["ingress"]["allowInsecure"]
                )
                self.assertTrue(
                    any(p["type"] == "Readiness" for p in container["probes"])
                )
            if container["command"][-1] == "web":
                self.assertNotIn("DMT_DATABASE_SECRET_ID", values)
                self.assertNotIn("DMT_ARTIFACT_AZURE_ACCOUNT_URL", values)
                self.assertIn("if(parameters('deployServices')", values["DMT_API_URL"])
        jobs = self.resources("Microsoft.App/jobs")
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            self.assertEqual(job["properties"]["configuration"]["replicaRetryLimit"], 0)
            self.assertEqual(
                job["properties"]["configuration"]["triggerType"], "Manual"
            )
        self.assertEqual(len(self.resources("Microsoft.Insights/metricAlerts")), 1)
        self.assertIn("@sha256:", self.template["variables"]["image"])

    def test_private_bundle_is_new_and_prints_only_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            keys = root / "keys.json"
            keys.write_text(json.dumps(fixtures.keys()))
            result = prepare_configuration(keys, root / "private")
            self.assertEqual(len(result["files"]), 3)
            admin = json.loads((root / "private/database-admin.json").read_text())
            app = json.loads((root / "private/database-app.json").read_text())
            self.assertNotEqual(admin["password"], app["password"])
            self.assertNotIn(admin["password"], json.dumps(result))
            self.assertEqual(admin["username"], "workbenchadmin")
            self.assertGreaterEqual(len(app["password"]), 40)
            before = (root / "private/backend.json").read_bytes()
            with self.assertRaises(FileExistsError):
                prepare_configuration(keys, root / "private")
            self.assertEqual((root / "private/backend.json").read_bytes(), before)
            if os.name != "nt":
                self.assertEqual(
                    (root / "private/database-app.json").stat().st_mode & 0o777, 0o600
                )

    def test_password_complexity_is_guaranteed_even_for_a_single_category_random_draw(
        self,
    ):
        with patch(
            "decision_workbench.platform.deployment_azure.secrets.token_urlsafe",
            side_effect=["workbenchadmin" + "x" * 35, "x" * 48],
        ):
            value = database_password("workbenchadmin")
        self.assertTrue(any(c.islower() for c in value))
        self.assertTrue(any(c.isupper() for c in value))
        self.assertTrue(any(c.isdigit() for c in value))
        self.assertTrue(any(not c.isalnum() for c in value))
        self.assertTrue(8 <= len(value) <= 128)
        self.assertNotIn("admin", value.casefold())

    def test_oversized_initial_key_inventory_does_not_create_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            keys = [
                {
                    "key_sha256": hashlib.sha256(str(n).encode()).hexdigest(),
                    "tenant": "A",
                    "actor": "a" * 200,
                    "role": "admin",
                }
                for n in range(120)
            ]
            file = root / "keys.json"
            file.write_text(json.dumps(keys))
            with self.assertRaisesRegex(ValueError, "size limit"):
                prepare_configuration(file, root / "refused")
            self.assertFalse((root / "refused").exists())

    def test_ui_environment_excludes_managed_identity_and_server_credentials(self):
        values = {
            "DMT_AZURE_KEY_VAULT_URL": "https://fictional.vault.azure.net",
            "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID": "fictional-id",
            "IDENTITY_HEADER": "fictional-private-header",
            "IDENTITY_ENDPOINT": "http://127.0.0.1/identity",
            "AZURE_FEDERATED_TOKEN_FILE": "/private/token",
            "AZURE_CLIENT_ID": "private-id",
            "DMT_API_URL": "https://api.example.com",
        }
        self.assertEqual(
            browser_environment(values), {"DMT_API_URL": "https://api.example.com"}
        )
        with self.assertRaisesRegex(ValueError, "supported"):
            secret_client({"DMT_DEPLOYMENT_PROVIDER": "unsupported"})


try:
    HAVE_SDK = importlib.util.find_spec("azure.keyvault.secrets") is not None
except ModuleNotFoundError:
    HAVE_SDK = False


@unittest.skipUnless(
    HAVE_SDK, "Install requirements/azure-application.txt for Key Vault contracts"
)
class AzureKeyVaultTests(unittest.TestCase):
    def test_actual_sdk_secret_version_and_sanitized_failures(self):
        from azure.core.credentials import AccessToken
        from azure.core.pipeline.transport import HttpTransport, HttpResponse
        from azure.keyvault.secrets import SecretClient
        from requests.structures import CaseInsensitiveDict

        requests = []

        class Response(HttpResponse):
            def __init__(self, request):
                super().__init__(request, None)
                self.status_code = 200
                self.headers = CaseInsensitiveDict({"content-type": "application/json"})
                self.content_type = "application/json"

            def body(self):
                return json.dumps(
                    {
                        "value": json.dumps({"fictional": "secret-value"}),
                        "id": "https://fictional.vault.azure.net/secrets/backend/"
                        + "a" * 32,
                        "attributes": {"enabled": True},
                    }
                ).encode()

            def json(self):
                return json.loads(self.body())

        class Transport(HttpTransport):
            def open(self):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def send(self, request, **kwargs):
                requests.append(request)
                return Response(request)

        credential = SimpleNamespace(
            get_token=lambda *a, **k: AccessToken(
                "fictional-token", int(time.time()) + 3600
            )
        )
        client = SecretClient(
            "https://fictional.vault.azure.net",
            credential,
            transport=Transport(),
            retry_total=0,
        )
        adapter = AzureSecrets(client)
        self.assertEqual(
            read_secret(adapter, "backend/" + "a" * 32), {"fictional": "secret-value"}
        )
        self.assertIn("/secrets/backend/" + "a" * 32, requests[0].url)
        with self.assertRaises(ValueError):
            adapter.get_secret_value(SecretId="https://untrusted.example/key")
        broken = AzureSecrets(
            SimpleNamespace(
                get_secret=lambda *a: (_ for _ in ()).throw(
                    RuntimeError("fictional-private-value")
                )
            )
        )
        with self.assertRaises(ValueError) as raised:
            read_secret(broken, "backend")
        self.assertNotIn("fictional-private-value", str(raised.exception))

    def test_environment_selects_managed_identity_and_rejects_non_vault_urls(self):
        environment = {
            "DMT_DEPLOYMENT_PROVIDER": "azure",
            "DMT_AZURE_KEY_VAULT_URL": "https://fictional.vault.azure.net",
            "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID": "fictional-managed-id",
        }
        credential = SimpleNamespace()
        with patch(
            "azure.identity.DefaultAzureCredential", return_value=credential
        ) as credentials, patch(
            "azure.keyvault.secrets.SecretClient", return_value=SimpleNamespace()
        ) as client:
            adapter = secret_client(environment)
        self.assertIsInstance(adapter, AzureSecrets)
        self.assertEqual(
            credentials.call_args.kwargs["managed_identity_client_id"],
            "fictional-managed-id",
        )
        self.assertTrue(
            credentials.call_args.kwargs["exclude_interactive_browser_credential"]
        )
        self.assertEqual(client.call_args.kwargs["retry_total"], 0)
        for url in [
            "http://fictional.vault.azure.net",
            "https://fictional.vault.azure.net?sig=private",
            "https://outside.example",
        ]:
            with self.assertRaises(ValueError):
                secret_client({**environment, "DMT_AZURE_KEY_VAULT_URL": url})


if __name__ == "__main__":
    unittest.main()
