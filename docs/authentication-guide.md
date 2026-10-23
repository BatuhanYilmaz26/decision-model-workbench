# Configure credentials and organization sign-in

<a id="credentials-and-enterprise-access-tokens"></a>

[Documentation](README.md) · [Project overview](../README.md)

Personal localhost startup connects automatically. For shared workspaces and integration clients, configure scoped workspace credentials or an approved enterprise identity flow. Provider API keys are separate from workspace authentication.

## Choose a section

- [Workspace keys and API tokens](#workspace-credentials)

- [Organization OIDC sign-in](#organization-sign-in)

<a id="workspace-credentials"></a>

## Workspace keys and API tokens

<a id="workspace-credentials--credentials-and-enterprise-access-tokens"></a>

The API supports local workspace keys and an optional JWT access-token adapter. Both derive tenant, actor and role from operator-controlled configuration. The browser's credential field accepts either form. For an organization sign-in button, configure the optional [OIDC browser workflow](authentication-guide.md#organization-sign-in); it uses authorization-code/S256 PKCE and obtains an API access token. Automatic access-token refresh is not implemented.

The dependency profile pins PyJWT 2.15.1 and cryptography 50.0.2. Configure RS256 or ES256 public signing keys and the `at+jwt` or `application/at+jwt` access-token type. Fixed algorithms, issuer and audience validation help keep API access tokens separate from ID tokens. [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html), [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068.html), [PyJWT verification API](https://pyjwt.readthedocs.io/en/latest/api.html)

<a id="workspace-credentials--choose-a-workspace-role"></a>

### Choose a workspace role

Assign the role to the principal when provisioning a key or configuring a trusted
access-token binding. The API enforces these permissions; hiding a browser control
does not grant or remove access. Personal localhost mode uses an `admin` identity.

| Action | Owner (`admin`) | Editor (`editor`) | Reviewer (`reviewer`) | Application (`inference`) |
|---|---|---|---|---|
| Read project questions and active release; request predictions | Yes | Yes | Yes | Yes |
| Read examples, dataset/model reports, reserved exports and job status | Yes | Yes | Yes | No |
| Create an independent project; import or enter examples | Yes | Yes | No | No |
| Approve, correct or reject example labels | Yes | No | Yes | No |
| Reserve data; queue supported local training | Yes | Yes | No | No |
| Cancel a local job | Yes | Yes | No | No |
| Set acceptance criteria; activate or roll back a release | Yes | No | No | No |
| Approve teacher requests, hosted evaluation or cloud training | Yes | No | No | No |
| Inspect configured teachers, hardware or cloud profiles | Yes | No | No | No |
| Inspect operational incidents | Yes | Yes | No | No |
| Assign review work; export audit history; inspect/reconcile provider calls | Yes | No | No | No |

An editor can supply examples and train a model without approving its labels or
releasing it. A reviewer can inspect and annotate data without starting training.
Use `inference` for an application that only needs project metadata and released
predictions; it cannot download training data or read job/audit history. Existing
record assignments, frozen criteria and model/backend prerequisites still apply.

Roles apply within the principal's tenant. A role never grants access to another
tenant's resources. Identity comes from the configured key or verified token
binding; request headers and body fields cannot select a different tenant or
role. Local job cancellation does not stop upstream cloud compute: an owner must
use the [managed provider controls](remote-training-guide.md#study-protocol) and observe its
terminal state. Key provisioning, rotation and revocation remain operator CLI
actions; an API `admin` token alone cannot administer the operator's key file.

<a id="workspace-credentials--workspace-key-lifecycle"></a>

### Workspace key lifecycle

Provision a principal with an explicit role:

```bash
domain-platform provision-key --file data/operator-keys.json --tenant acme --actor platform-owner --role admin
```

The bearer token is printed once. The operator file stores its SHA-256 hash and trusted identity, rather than the plaintext token. Supply the token through the browser's masked field or an `Authorization: Bearer ...` header. Keep console output containing newly issued tokens out of shared logs.

```bash
domain-platform list-keys --file data/operator-keys.json
domain-platform rotate-key --file data/operator-keys.json --tenant acme --actor platform-owner
domain-platform rotate-key --file data/operator-keys.json --tenant acme --actor annotator --role reviewer
domain-platform revoke-key --file data/operator-keys.json --tenant acme --actor annotator
```

Listing shows principal fields and a short fingerprint. Rotation replaces the selected principal's hash immediately; its old token cannot authenticate the next request. Without `--role`, rotation preserves the previous role. Revocation removes every matching key for that tenant/actor. The file can become an empty list, which denies all local keys while leaving a configured JWT adapter available.

API authentication rereads and validates its bounded configuration on every request. Operator commands serialize across processes with an OS file lock and publish a same-directory atomic replacement. They refuse an update larger than the API's 1 MiB limit before changing the file. Malformed, missing or oversized configured files fail closed with HTTP 503; the API does not retain stale permissions as a fallback. `/ready` also checks live authentication configuration; it does not probe the remote issuer or establish complete worker/GPU readiness. Never edit the live file in place while users are requesting it; use these commands or an approved atomic configuration publisher.

POSIX temporary-file creation uses restricted modes. Windows access controls and enterprise secret storage remain the operator's responsibility; this command does not install or verify enterprise ACLs. Operator credential changes are not yet exported to an enterprise identity audit system.

<a id="workspace-credentials--configure-a-trusted-enterprise-issuer"></a>

### Configure a trusted enterprise issuer

Configure your authorization server to issue **API access tokens** for a dedicated resource audience and the required API scopes. A token intended for another application or an ID token is refused. The issuer must publish public JWKS with unique key IDs. Opaque tokens and provider token formats that do not satisfy this access-token profile need a separately qualified adapter.

Create a private operator file, for example `data/config/sso.json`:

```json
{
  "issuers": [
    {
      "issuer": "https://identity.example.com/realms/enterprise",
      "audience": "urn:domain-platform-api",
      "jwks_url": "https://identity.example.com/realms/enterprise/protocol/openid-connect/certs",
      "algorithms": ["RS256"],
      "required_scopes": ["decisions.read"],
      "max_token_seconds": 3600,
      "jwks_cache_seconds": 300,
      "bindings": [
        {
          "subject": "REPLACE_WITH_IMMUTABLE_PROVIDER_SUBJECT",
          "tenant": "acme",
          "actor": "platform-owner",
          "role": "admin"
        },
        {
          "group": "decision-annotators",
          "tenant": "acme",
          "role": "reviewer"
        }
      ]
    }
  ],
  "revoked_tokens": []
}
```

The URL and identifiers above are format examples. Configure the actual issuer/audience/JWKS from the enterprise's approved identity service. Set `DMT_SSO_FILE` before API startup; for example, in PowerShell:

```powershell
$env:DMT_SSO_FILE=(Resolve-Path 'data/config/sso.json').Path
domain-platform run --home data/workspace --keys-file data/operator-keys.json
```

For SSO-only operation, use an existing empty local-key file with the launcher, or unset `DMT_KEYS_FILE` when starting the API individually. API startup requires at least one configured authentication source. Existing explicitly provisioned local keys remain usable when both sources are configured.

The adapter validates the cryptographic signature, configured issuer/audience, expiry and issued-at times, optional not-before, a bounded token lifetime and required scopes. It requires the profile's `iss`, `sub`, `aud`, `exp`, `iat`, `jti` and `client_id` claims. Time claims must be finite numeric values; clocks must be synchronized because no clock leeway is configured. The maximum token size is 16 KiB.

Only a configured issuer selects a configured JWKS URL. Token-supplied `jku`, embedded keys/certificates, remote key references and unsupported critical headers are refused. Signing-key responses are limited to 1 MiB and 100 entries; redirects, private/symmetric keys, ambiguous key IDs and incompatible key/algorithm pairs are refused. Remote issuer/JWKS URLs require HTTPS; loopback HTTP supports local acceptance fixtures.

<a id="workspace-credentials--mapping-and-revocation-rules"></a>

### Mapping and revocation rules

An exact configured subject binding takes precedence over groups. Otherwise, signed group memberships must produce exactly one distinct tenant/role combination. Ambiguous or unmapped identities receive HTTP 403. Group actors use a stable hash of issuer and subject so two members do not share an audit identity. Tokens cannot choose their own tenant, platform role or audit actor through arbitrary claims.

To revoke one access token, use `domain-platform revoke-access-token --sso-file /operator/sso.json --issuer YOUR_CONFIGURED_ISSUER --jti YOUR_TOKEN_IDENTIFIER`. The command atomically adds its issuer and verified `jti` to `revoked_tokens` without storing the bearer token. Removing/changing a binding also affects the next request without API restart. Provider group changes in an already issued JWT are not introspected: they become visible when the provider issues a new token, unless the operator denies the token or changes the trusted binding. Refresh-token/session revocation remains with the identity provider; the [browser workflow](authentication-guide.md#organization-sign-in--sign-in-change-accounts-and-end-a-session) asks users to sign in again when API access expires.

JWKS are cached for the configured interval (default 300 seconds). An unknown key ID can trigger a refresh, rate-limited to one such refresh per 30 seconds per configured key set. Removing a key at the issuer alone may leave a previously cached key usable until cache expiry. Use the local token/binding deny mechanism for an immediate platform restriction. Expired-cache retrieval failures return HTTP 503 and do not fall back to stale signing keys.

The API reauthenticates before committing a release and before returning a decision result. A credential revoked during model validation or inference can therefore deny the operation. Revocation does not undo committed changes or cancel queued/running jobs; inspect and cancel those jobs through an authorized principal. Identity-provider state, local configuration and SQLite do not form one distributed transaction.

<a id="workspace-credentials--containers-and-recovery"></a>

### Containers and recovery

Compose mounts `data/config/` read-only at `/run/config/` and reads `/run/config/operator-keys.json`. Provision the host file before starting Compose. A directory mount exposes atomic host replacements; a bind mount tied to an old file inode may retain the old file. To enable JWT authentication, set `DMT_SSO_FILE=/run/config/sso.json` in a deployment override. Check permissions and secret-volume replacement behavior in your container environment.

Workspace backups exclude operator credentials and SSO configuration. Supply trusted current configuration separately after [workspace recovery](recovery-guide.md#local-backups).

<a id="organization-sign-in"></a>

## Organization OIDC sign-in

<a id="organization-sign-in--sign-in-through-your-organizations-identity-provider"></a>

Enable an optional OIDC sign-in button in the Streamlit application. Users sign
in with their organization rather than copying a workspace key. The API still
verifies their **API access token** and assigns the tenant, actor and workspace
role from trusted mappings. A successful identity-provider login alone grants no
workspace permissions. Personal localhost mode remains available without OIDC.

<a id="organization-sign-in--prepare-the-identity-provider-and-api"></a>

### Prepare the identity provider and API

1. Configure the API's [trusted issuer, audience, signing keys, scopes and bindings](authentication-guide.md#workspace-credentials--configure-a-trusted-enterprise-issuer).
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

<a id="organization-sign-in--configure-the-ui-server"></a>

### Configure the UI server

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

<a id="organization-sign-in--sign-in-change-accounts-and-end-a-session"></a>

### Sign in, change accounts and end a session

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

<a id="organization-sign-in--revoke-or-rotate-access"></a>

### Revoke or rotate access

Local workspace keys retain their [provision/rotate/revoke commands](authentication-guide.md#workspace-credentials--workspace-key-lifecycle).
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

<a id="organization-sign-in--check-your-deployment"></a>

### Check your deployment

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
