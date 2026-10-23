"""Live operator credentials and bounded RFC 9068 JWT access-token verification."""

import copy
import hashlib
import hmac
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROLES = {"admin", "editor", "reviewer", "inference"}
MAX_CONFIG_BYTES = 1024 * 1024
MAX_TOKEN_BYTES = 16384


class AuthenticationError(Exception):
    pass


class AuthenticationUnavailable(Exception):
    pass


class UnmappedIdentity(Exception):
    pass


def read_config(path):
    try:
        with Path(path).open("rb") as stream:
            body = stream.read(MAX_CONFIG_BYTES + 1)
        if len(body) > MAX_CONFIG_BYTES:
            raise ValueError("Authentication configuration exceeds 1 MiB")
        return json.loads(body.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AuthenticationUnavailable(
            "Operator authentication configuration is unavailable"
        ) from exc


def principal(value):
    if (
        not isinstance(value, dict)
        or not all(
            isinstance(value.get(field), str) and 0 < len(value[field]) <= 256
            for field in ("tenant", "actor", "role")
        )
        or value["role"] not in ROLES
    ):
        raise ValueError("Invalid trusted principal mapping")
    return {field: value[field] for field in ("tenant", "actor", "role")}


def validate_keys(keys):
    if not isinstance(keys, list) or len(keys) > 10000:
        raise ValueError("Invalid operator credential list")
    hashes = set()
    for key in keys:
        if not isinstance(key, dict) or set(key) != {
            "key_sha256",
            "tenant",
            "actor",
            "role",
        }:
            raise ValueError("Invalid operator credential entry")
        principal(key)
        if (
            not isinstance(key["key_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", key["key_sha256"])
            or key["key_sha256"] in hashes
        ):
            raise ValueError("Invalid or duplicate credential hash")
        hashes.add(key["key_sha256"])
    return keys


def endpoint(value, issuer=False):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Invalid trusted issuer/JWKS URL")
    parsed = urlsplit(value)
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid trusted endpoint port")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (issuer and parsed.query)
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in ("127.0.0.1", "localhost", "::1")
            )
        )
    ):
        raise ValueError(
            "Trusted issuer/JWKS requires HTTPS; HTTP is limited to loopback"
        )
    return value


def validate_sso(config):
    if not isinstance(config, dict) or set(config) - {"issuers", "revoked_tokens"}:
        raise ValueError("Invalid JWT access-token configuration")
    issuers = config.get("issuers")
    if not isinstance(issuers, list) or not 1 <= len(issuers) <= 10:
        raise ValueError("Configure 1-10 trusted issuers")
    seen = set()
    for provider in issuers:
        if not isinstance(provider, dict) or set(provider) - {
            "issuer",
            "audience",
            "jwks_url",
            "algorithms",
            "bindings",
            "groups_claim",
            "required_scopes",
            "max_token_seconds",
            "jwks_cache_seconds",
        }:
            raise ValueError("Invalid trusted issuer settings")
        name = endpoint(provider.get("issuer"), issuer=True)
        endpoint(provider.get("jwks_url"))
        if name in seen:
            raise ValueError("Duplicate trusted issuer")
        seen.add(name)
        if (
            not isinstance(provider.get("audience"), str)
            or not 0 < len(provider["audience"]) <= 2048
        ):
            raise ValueError("Set the API resource audience")
        algorithms = provider.get("algorithms", ["RS256"])
        if (
            not isinstance(algorithms, list)
            or not algorithms
            or len(set(algorithms)) != len(algorithms)
            or any(a not in ("RS256", "ES256") for a in algorithms)
        ):
            raise ValueError(
                "Only explicit RS256/ES256 access-token algorithms are supported"
            )
        for name, default, low, high in (
            ("max_token_seconds", 3600, 60, 86400),
            ("jwks_cache_seconds", 300, 1, 3600),
        ):
            setting = provider.get(name, default)
            if type(setting) is not int or not low <= setting <= high:
                raise ValueError("Invalid access-token/cache time bound")
        group_claim = provider.get("groups_claim", "groups")
        if not isinstance(group_claim, str) or not 1 <= len(group_claim) <= 80:
            raise ValueError("Invalid groups claim name")
        scopes = provider.get("required_scopes", [])
        if (
            not isinstance(scopes, list)
            or len(scopes) > 50
            or any(
                not isinstance(s, str) or not s or any(c.isspace() for c in s)
                for s in scopes
            )
        ):
            raise ValueError("Invalid required API scopes")
        bindings = provider.get("bindings")
        if not isinstance(bindings, list) or len(bindings) > 10000:
            raise ValueError("Explicit subject/group bindings are required")
        selectors = set()
        for binding in bindings:
            if (
                not isinstance(binding, dict)
                or set(binding) - {"subject", "group", "tenant", "actor", "role"}
                or ("subject" in binding) == ("group" in binding)
            ):
                raise ValueError("Bind exactly one subject or group")
            selector = "subject" if "subject" in binding else "group"
            if (
                not isinstance(binding[selector], str)
                or not 0 < len(binding[selector]) <= 256
                or (selector, binding[selector]) in selectors
            ):
                raise ValueError("Invalid/duplicate identity binding")
            selectors.add((selector, binding[selector]))
            if selector == "group" and "actor" in binding:
                raise ValueError(
                    "Group actors derive from signed subject, not a shared account"
                )
            principal({**binding, "actor": binding.get("actor", "group-subject")})
    denied = config.get("revoked_tokens", [])
    if (
        not isinstance(denied, list)
        or len(denied) > 10000
        or any(
            not isinstance(item, dict)
            or set(item) != {"issuer", "jti"}
            or item["issuer"] not in seen
            or not isinstance(item["jti"], str)
            or not 0 < len(item["jti"]) <= 256
            for item in denied
        )
    ):
        raise ValueError("Invalid revoked access-token list")
    return config


