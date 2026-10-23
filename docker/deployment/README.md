# Managed application image

Extend the repository's pinned Linux/amd64 CPU image with the hash-pinned [PostgreSQL/OIDC extension](../../requirements/managed-application-linux-amd64-py312.txt) and the public RDS CA bundle. API, worker, bootstrap and browser roles share the same image digest; runtime permissions and secret access differ by role.

Follow the [AWS application deployment](../../docs/application-hosting/aws.md) for image publication, infrastructure review, database/authorization initialization, HTTPS access, monitoring, updates and cleanup. This image contains the existing five-provider control clients and CPU baseline, not GPU frameworks or model weights.

For Azure, extend that managed image with [Dockerfile.azure](Dockerfile.azure) and its [hash-pinned SDK profile](../../requirements/managed-azure-linux-amd64-py312.txt). Follow the [Azure hosting walkthrough](../../docs/application-hosting/azure.md). It selects `DMT_DEPLOYMENT_PROVIDER=azure`, reads JSON configuration through Key Vault and uses Blob Storage; the AWS profile keeps its existing default. Both use the same database bootstrap, API, worker and authorization publication commands.
