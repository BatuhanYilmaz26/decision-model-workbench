"""Real asymmetric signatures, loopback JWKS, live credential CLI and authorization fences."""

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import jwt
import httpx
from streamlit.testing.v1 import AppTest
from http_server_fixture import LiveServer
from decision_workbench.data import jsonl
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from fastapi.testclient import TestClient
import test_platform as fixtures
from decision_workbench.platform.api import create_app
from decision_workbench.platform.auth import AuthenticationUnavailable, MAX_CONFIG_BYTES
from decision_workbench.platform.credentials import credentials
from decision_workbench.platform.worker import Worker


def jwk(key, kid, algorithm="RS256"):
    value = json.loads(
        (
            jwt.algorithms.RSAAlgorithm
            if algorithm == "RS256"
            else jwt.algorithms.ECAlgorithm
        ).to_jwk(key.public_key())
    )
    return {**value, "kid": kid, "alg": algorithm, "use": "sig", "key_ops": ["verify"]}


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.document = {"keys": [jwk(self.private, "first")]}
        self.http_status = 200
        self.requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests += 1
                self.send_response(owner.http_status)
                self.send_header("Content-Type", "application/json")
                if owner.http_status == 302:
                    self.send_header("Location", "/untrusted-redirect")
                self.end_headers()
                self.wfile.write(json.dumps(owner.document).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.issuer = f"http://127.0.0.1:{self.server.server_port}/issuer"
        self.config = {
            "issuers": [
                {
                    "issuer": self.issuer,
                    "jwks_url": f"http://127.0.0.1:{self.server.server_port}/keys",
                    "audience": "urn:domain-platform-api",
                    "algorithms": ["RS256", "ES256"],
                    "required_scopes": ["decisions.read"],
                    "jwks_cache_seconds": 1,
                    "bindings": [
                        {
                            "subject": "alice",
                            "tenant": "A",
                            "actor": "employee-alice",
                            "role": "admin",
                        },
                        {
                            "subject": "bob",
                            "tenant": "B",
                            "actor": "employee-bob",
                            "role": "admin",
                        },
                        {
                            "subject": "reader",
                            "tenant": "A",
                            "actor": "employee-reader",
                            "role": "inference",
                        },
                        {"group": "editors-a", "tenant": "A", "role": "editor"},
                        {"group": "admins-b", "tenant": "B", "role": "admin"},
                    ],
                }
            ]
        }
        self.sso_file = self.folder / "sso.json"
        self.key_file = self.folder / "keys.json"
        self.write_sso()
        self.local = credentials(
            self.key_file, "provision", "A", "local-owner", "admin"
        )["token"]
        self.env = patch.dict(
            os.environ,
            {"DMT_KEYS_FILE": str(self.key_file), "DMT_SSO_FILE": str(self.sso_file)},
        )
        self.env.start()
        self.app = create_app(self.folder / "workspace")
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def write_sso(self):
        self.sso_file.write_text(json.dumps(self.config), encoding="utf-8")

    def token(
        self,
        subject="alice",
        key=None,
        kid="first",
        algorithm="RS256",
        header=None,
        **changes,
    ):
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": subject,
            "aud": "urn:domain-platform-api",
            "iat": now,
            "exp": now + 300,
            "jti": uuid.uuid4().hex,
            "client_id": "fixture-client",
            "scope": "decisions.read",
        }
        for name, value in changes.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return jwt.encode(
            claims,
            key or self.private,
            algorithm=algorithm,
            headers={"typ": "at+jwt", "kid": kid, **(header or {})},
        )

    def call(self, token, method="GET", path="/v1/me", **kwargs):
        return self.client.request(
            method, path, headers={"Authorization": "Bearer " + token}, **kwargs
        )

    def project(self):
        reply = self.call(
            self.local,
            "POST",
            "/v1/projects",
            json={
                "name": "Auth fixture",
                "description": "Fictional support routing",
                "questions": fixtures.QUESTIONS,
            },
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        return reply.json()["id"]

    def test_signed_identity_role_scope_and_explicit_group_bindings(self):
        project = self.project()
        result = self.call(self.token(tenant="B", role="inference"))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            result.json(), {"tenant": "A", "actor": "employee-alice", "role": "admin"}
        )
        self.assertEqual(
            self.call(self.token("bob"), path=f"/v1/projects/{project}").status_code,
            404,
        )
        self.assertEqual(
            self.call(
                self.token("reader", tenant="B", role="admin"),
                "POST",
                "/v1/projects",
                json={
                    "name": "Forbidden",
                    "description": "Cannot escalate",
                    "questions": fixtures.QUESTIONS,
                },
            ).status_code,
            403,
        )
        group = self.call(self.token("group-user", groups=["editors-a"]))
        self.assertEqual(group.status_code, 200, group.text)
        self.assertEqual(group.json()["role"], "editor")
        self.assertEqual(group.json()["tenant"], "A")
        self.assertEqual(
            group.json()["actor"],
            self.call(self.token("group-user", groups=["editors-a"])).json()["actor"],
        )
        self.assertNotEqual(
            group.json()["actor"],
            self.call(self.token("different-user", groups=["editors-a"])).json()[
                "actor"
            ],
        )
        self.assertEqual(
            self.call(
                self.token("ambiguous", groups=["editors-a", "admins-b"])
            ).status_code,
            403,
        )
        self.assertEqual(self.call(self.token("unmapped")).status_code, 403)
        self.assertEqual(self.call(self.token(scope="unrelated")).status_code, 403)
        # Explicit subject mapping overrides conflicting group claims.
        self.assertEqual(
            self.call(self.token("reader", groups=["admins-b"])).json()["role"],
            "inference",
        )

    def test_invalid_claims_signature_token_type_and_untrusted_headers_are_refused(
        self,
    ):
        now = int(time.time())
        invalid = [
            self.token(header={"typ": "JWT"}),
            self.token(header={"jku": "http://127.0.0.1:1/attacker"}),
            self.token(header={"crit": ["unknown"]}),
            self.token(iss="https://untrusted.invalid"),
            self.token(aud="other-app"),
            self.token(exp=now - 1, iat=now - 300),
            self.token(iat=now + 600, exp=now + 900),
            self.token(nbf=now + 600),
            self.token(exp=None),
            self.token(jti=None),
            self.token(client_id=None),
            self.token(exp=str(now + 300)),
            self.token(exp=10**300),
            self.token(sub=42),
            self.token(exp=now + 10000),
            self.token(
                key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ),
            self.token(
                key="untrusted-secret-that-is-more-than-32-bytes", algorithm="HS256"
            ),
            jwt.encode(
                {"iss": self.issuer},
                "",
                algorithm="none",
                headers={"typ": "at+jwt", "kid": "first"},
            ),
        ]
        for token in invalid:
            with self.subTest(token_prefix=token[:10]):
                reply = self.call(token)
                self.assertEqual(reply.status_code, 401, reply.text)
                self.assertEqual(reply.headers["www-authenticate"], "Bearer")
                self.assertNotIn(token, reply.text)
        self.assertLessEqual(self.requests, 1)
        self.assertEqual(self.call("x" * 16385).status_code, 401)

    def test_es256_and_key_rotation_cache_retirement_outage_and_redirect(self):
        self.assertEqual(self.call(self.token()).status_code, 200)
        second = ec.generate_private_key(ec.SECP256R1())
        self.document = {"keys": [jwk(second, "second", "ES256")]}
        signed = self.token(key=second, kid="second", algorithm="ES256")
        self.assertEqual(self.call(signed).status_code, 200)
        count = self.requests
        self.assertEqual(count, 2)
        self.assertEqual(self.call(self.token()).status_code, 401)
        for _ in range(5):
            self.assertEqual(
                self.call(self.token(kid="unknown-" + uuid.uuid4().hex)).status_code,
                401,
            )
        self.assertEqual(self.requests, count)
        time.sleep(1.05)
        self.http_status = 500
        self.assertEqual(self.call(signed).status_code, 503)
        self.http_status = 302
        before = self.requests
        self.assertEqual(self.call(signed).status_code, 503)
        self.assertEqual(self.requests, before + 1)
        self.http_status = 200
        self.assertEqual(self.call(signed).status_code, 200)

    def test_live_mapping_token_revocation_and_bad_configuration_fail_closed(self):
        signed = self.token()
        self.assertEqual(self.call(signed).status_code, 200)
        self.config["issuers"][0]["bindings"][0]["role"] = "reviewer"
        self.write_sso()
        self.assertEqual(self.call(signed).json()["role"], "reviewer")
        claims = jwt.decode(signed, options={"verify_signature": False})
        self.config["revoked_tokens"] = [{"issuer": self.issuer, "jti": claims["jti"]}]
        self.write_sso()
        self.assertEqual(self.call(signed).status_code, 401)
        self.sso_file.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.call(self.local).status_code, 503)
        self.assertEqual(self.client.get("/ready").status_code, 503)
        self.assertEqual(self.call(self.token()).status_code, 503)
        self.write_sso()
        self.assertEqual(self.client.get("/ready").status_code, 200)
        self.key_file.write_text("[]", encoding="utf-8")
        self.assertEqual(self.call(self.local).status_code, 401)
        self.assertEqual(self.call(self.token("bob")).status_code, 200)

    def test_actual_cli_rotation_revoke_listing_and_cross_process_edits(self):
        def command(*args):
            result = subprocess.run(
                [sys.executable, "-m", "decision_workbench.platform.cli", *args],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        listed = command("list-keys", "--file", str(self.key_file))
        self.assertNotIn(self.local, listed)
        self.assertNotIn(hashlib.sha256(self.local.encode()).hexdigest(), listed)
        rotated = command(
            "rotate-key",
            "--file",
            str(self.key_file),
            "--tenant",
            "A",
            "--actor",
            "local-owner",
            "--role",
            "inference",
        ).splitlines()[-1]
        self.assertEqual(self.call(self.local).status_code, 401)
        self.assertEqual(self.call(rotated).json()["role"], "inference")
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "decision_workbench.platform.cli",
                    "provision-key",
                    "--file",
                    str(self.key_file),
                    "--tenant",
                    "A",
                    "--actor",
                    actor,
                    "--role",
                    "reviewer",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for actor in ("parallel-one", "parallel-two")
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(len(json.loads(self.key_file.read_text(encoding="utf-8"))), 3)
        command(
            "revoke-key",
            "--file",
            str(self.key_file),
            "--tenant",
            "A",
            "--actor",
            "local-owner",
        )
        self.assertEqual(self.call(rotated).status_code, 401)
        self.assertEqual(len(json.loads(self.key_file.read_text(encoding="utf-8"))), 2)
        self.assertEqual(list(self.folder.glob(".dmt-keys-*")), [])

    def test_live_http_jwt_worker_lifecycle_and_browser_connection(self):
        signed = self.token()
        with LiveServer(self.folder / "workspace", None) as server:
            with httpx.Client(
                base_url=server.url,
                headers={"Authorization": "Bearer " + signed},
                timeout=30,
            ) as client:
                created = client.post(
                    "/v1/projects",
                    json={
                        "name": "JWT HTTP lifecycle",
                        "description": "Fictional domain authentication acceptance",
                        "questions": fixtures.QUESTIONS,
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)
                project = created.json()["id"]
                prefix = "/v1/projects/" + project
                imported = client.post(
                    prefix + "/datasets",
                    files={
                        "file": (
                            "fixture.jsonl",
                            jsonl(fixtures.fixture_rows()).encode(),
                        )
                    },
                )
                self.assertEqual(imported.status_code, 201, imported.text)
                dataset = imported.json()
                for row in fixtures.fixture_rows():
                    reviewed = client.post(
                        prefix + "/datasets/" + dataset["id"] + "/reviews",
                        json={
                            "record_id": row["id"],
                            "gold": row["gold"],
                            "approved": True,
                            "expected_hash": dataset["payload"]["sha256"],
                            "note": "Fictional issuer/worker fixture",
                        },
                    )
                    self.assertEqual(reviewed.status_code, 201, reviewed.text)
                    dataset = reviewed.json()

                def run(kind, config):
                    queued = client.post(
                        prefix + "/jobs",
                        json={
                            "kind": kind,
                            "config": config,
                            "request_key": "jwt-" + kind,
                        },
                    )
                    self.assertEqual(queued.status_code, 202, queued.text)
                    worker = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "decision_workbench.platform.worker",
                            "--home",
                            str(self.folder / "workspace"),
                            "--once",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(worker.returncode, 0, worker.stderr)
                    job = self.app.state.store.job(queued.json()["id"], "A", project)
                    self.assertEqual(job["status"], "succeeded", job["error"])
                    return job["result"]

                snapshot = run("prepare", {"datasets": [dataset["id"]]})["snapshot"]
                policy = client.put(
                    prefix + "/release-policy",
                    json={
                        "min_accuracy": 0.0,
                        "min_macro_f1": 0.0,
                        "max_ece": 1.0,
                        "min_test_rows": 10,
                    },
                )
                self.assertEqual(policy.status_code, 200, policy.text)
                model = run("train", {"snapshot": snapshot, "backend": "cpu-baseline"})[
                    "model"
                ]
                released = client.post(
                    prefix + "/releases",
                    json={
                        "model_id": model,
                        "expected_revision": 0,
                        "note": "Fictional JWT lifecycle only",
                    },
                )
                self.assertEqual(released.status_code, 200, released.text)
                inference = client.post(
                    prefix + "/decisions",
                    json={
                        "state": "invoice payment refund requested urgent",
                        "threshold": 0.8,
                    },
                    headers={"Authorization": "Bearer " + self.token("reader")},
                )
                self.assertEqual(inference.status_code, 200, inference.text)
                self.assertEqual(inference.json()["model_id"], model)
                self.assertIsInstance(
                    inference.json()["answers"]["refund"]["answer"], bool
                )
                events = client.get(prefix + "/audit").json()
                self.assertTrue(
                    any(
                        event["actor"] == "employee-alice"
                        and event["action"].startswith("submit.")
                        for event in events
                    )
                )
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
            ).run()
            app.text_input[0].set_value(server.url)
            app.text_input[1].set_value(signed)
            next(
                button for button in app.button if button.label == "Connect"
            ).click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(
                app.session_state["principal"],
                {"tenant": "A", "actor": "employee-alice", "role": "admin"},
            )
            self.assertEqual(len(app.tabs), 6)
            next(
                button for button in app.button if button.label == "Sign out"
            ).click().run(timeout=30)
            self.assertNotIn("credential", app.session_state)

    def test_malformed_bounded_jwks_and_credential_update_limits(self):
        good = jwk(self.private, "first")
        documents = [
            [],
            {"keys": "not-a-key-list"},
            {"keys": [good, good]},
            {"keys": [good] * 101},
            {"keys": [good], "padding": "x" * (MAX_CONFIG_BYTES + 1)},
            {"keys": [{**good, "d": "private-material-refused"}]},
        ]
        for document in documents:
            self.document = document
            self.app.state.authentication.providers.clear()
            with self.subTest(document_type=type(document).__name__):
                response = self.call(self.token())
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(
                    response.json()["detail"], "Trusted signing keys are unavailable"
                )
        self.document = {"keys": [good]}
        self.app.state.authentication.providers.clear()
        self.assertEqual(self.call(self.token()).status_code, 200)
        prototype = {
            "tenant": "t" * 256,
            "actor": "a" * 256,
            "role": "inference",
            "key_sha256": "a" * 64,
        }
        count = (MAX_CONFIG_BYTES - 200) // (
            len(json.dumps(prototype, separators=(",", ":"))) + 1
        )
        entries = [
            {
                **prototype,
                "actor": f"{i:010d}" + "a" * 246,
                "key_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
            }
            for i in range(count)
        ]
        compact = json.dumps(entries, separators=(",", ":")).encode()
        self.assertLessEqual(len(compact), MAX_CONFIG_BYTES)
        self.assertGreater(
            len(json.dumps(entries, indent=2).encode()), MAX_CONFIG_BYTES
        )
        path = self.folder / "near-limit.json"
        path.write_bytes(compact)
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            credentials(path, "provision", "A", "new-actor", "inference")
        self.assertEqual(path.read_bytes(), compact)
        self.assertEqual(list(self.folder.glob(".dmt-keys-*")), [])

    def test_startup_configuration_rejects_unsafe_or_ambiguous_trust(self):
        provider = self.config["issuers"][0]
        for name, value in [
            ("jwks_url", "http://remote.invalid/keys"),
            ("algorithms", ["HS256"]),
            ("algorithms", [{}]),
            ("issuer", "https://user:password@issuer.invalid"),
            (
                "bindings",
                [{"subject": "x", "group": "g", "tenant": "A", "role": "admin"}],
            ),
        ]:
            original = provider.get(name)
            provider[name] = value
            self.write_sso()
            with self.subTest(setting=name):
                with self.assertRaises(AuthenticationUnavailable):
                    create_app(self.folder / "bad-startup")
            provider[name] = original
        self.write_sso()
        self.assertFalse((self.folder / "bad-startup").exists())

    def test_revocation_during_real_release_and_prediction_is_rechecked(self):
        project = self.project()
        service = self.app.state.service
        owner = {"tenant": "A", "actor": "local-owner", "role": "admin"}
        dataset = service.save_dataset(
            owner, project, fixtures.fixture_rows(), "fixture"
        )
        prepare = service.submit(
            owner, project, "prepare", {"datasets": [dataset["id"]]}, "auth-prepare"
        )
        Worker(service.store).once()
        snapshot = service.store.job(prepare["id"], "A", project)["result"]["snapshot"]
        service.configure_release_policy(owner, project, 0, 0, 1, 10, None)
        train = service.submit(
            owner,
            project,
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "auth-train",
        )
        Worker(service.store).once()
        model = service.store.job(train["id"], "A", project)["result"]["model"]
        original = service.checked_bundle

        def held_request(token, path, body, mutate):
            reached, released = threading.Event(), threading.Event()
            output = []

            def hold(value):
                bundle = original(value)
                reached.set()
                if not released.wait(10):
                    raise RuntimeError("Test release timeout")
                return bundle

            def send():
                output.append(self.call(token, "POST", path, json=body))

            with patch.object(service, "checked_bundle", side_effect=hold):
                thread = threading.Thread(target=send)
                thread.start()
                try:
                    self.assertTrue(reached.wait(10))
                    mutate()
                finally:
                    released.set()
                    thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(output), 1)
            self.assertEqual(output[0].status_code, 401, output[0].text)
            self.assertNotIn("answers", output[0].json())

        new_token = []
        held_request(
            self.local,
            f"/v1/projects/{project}/releases",
            {"model_id": model, "expected_revision": 0, "note": "Fictional auth fence"},
            lambda: new_token.append(
                credentials(self.key_file, "rotate", "A", "local-owner")["token"]
            ),
        )
        self.assertEqual(service.store.active("A", project)["revision"], 0)
        activated = self.call(
            new_token[0],
            "POST",
            f"/v1/projects/{project}/releases",
            json={
                "model_id": model,
                "expected_revision": 0,
                "note": "After explicit rotation",
            },
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        held_request(
            new_token[0],
            f"/v1/projects/{project}/decisions",
            {"state": "invoice payment refund requested normal", "threshold": 0.8},
            lambda: credentials(self.key_file, "revoke", "A", "local-owner"),
        )
        self.assertEqual(service.store.active("A", project)["revision"], 1)

        def downgrade():
            self.config["issuers"][0]["bindings"][0]["role"] = "reviewer"
            self.write_sso()

        held_request(
            self.token(),
            f"/v1/projects/{project}/releases",
            {
                "model_id": model,
                "expected_revision": 1,
                "note": "JWT permission change fixture",
            },
            downgrade,
        )
        self.assertEqual(service.store.active("A", project)["revision"], 1)

        def remap():
            self.config["issuers"][0]["bindings"][0]["tenant"] = "B"
            self.write_sso()

        held_request(
            self.token(),
            f"/v1/projects/{project}/decisions",
            {"state": "invoice payment refund requested normal", "threshold": 0.8},
            remap,
        )
        self.assertEqual(service.store.active("A", project)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