class KeySet:
    def __init__(self, provider):
        self.provider = provider
        self.keys = {}
        self.expires = 0
        self.unknown_refresh = float("-inf")
        self.lock = threading.Lock()

    def fetch(self):
        import jwt

        try:
            with httpx.Client(
                timeout=5, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream("GET", self.provider["jwks_url"]) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_CONFIG_BYTES:
                            raise ValueError("JWKS too large")
            document = json.loads(body)
            if not isinstance(document, dict):
                raise ValueError("Malformed JWKS document")
            records = document.get("keys")
            if not isinstance(records, list) or not 1 <= len(records) <= 100:
                raise ValueError("Invalid JWKS inventory")
            found = {}
            all_kids = set()
            for value in records:
                if not isinstance(value, dict):
                    raise ValueError("Malformed JWK")
                kid = value.get("kid")
                if (
                    not isinstance(kid, str)
                    or not 0 < len(kid) <= 200
                    or kid in all_kids
                ):
                    raise ValueError("Missing/ambiguous signing key ID")
                all_kids.add(kid)
                if (
                    value.get("kty") not in ("RSA", "EC")
                    or value.get("use", "sig") != "sig"
                    or ("key_ops" in value and value["key_ops"] != ["verify"])
                ):
                    continue
                algorithm = value.get("alg") or (
                    "RS256" if value["kty"] == "RSA" else "ES256"
                )
                if algorithm not in self.provider.get("algorithms", ["RS256"]):
                    continue
                if (algorithm == "RS256" and value["kty"] != "RSA") or (
                    algorithm == "ES256"
                    and (value["kty"] != "EC" or value.get("crv") != "P-256")
                ):
                    raise ValueError("Signing key/algorithm mismatch")
                if any(field in value for field in ("d", "p", "q", "dp", "dq", "qi")):
                    raise ValueError("JWKS must expose public keys only")
                key = jwt.PyJWK.from_dict(value, algorithm=algorithm)
                if algorithm == "RS256" and not 2048 <= key.key.key_size <= 8192:
                    raise ValueError("Unsupported RSA signing key size")
                found[kid] = (algorithm, key)
            if not found:
                raise ValueError("No allowed verification keys")
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            jwt.PyJWTError,
        ) as exc:
            raise AuthenticationUnavailable(
                "Trusted signing keys are unavailable"
            ) from exc
        self.keys = found
        self.expires = time.monotonic() + self.provider.get("jwks_cache_seconds", 300)

    def signing_key(self, kid, algorithm):
        with self.lock:
            now = time.monotonic()
            had_cache = bool(self.keys)
            refreshed = False
            if now >= self.expires:
                self.fetch()
                refreshed = True
            if (
                kid not in self.keys
                and had_cache
                and not refreshed
                and now - self.unknown_refresh >= 30
            ):
                self.unknown_refresh = now
                self.fetch()
            value = self.keys.get(kid)
            if not value or value[0] != algorithm:
                raise AuthenticationError("Invalid access token")
            return value[1]


