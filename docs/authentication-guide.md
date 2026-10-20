# Credentials and enterprise access tokens

[All guides](README.md) · [Project overview](../README.md)

The API supports local workspace keys and an optional JWT access-token adapter. Both derive tenant, actor and role from operator-controlled configuration. The browser's credential field accepts either form. The current adapter does not implement an interactive OIDC authorization-code/PKCE login or refresh-token session.

The dependency profile pins PyJWT 2.15.1 and cryptography 50.0.2. Configure RS256 or ES256 public signing keys and the `at+jwt` or `application/at+jwt` access-token type. Fixed algorithms, issuer and audience validation help keep API access tokens separate from ID tokens. [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html), [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068.html), [PyJWT verification API](https://pyjwt.readthedocs.io/en/latest/api.html)

## Choose a workspace role

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
use the [managed provider controls](remote-training-guide.md) and observe its
terminal state. Key provisioning, rotation and revocation remain operator CLI
actions; an API `admin` token alone cannot administer the operator's key file.

## Workspace key lifecycle

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

## Configure a trusted enterprise issuer

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

## Mapping and revocation rules

An exact configured subject binding takes precedence over groups. Otherwise, signed group memberships must produce exactly one distinct tenant/role combination. Ambiguous or unmapped identities receive HTTP 403. Group actors use a stable hash of issuer and subject so two members do not share an audit identity. Tokens cannot choose their own tenant, platform role or audit actor through arbitrary claims.

To revoke one access token, atomically add its issuer and verified `jti` to `revoked_tokens`. Removing/changing a binding also affects the next request without API restart. Provider group changes in an already issued JWT are not introspected: they become visible when the provider issues a new token, unless the operator denies the token or changes the trusted binding. Refresh-token/session revocation remains with the identity provider; browser refresh handling is not implemented.

JWKS are cached for the configured interval (default 300 seconds). An unknown key ID can trigger a refresh, rate-limited to one such refresh per 30 seconds per configured key set. Removing a key at the issuer alone may leave a previously cached key usable until cache expiry. Use the local token/binding deny mechanism for an immediate platform restriction. Expired-cache retrieval failures return HTTP 503 and do not fall back to stale signing keys.

The API reauthenticates before committing a release and before returning a decision result. A credential revoked during model validation or inference can therefore deny the operation. Revocation does not undo committed changes or cancel queued/running jobs; inspect and cancel those jobs through an authorized principal. Identity-provider state, local configuration and SQLite do not form one distributed transaction.

## Containers and recovery

Compose mounts `data/config/` read-only at `/run/config/` and reads `/run/config/operator-keys.json`. Provision the host file before starting Compose. A directory mount exposes atomic host replacements; a bind mount tied to an old file inode may retain the old file. To enable JWT authentication, set `DMT_SSO_FILE=/run/config/sso.json` in a deployment override. Check permissions and secret-volume replacement behavior in your container environment.

Workspace backups exclude operator credentials and SSO configuration. Supply trusted current configuration separately after [workspace recovery](recovery-guide.md).
