# Sign in through your organization's identity provider

[All guides](README.md) · [API identities and roles](authentication-guide.md) · [Project overview](../README.md)

Enable an optional OIDC sign-in button in the Streamlit application. Users sign
in with their organization rather than copying a workspace key. The API still
verifies their **API access token** and assigns the tenant, actor and workspace
role from trusted mappings. A successful identity-provider login alone grants no
workspace permissions. Personal localhost mode remains available without OIDC.

## Prepare the identity provider and API

1. Configure the API's [trusted issuer, audience, signing keys, scopes and bindings](authentication-guide.md#configure-a-trusted-enterprise-issuer).
   The current API requires an RFC 9068 JWT access token with `at+jwt` type,
   RS256/ES256 signing and the documented claims. Opaque tokens, ID tokens and
   access tokens for another resource do not satisfy this contract.
2. Register a **confidential OIDC web client** for the browser application. Enable
   authorization-code flow with S256 PKCE. Register the exact callback, such as
   `https://workbench.example.com/oauth2callback`. Disable unused implicit grants.
3. Configure the client to obtain an access token for your workbench API's
   audience and required scopes, in addition to `openid`. Follow your issuer's
   resource/audience configuration. A generic Google/Microsoft login token is
   not automatically a compatible workbench API token.
4. Operate the API and UI behind HTTPS and restricted ingress. The UI needs a
   fixed trusted API URL and network access to the issuer's discovery, token and
   signing-key endpoints. Supply provider credentials only to their API/worker
   services, not to the UI service.

The client uses Streamlit's OIDC implementation; its state and nonce handling
are retained. S256 is also required in the workbench configuration below.
[Streamlit login configuration](https://docs.streamlit.io/1.65.0/develop/api-reference/user/st.login),
[OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html),
[PKCE](https://www.rfc-editor.org/rfc/rfc7636.html).

## Configure the UI server

Install the normal application requirements, then the optional client profile:

```bash
python -m pip install -r requirements.txt -r requirements/enterprise-ui.txt
python -m pip check
```

This profile retains Streamlit 1.65.0 and pins Authlib 1.9.1/Joserfc 1.7.7. It
contains no model runtime. See [Authlib releases](https://github.com/authlib/authlib/releases/tag/v1.9.1).

Copy [the configuration template](../examples/platform/streamlit-secrets.toml.example)
to the UI server's private `.streamlit/secrets.toml` in its working directory.
Replace the client credentials, discovery URL and callback. Generate a random
cookie secret, for example with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Keep this value and the client secret in your deployment's secret store or
restricted mounted file; the generated value is sensitive terminal output.
Use the same cookie secret and configuration on UI replicas, with session-aware
WebSocket routing.

Expose only `access` in the shared `[auth]` section. In `[auth.enterprise]`, retain
`code_challenge_method = "S256"` and request `openid` plus your actual API scopes.
The template uses `prompt = "login"`; adjust it only according to the issuer's
documented sign-in policy. Do not replace state, nonce or code verifier with
static configuration. [Streamlit token access](https://docs.streamlit.io/1.65.0/develop/api-reference/user/st.user).

Set these nonsecret values in the UI service environment:

```bash
export DMT_UI_AUTH_MODE=oidc
export DMT_UI_OIDC_PROVIDER=enterprise
export DMT_API_URL=https://api.example.com
streamlit run app.py --server.address 127.0.0.1 --server.headless true
```

In PowerShell, use `$env:DMT_UI_AUTH_MODE='oidc'` and the same syntax for the other
variables. The command above expects a local reverse proxy forwarding the public
HTTPS application address to Streamlit. Container ingress may require a private
container listener instead. The fixed API URL accepts HTTPS, with HTTP reserved
for loopback development; users cannot change it from the sign-in screen.

The UI's provider name must match the named secrets section and cannot contain
underscores. `workspace`, the default authentication mode, retains the ordinary
personal/shared workspace connection screen. A malformed `oidc` configuration
fails closed instead of opening that fallback screen.

## Sign in, change accounts and end a session

Open the workbench and select **Sign in with your organization**. After the
redirect, the UI sends the access token to the fixed API's `/v1/me` endpoint.
The workspace sidebar shows the API-derived identity and role. The UI repeats
this identity check on each render; every API operation independently verifies
the token. An account, tenant, role or API change clears the old workspace's
drafts and cached browser-session data before rendering the new workspace.

Select **Sign out** to clear workspace session state and use Streamlit's logout.
An expired/revoked/unmapped token or unavailable API stops workspace rendering
and offers **Sign out and choose an account**. The application does not refresh
access tokens automatically; sign in again when they expire. Browser login does
not extend the API token's validity.

Streamlit uses signed, HttpOnly, SameSite cookies; its identity cookie can outlive
the API token. Tokens are sensitive even when their cookie is signed. Require
HTTPS, prevent direct public HTTP access, and enforce the `Secure` attribute for
authentication/session cookies at your TLS ingress. Do not expose cookie
contents, `st.user.tokens`, client secrets or bearer headers in UI/logs. Logout
does not guarantee termination of other open tabs or issuer sessions; use API
revocation when immediate denial is required.
[Streamlit session behavior](https://docs.streamlit.io/develop/concepts/connections/authentication).

## Revoke or rotate access

Local workspace keys retain their [provision/rotate/revoke commands](authentication-guide.md#workspace-key-lifecycle).
To deny a particular issued access token, use its exact configured issuer and
verified `jti` identifier from your issuer/operator records, without supplying the
bearer token itself:

```bash
domain-platform revoke-access-token --sso-file /operator/sso.json --issuer https://identity.example.com/realms/enterprise --jti YOUR_TOKEN_IDENTIFIER
```

This operator command validates the configuration, serializes concurrent writers
and publishes an atomic replacement. It is idempotent and stores no bearer token.
Unknown issuers and oversized configuration updates are refused. The next API
request reads the deny entry; the user must obtain a different authorized token
before reconnecting. Keep the same live configuration on API replicas, using a
secret/configuration publisher that exposes atomic replacements. This command
updates one file, not a distributed identity-provider transaction.

Run the command under an operator identity permitted to publish that file.
Replacement files use restricted temporary-file permissions; ensure the API's
service identity can read the published configuration. For mounted enterprise
secrets, use your approved configuration publisher and its access-control policy.
Windows ACLs and secret-volume replacement semantics need deployment checks.

Remove or change trusted subject/group bindings to deny or change a principal's
workspace access. Publish a complete validated file atomically rather than
editing the live file in place. Provider group changes are visible in newly
issued tokens; the API does not introspect already-issued JWTs. Revocation does
not cancel previously queued jobs or undo committed changes.

Rotate the OIDC client secret through the identity provider and secret manager,
then restart UI replicas with the matching value. Rotating the cookie secret
invalidates existing cookies; coordinate it across replicas and require users to
sign in again. Rotate hosted-provider credentials in their API/worker deployment,
never through the UI OIDC file. Drain or reconcile in-flight provider calls before
retries; a credential change does not prove a timed-out call was not billed.

## Check your deployment

Test each workspace role, an unmapped user, a different tenant, expired/revoked
tokens, account switching and logout. Check the actual callback, issuer audience,
MFA policy, ingress cookie flags and secret rotation on your own deployment.
For local implementation checks, run:

```bash
python -m unittest discover -s tests -p test_enterprise_auth.py -v
python -m unittest discover -s tests -p test_authentication.py -v
```

These use fictional loopback issuers and real asymmetric signatures. They cover
the Streamlit/Authlib callback exchange and UI/API authorization mechanics, not
an organization's identity-provider account, MFA policy or deployed ingress.
