"""OIDC UI authorization plus real Streamlit/Authlib loopback code exchange."""

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import threading
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx
import jwt
import streamlit as st
from streamlit.testing.v1 import AppTest
from fastapi.testclient import TestClient

from decision_workbench.ui.enterprise_auth import settings
from http_server_fixture import LiveServer
import test_authentication as auth_fixtures

ROOT = Path(__file__).resolve().parents[1]
AUTHLIB = importlib.util.find_spec("authlib") is not None


def configuration(issuer="https://identity.example.com"):
    return {
        "redirect_uri": "http://127.0.0.1:18501/oauth2callback",
        "cookie_secret": "fictional-cookie-key-" + "a" * 48,
        "expose_tokens": ["access"],
        "enterprise": {
            "client_id": "fixture-web-client",
            "client_secret": "fictional-client-secret",
            "server_metadata_url": issuer + "/discovery",
            "client_kwargs": {
                "scope": "openid decisions.read",
                "code_challenge_method": "S256",
            },
        },
    }


class EnterpriseConfigurationTests(unittest.TestCase):
    def test_fixed_api_target_pkce_and_access_only_configuration(self):
        with patch.dict(
            os.environ, {"DMT_API_URL": "https://workspace.example.com/api"}
        ):
            self.assertEqual(
                settings(configuration()),
                ("https://workspace.example.com/api", "enterprise"),
            )
            for changes in (
                {"expose_tokens": ["id", "access"]},
                {"cookie_secret": "short"},
                {"redirect_uri": "http://remote.invalid/oauth2callback"},
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    settings({**configuration(), **changes})
            for key, value in (
                ("code_challenge_method", "plain"),
                ("scope", "profile"),
                ("nonce", "static"),
            ):
                config = configuration()
                config["enterprise"]["client_kwargs"][key] = value
                with self.assertRaises(ValueError):
                    settings(config)
        for target in (
            "http://remote.invalid",
            "https://user:secret@api.invalid",
            "https://api.invalid/?token=secret",
        ):
            with patch.dict(os.environ, {"DMT_API_URL": target}), self.assertRaises(
                ValueError
            ):
                settings(configuration())


@unittest.skipUnless(
    AUTHLIB, "Install requirements/enterprise-ui.txt for OIDC UI checks"
)
class EnterpriseBrowserTests(unittest.TestCase):
    def setUp(self):
        self.f = auth_fixtures.AuthenticationTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.server = LiveServer(self.f.folder / "browser-api", None)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        for target, name, value in (
            (st, "secrets", {"auth": configuration()}),
            (st, "user", SimpleNamespace(is_logged_in=False, tokens={})),
        ):
            binding = patch.object(target, name, value)
            binding.start()
            self.addCleanup(binding.stop)
        env = patch.dict(
            os.environ, {"DMT_UI_AUTH_MODE": "oidc", "DMT_API_URL": self.server.url}
        )
        env.start()
        self.addCleanup(env.stop)

    def app(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        return app

    def login(self, token):
        st.user = SimpleNamespace(is_logged_in=True, tokens={"access": token})

    def test_entry_uses_native_login_without_local_or_key_fallback(self):
        app = self.app()
        self.assertFalse(app.text_input)
        self.assertFalse(any(b.label == "Start local workspace" for b in app.button))
        with patch.object(st, "login") as login:
            next(
                b for b in app.button if b.label == "Sign in with your organization"
            ).click().run()
        login.assert_called_once_with("enterprise")

    def test_real_signed_access_token_roles_revocation_and_account_switch(self):
        token = self.f.token()
        self.login(token)
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                self.server.url + "/v1/projects",
                headers={"Authorization": "Bearer " + token},
                json={
                    "name": "Fictional protected project",
                    "description": "A bounded fictional domain",
                    "questions": auth_fixtures.fixtures.QUESTIONS,
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        app = self.app()
        self.assertEqual(app.session_state["principal"]["actor"], "employee-alice")
        self.assertEqual(len(app.tabs), 6)
        app.session_state["api_url"] = "https://untrusted.invalid"
        app.run()
        self.assertEqual(app.session_state["api_url"], self.server.url)
        app.session_state["private-old-account-data"] = b"fictional prior-tenant data"
        self.login(self.f.token("reader"))
        app.run()
        self.assertEqual(app.session_state["principal"]["role"], "inference")
        self.assertNotIn("private-old-account-data", app.session_state)
        self.assertNotIn(token, str([e.value for e in app.markdown]))
        self.f.config["issuers"][0]["bindings"] = []
        self.f.write_sso()
        app.run()
        self.assertNotIn("credential", app.session_state)
        self.assertFalse(app.tabs)
        self.assertTrue(app.error)

    def test_expired_missing_and_id_tokens_are_refused_without_rendering_credentials(
        self,
    ):
        for token in (
            None,
            self.f.token(exp=int(time.time()) - 1),
            self.f.token(header={"typ": "JWT"}),
        ):
            with self.subTest(token_type=type(token).__name__):
                self.login(token)
                app = self.app()
                self.assertTrue(app.error)
                self.assertNotIn("principal", app.session_state)
                if token:
                    self.assertNotIn(token, str([e.value for e in app.error]))

    def test_signout_clears_workspace_and_calls_native_logout(self):
        self.login(self.f.token())
        app = self.app()
        app.session_state["private-draft"] = "fictional"
        with patch.object(st, "logout", side_effect=st.stop) as logout:
            next(b for b in app.button if b.label == "Sign out").click().run()
        logout.assert_called_once()
        self.assertFalse(app.exception)
        self.assertNotIn("private-draft", app.session_state)
        self.assertNotIn("credential", app.session_state)

    def test_misconfigured_mode_or_secrets_fail_closed(self):
        self.login(self.f.token())
        st.secrets = {"auth": {}}
        app = self.app()
        self.assertTrue(app.error)
        self.assertFalse(app.tabs)
        self.assertFalse(app.text_input)


@unittest.skipUnless(
    AUTHLIB, "Install requirements/enterprise-ui.txt for native OIDC exchange"
)
class NativeOIDCFlowTests(unittest.TestCase):
    def setUp(self):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.sessions import SessionMiddleware
        from streamlit.runtime.secrets import AttrDict
        from streamlit import auth_util
        from streamlit.web.server.starlette import starlette_auth_routes as routes
        from streamlit.web.server.starlette.starlette_server_config import (
            TOKENS_COOKIE_NAME,
        )

        self.routes, self.cookie_name = routes, TOKENS_COOKIE_NAME
        self.f = auth_fixtures.AuthenticationTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.codes, self.exchanges, self.bad_nonce = {}, 0, False
        owner = self

        class Identity(BaseHTTPRequestHandler):
            def send(self, body, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                path = urlsplit(self.path)
                if path.path == "/discovery":
                    self.send(
                        {
                            "issuer": owner.issuer,
                            "authorization_endpoint": owner.issuer + "/authorize",
                            "token_endpoint": owner.issuer + "/token",
                            "jwks_uri": owner.issuer + "/keys",
                            "response_types_supported": ["code"],
                            "subject_types_supported": ["public"],
                            "id_token_signing_alg_values_supported": ["RS256"],
                            "code_challenge_methods_supported": ["S256"],
                        }
                    )
                elif path.path == "/keys":
                    self.send(owner.f.document)
                elif path.path == "/authorize":
                    query = {k: v[0] for k, v in parse_qs(path.query).items()}
                    code = uuid.uuid4().hex
                    owner.codes[code] = query
                    self.send_response(302)
                    self.send_header(
                        "Location",
                        query["redirect_uri"]
                        + "?"
                        + urlencode({"code": code, "state": query["state"]}),
                    )
                    self.end_headers()
                else:
                    self.send({}, 404)

            def do_POST(self):
                form = {
                    k: v[0]
                    for k, v in parse_qs(
                        self.rfile.read(int(self.headers["Content-Length"])).decode()
                    ).items()
                }
                authorization = (
                    "Basic "
                    + base64.b64encode(
                        b"fixture-web-client:fictional-client-secret"
                    ).decode()
                )
                record = owner.codes.pop(form.get("code"), None)
                owner.exchanges += 1
                verifier = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                    )
                    .rstrip(b"=")
                    .decode()
                )
                if (
                    not record
                    or self.headers.get("Authorization") != authorization
                    or verifier != record["code_challenge"]
                    or form.get("redirect_uri") != record["redirect_uri"]
                ):
                    self.send({"error": "invalid_grant"}, 400)
                    return
                now = int(time.time())
                identity = jwt.encode(
                    {
                        "iss": owner.issuer,
                        "sub": "alice",
                        "aud": "fixture-web-client",
                        "iat": now,
                        "exp": now + 300,
                        "nonce": "wrong" if owner.bad_nonce else record["nonce"],
                    },
                    owner.f.private,
                    algorithm="RS256",
                    headers={"kid": "first"},
                )
                self.send(
                    {
                        "token_type": "Bearer",
                        "access_token": owner.f.token(),
                        "expires_in": 300,
                        "id_token": identity,
                    }
                )

            def log_message(self, *args):
                pass

        self.identity = ThreadingHTTPServer(("127.0.0.1", 0), Identity)
        self.issuer = f"http://127.0.0.1:{self.identity.server_port}"
        thread = threading.Thread(target=self.identity.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(
            lambda: (
                self.identity.shutdown(),
                self.identity.server_close(),
                thread.join(timeout=5),
            )
        )
        self.f.issuer = self.issuer
        self.f.config["issuers"][0].update(
            issuer=self.issuer, jwks_url=self.issuer + "/keys"
        )
        self.f.write_sso()
        self.auth = configuration(self.issuer)
        for target, name, value in (
            (auth_util, "get_secrets_auth_section", lambda: AttrDict(self.auth)),
            (routes, "get_secrets_auth_section", lambda: AttrDict(self.auth)),
            (routes, "get_cookie_secret", lambda: self.auth["cookie_secret"]),
            (auth_util, "get_signing_secret", lambda: self.auth["cookie_secret"]),
        ):
            binding = patch.object(target, name, value)
            binding.start()
            self.addCleanup(binding.stop)
        application = Starlette(
            routes=routes.create_auth_routes(""),
            middleware=[
                Middleware(
                    SessionMiddleware,
                    secret_key=self.auth["cookie_secret"],
                    same_site="lax",
                )
            ],
        )
        self.client = TestClient(
            application, base_url="http://127.0.0.1:18501", follow_redirects=False
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def authorization(self):
        from streamlit.auth_util import encode_provider_token

        response = self.client.get(
            "/auth/login", params={"provider": encode_provider_token("enterprise")}
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["state"] and query["nonce"])
        with httpx.Client(trust_env=False) as client:
            redirect = client.get(response.headers["location"])
        return redirect.headers["location"]

    def test_actual_code_pkce_nonce_cookie_api_and_replay(self):
        from streamlit.web.server.starlette.starlette_app_utils import (
            decode_signed_value,
        )

        callback = self.authorization()
        response = self.client.get(callback)
        self.assertEqual(response.status_code, 302)
        cookie = self.client.cookies.get(self.cookie_name)
        self.assertIsNotNone(cookie)
        token = json.loads(
            decode_signed_value(
                self.auth["cookie_secret"], self.cookie_name, cookie.encode()
            )
        )["access_token"]
        self.assertEqual(self.f.call(token).status_code, 200)
        self.assertTrue(
            any(
                "httponly" in h.lower() and "samesite=lax" in h.lower()
                for h in response.headers.get_list("set-cookie")
            )
        )
        self.client.get(callback)
        self.assertEqual(self.exchanges, 1)
        logout = self.client.get("/auth/logout")
        self.assertEqual(logout.status_code, 302)
        self.assertIsNone(self.client.cookies.get(self.cookie_name))

    def test_wrong_state_and_nonce_do_not_create_authentication(self):
        callback = self.authorization()
        self.client.get(callback.replace("state=", "state=wrong"))
        self.assertIsNone(self.client.cookies.get(self.cookie_name))
        self.assertEqual(self.exchanges, 0)
        self.bad_nonce = True
        self.client.get(self.authorization())
        self.assertIsNone(self.client.cookies.get(self.cookie_name))


class EnterpriseTokenLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = auth_fixtures.AuthenticationTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_actual_cli_revokes_next_request_without_affecting_other_tokens(self):
        token = self.f.token(jti="fictional-revoke-001")
        self.assertEqual(self.f.call(token).status_code, 200)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.cli",
                "revoke-access-token",
                "--sso-file",
                str(self.f.sso_file),
                "--issuer",
                self.f.issuer,
                "--jti",
                "fictional-revoke-001",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["revoked"])
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertEqual(self.f.call(token).status_code, 401)
        self.assertEqual(self.f.call(self.f.token()).status_code, 200)

    def test_concurrent_revocations_are_preserved_and_repetition_is_idempotent(self):
        from decision_workbench.platform.sso_credentials import revoke_access_token

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda i: revoke_access_token(
                        self.f.sso_file, self.f.issuer, "fictional-" + str(i)
                    ),
                    range(8),
                )
            )
        before = self.f.sso_file.read_bytes()
        self.assertEqual(len(json.loads(before)["revoked_tokens"]), 8)
        revoke_access_token(self.f.sso_file, self.f.issuer, "fictional-0")
        self.assertEqual(self.f.sso_file.read_bytes(), before)

    def test_unknown_issuer_oversize_and_publication_failure_leave_config_unchanged(
        self,
    ):
        from decision_workbench.platform.sso_credentials import revoke_access_token

        before = self.f.sso_file.read_bytes()
        with self.assertRaises(ValueError):
            revoke_access_token(
                self.f.sso_file, "https://untrusted.invalid", "fictional"
            )
        with patch(
            "decision_workbench.platform.sso_credentials.MAX_CONFIG_BYTES", 20
        ), self.assertRaises(ValueError):
            revoke_access_token(self.f.sso_file, self.f.issuer, "fictional")
        with patch(
            "decision_workbench.platform.sso_credentials.publish",
            side_effect=OSError("Fictional interrupted publication"),
        ), self.assertRaises(OSError):
            revoke_access_token(self.f.sso_file, self.f.issuer, "fictional")
        self.assertEqual(self.f.sso_file.read_bytes(), before)
        self.assertEqual(list(self.f.folder.glob(".dmt-sso-*")), [])


if __name__ == "__main__":
    unittest.main()
