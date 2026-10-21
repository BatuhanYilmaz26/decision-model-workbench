"""Optional Streamlit OIDC access-token connection to a fixed workspace API."""

from importlib.metadata import PackageNotFoundError, version
import os
import re

import httpx
import streamlit as st

from decision_workbench.platform.auth import endpoint, ROLES, MAX_TOKEN_BYTES


def authentication_mode():
    value = os.environ.get("DMT_UI_AUTH_MODE", "workspace")
    if value not in {"workspace", "oidc"}:
        raise ValueError("Set DMT_UI_AUTH_MODE to workspace or oidc")
    return value


def settings(auth):
    """Validate operator configuration; no browser field chooses a token target."""
    url = endpoint(os.environ.get("DMT_API_URL"), issuer=True).rstrip("/")
    provider = os.environ.get("DMT_UI_OIDC_PROVIDER", "enterprise")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", provider):
        raise ValueError("Use a named OIDC provider without underscores")
    redirect = endpoint(auth.get("redirect_uri"), issuer=True)
    if not redirect.endswith("/oauth2callback"):
        raise ValueError("OIDC redirect URI must end with /oauth2callback")
    secret = auth.get("cookie_secret")
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError(
            "Use a randomly generated cookie secret of at least 32 characters"
        )
    if auth.get("expose_tokens") not in ("access", ["access"]):
        raise ValueError("Expose only the OIDC access token")
    selected = auth.get(provider)
    if not selected or any(
        not isinstance(selected.get(k), str) or not selected[k]
        for k in ("client_id", "client_secret", "server_metadata_url")
    ):
        raise ValueError("Configure the named OIDC client")
    endpoint(selected["server_metadata_url"], issuer=True)
    options = selected.get("client_kwargs", {})
    if (
        options.get("code_challenge_method") != "S256"
        or "openid" not in options.get("scope", "").split()
        or any(
            k in options
            for k in (
                "state",
                "nonce",
                "code_verifier",
                "redirect_uri",
                "response_type",
            )
        )
    ):
        raise ValueError(
            "Use the OIDC code flow with S256 PKCE and an openid/API scope"
        )
    return url, provider


def require_client():
    try:
        if tuple(int(x) for x in version("streamlit").split(".")[:2]) < (1, 65):
            raise ValueError("Install the enterprise UI dependency profile")
        version("Authlib")
    except PackageNotFoundError:
        raise ValueError("Install requirements/enterprise-ui.txt") from None


def clear_workspace():
    # Drafts, downloaded data and cached identity cannot survive account changes.
    st.session_state.clear()


def sign_out():
    oidc = authentication_mode() == "oidc"
    clear_workspace()
    if oidc:
        st.logout()
    else:
        st.session_state["manual_connection"] = True
        st.rerun()


def connect_access(url, token):
    if not isinstance(token, str) or not 1 <= len(token.encode()) <= MAX_TOKEN_BYTES:
        raise ValueError(
            "The identity provider did not return a usable API access token"
        )
    # Do not reuse an ambient proxy or follow a redirect carrying a bearer token.
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.get(
            url + "/v1/me", headers={"Authorization": "Bearer " + token}
        )
    if response.status_code != 200:
        raise ValueError("The workspace did not accept the current API access token")
    principal = response.json()
    if (
        not isinstance(principal, dict)
        or set(principal) != {"tenant", "actor", "role"}
        or any(
            not isinstance(principal[k], str) or not 1 <= len(principal[k]) <= 256
            for k in principal
        )
        or principal["role"] not in ROLES
    ):
        raise ValueError("The workspace returned an invalid identity")
    if (
        st.session_state.get("principal") != principal
        or st.session_state.get("api_url") != url
        or st.session_state.get("connection_mode") != "oidc"
    ):
        clear_workspace()
    st.session_state.update(
        credential=token, api_url=url, principal=principal, connection_mode="oidc"
    )


def render_sign_in():
    """Validate the live API identity on every render, including existing sessions."""
    try:
        require_client()
        url, provider = settings(st.secrets["auth"])
    except Exception:
        clear_workspace()
        st.error(
            "Enterprise sign-in is not configured. Ask your administrator to check the enterprise sign-in guide and server configuration."
        )
        st.stop()
    if not st.user.is_logged_in:
        clear_workspace()
        st.subheader("Sign in to your workspace")
        st.write(
            "Use your organization's identity provider. Your workspace role is assigned by the API; no workspace key is needed here."
        )
        if st.button("Sign in with your organization", type="primary"):
            st.login(provider)
        st.stop()
    try:
        connect_access(url, st.user.tokens.get("access"))
    except (ValueError, KeyError, httpx.HTTPError):
        clear_workspace()
        st.error(
            "Your workspace session is unavailable or no longer authorized. Sign out and sign in again; ask your administrator if access is still refused."
        )
        st.caption(
            "An identity-provider login does not grant a workspace role. Access-token expiry, revocation and role mappings are checked by the API."
        )
        if st.button("Sign out and choose an account"):
            sign_out()
        st.stop()