class Authentication:
    def __init__(
        self, keys=None, keys_file=None, sso_file=None, configuration_reader=None
    ):
        self.keys = copy.deepcopy(validate_keys(keys)) if keys is not None else None
        self.keys_file = Path(keys_file).absolute() if keys_file else None
        self.sso_file = Path(sso_file).absolute() if sso_file else None
        self.providers = {}
        self.lock = threading.Lock()
        self.configuration_reader = configuration_reader
        if configuration_reader is not None and (
            not callable(configuration_reader)
            or keys is not None
            or keys_file
            or sso_file
        ):
            raise ValueError("Choose one trusted authentication configuration source")
        if (
            self.keys is None
            and self.keys_file is None
            and self.sso_file is None
            and configuration_reader is None
        ):
            raise ValueError("Configure DMT_KEYS_FILE and/or DMT_SSO_FILE")
        self.configuration()

    def configuration(self):
        try:
            if self.configuration_reader is not None:
                value = self.configuration_reader()
                return validate_keys(value["keys"]), (
                    validate_sso(value["sso"]) if value["sso"] is not None else None
                )
            keys = (
                self.keys
                if self.keys is not None
                else (
                    validate_keys(read_config(self.keys_file)) if self.keys_file else []
                )
            )
            sso = validate_sso(read_config(self.sso_file)) if self.sso_file else None
            return keys, sso
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AuthenticationUnavailable(
                "Operator authentication configuration is invalid"
            ) from exc

    def authenticate(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith(
            "Bearer "
        ):
            raise AuthenticationError("Bearer credential required")
        token = authorization[7:]
        if (
            not token
            or len(token.encode()) > MAX_TOKEN_BYTES
            or any(c.isspace() for c in token)
        ):
            raise AuthenticationError("Invalid credential")
        keys, sso = self.configuration()
        hashed = hashlib.sha256(token.encode()).hexdigest()
        for key in keys:
            if hmac.compare_digest(hashed, key["key_sha256"]):
                return principal(key)
        if not sso:
            raise AuthenticationError("Invalid credential")
        import jwt

        try:
            header = jwt.get_unverified_header(token)
            if header.get("typ") not in ("at+jwt", "application/at+jwt") or any(
                field in header for field in ("crit", "b64", "jku", "jwk", "x5u", "x5c")
            ):
                raise AuthenticationError("Invalid access token")
            kid = header.get("kid")
            if not isinstance(kid, str) or not 0 < len(kid) <= 200:
                raise AuthenticationError("Invalid access token")
            claims = jwt.decode(token, options={"verify_signature": False})
            issuer = claims.get("iss")
            provider = next((p for p in sso["issuers"] if p["issuer"] == issuer), None)
            if provider is None or header.get("alg") not in provider.get(
                "algorithms", ["RS256"]
            ):
                raise AuthenticationError("Invalid access token")
            # Select only a configured issuer; network targets and permissions never come from token claims.
            for name in ("exp", "iat", "nbf"):
                if name in claims and (
                    type(claims[name]) not in (int, float)
                    or not 0 <= claims[name] <= 253402300799
                ):
                    raise AuthenticationError("Invalid access token")
            descriptor = json.dumps(
                {
                    name: provider.get(name)
                    for name in (
                        "issuer",
                        "jwks_url",
                        "algorithms",
                        "jwks_cache_seconds",
                    )
                },
                sort_keys=True,
            )
            with self.lock:
                active_descriptors = {
                    json.dumps(
                        {
                            name: p.get(name)
                            for name in (
                                "issuer",
                                "jwks_url",
                                "algorithms",
                                "jwks_cache_seconds",
                            )
                        },
                        sort_keys=True,
                    )
                    for p in sso["issuers"]
                }
                self.providers = {
                    key: value
                    for key, value in self.providers.items()
                    if key in active_descriptors
                }
                keyset = self.providers.setdefault(descriptor, KeySet(provider))
            claims = jwt.decode(
                token,
                keyset.signing_key(kid, header["alg"]),
                algorithms=provider.get("algorithms", ["RS256"]),
                issuer=provider["issuer"],
                audience=provider["audience"],
                leeway=0,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id"]
                },
            )
            if claims["exp"] <= claims["iat"] or claims["exp"] - claims[
                "iat"
            ] > provider.get("max_token_seconds", 3600):
                raise AuthenticationError("Invalid access token")
            for name in ("sub", "jti", "client_id"):
                if (
                    not isinstance(claims[name], str)
                    or not 0 < len(claims[name]) <= 256
                ):
                    raise AuthenticationError("Invalid access token")
            if {"issuer": issuer, "jti": claims["jti"]} in sso.get(
                "revoked_tokens", []
            ):
                raise AuthenticationError("Revoked access token")
            scope = claims.get("scope", "")
            if not isinstance(scope, str) or not set(
                provider.get("required_scopes", [])
            ) <= set(scope.split()):
                raise UnmappedIdentity("Access token lacks the required API scopes")
            subject = claims["sub"]
            exact = [b for b in provider["bindings"] if b.get("subject") == subject]
            if exact:
                return principal(exact[0])
            groups = claims.get(provider.get("groups_claim", "groups"), [])
            if (
                not isinstance(groups, list)
                or len(groups) > 1000
                or any(not isinstance(g, str) for g in groups)
            ):
                raise UnmappedIdentity("Identity is not mapped to this workspace")
            matches = [b for b in provider["bindings"] if b.get("group") in groups]
            identities = {(b["tenant"], b["role"]) for b in matches}
            if len(identities) != 1:
                raise UnmappedIdentity(
                    "Identity is not uniquely mapped to this workspace"
                )
            tenant, role = next(iter(identities))
            return {
                "tenant": tenant,
                "role": role,
                "actor": "sso:"
                + hashlib.sha256((issuer + "\0" + subject).encode()).hexdigest(),
            }
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise AuthenticationError("Invalid access token") from exc
