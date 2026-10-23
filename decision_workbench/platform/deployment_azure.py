"""Azure Key Vault access and private configuration files for managed services."""

import argparse
import json
from pathlib import Path
import re
import secrets
import string
from urllib.parse import urlsplit


class AzureSecrets:
    def __init__(self, client):
        self.client = client

    @classmethod
    def from_environment(cls, environment):
        endpoint = environment.get("DMT_AZURE_KEY_VAULT_URL", "")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not re.fullmatch(
                r"[a-z][a-z0-9-]{1,22}[a-z0-9]\.vault\.azure\.net", parsed.netloc
            )
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Set the public Azure Key Vault HTTPS endpoint without credentials"
            )
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            raise RuntimeError(
                "Install requirements/azure-application.txt for Azure managed startup"
            ) from None
        credential = DefaultAzureCredential(
            managed_identity_client_id=environment.get(
                "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID"
            )
            or None,
            exclude_interactive_browser_credential=True,
        )
        return cls(
            SecretClient(
                vault_url=endpoint,
                credential=credential,
                retry_total=0,
                connection_timeout=10,
                read_timeout=30,
                logging_enable=False,
            )
        )

    def get_secret_value(self, *, SecretId):
        if not isinstance(SecretId, str) or not re.fullmatch(
            r"[a-zA-Z0-9-]{1,127}(?:/[a-f0-9]{32})?", SecretId
        ):
            raise ValueError(
                "Use a Key Vault secret name and optional immutable version"
            )
        name, _, version = SecretId.partition("/")
        return {"SecretString": self.client.get_secret(name, version or None).value}


def database_password(username):
    """Keep random entropy while always satisfying the managed database character mix."""
    fragments = {
        word[index : index + 3]
        for word in re.findall(r"[a-z0-9]+", username.casefold())
        for index in range(len(word) - 2)
    }
    while True:
        prefix = "".join(
            secrets.choice(characters)
            for characters in (
                string.ascii_lowercase,
                string.ascii_uppercase,
                string.digits,
                "!#%+-=",
            )
        )
        value = prefix + secrets.token_urlsafe(36)
        if not any(part in value.casefold() for part in fragments):
            return value


def prepare_configuration(keys_file, destination):
    """Create a new private bundle; print paths rather than passwords or key values."""
    from .auth import AuthenticationUnavailable, read_config, validate_keys
    from .deployment import private_file

    try:
        keys = validate_keys(read_config(keys_file))
    except AuthenticationUnavailable:
        raise ValueError("Supply a readable approved workspace-key file") from None
    if not keys:
        raise ValueError("Supply at least one approved initial workspace key")
    destination = Path(destination).absolute()
    values = {
        "backend.json": {"operator_keys": keys, "environment": {}},
        "database-admin.json": {
            "username": "workbenchadmin",
            "password": database_password("workbenchadmin"),
        },
        "database-app.json": {
            "username": "workbench_app",
            "password": database_password("workbench_app"),
        },
    }
    bodies = {
        name: json.dumps(value, allow_nan=False) for name, value in values.items()
    }
    if any(len(body.encode()) > 25 * 1024 for body in bodies.values()):
        raise ValueError(
            "Initial configuration exceeds the Azure Key Vault secret size limit"
        )
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, body in bodies.items():
        private_file(destination, name, body)
    return {"files": [str(destination / name) for name in values]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys-file", required=True)
    parser.add_argument(
        "--output",
        required=True,
        help="New private directory; existing directories are preserved",
    )
    args = parser.parse_args()
    try:
        result = prepare_configuration(args.keys_file, args.output)
    except (ValueError, OSError, RuntimeError):
        parser.error(
            "Cannot create private Azure configuration; check the keys file and choose a new output directory"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
