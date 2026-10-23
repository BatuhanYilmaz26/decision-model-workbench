# Store shared model artifacts in Azure Blob Storage

[All guides](README.md) · [PostgreSQL metadata](postgresql-guide.md) · [Shared recovery](shared-recovery-guide.md)

Use a private Azure Blob container to share exported model files between API and worker instances. PostgreSQL holds the project, job, review and release history; each instance keeps a verified local model cache. This storage configuration is separate from submitting an Azure Machine Learning training job.

## Prepare storage and identity

Use a public-Azure HTTPS account endpoint such as `https://YOURACCOUNT.blob.core.windows.net`, a private container and a canonical lowercase prefix. The adapter accepts neither an endpoint SAS token nor an account connection string. Sovereign-cloud endpoints and hierarchical-namespace accounts are outside this profile.

Enable blob versioning on a compatible general-purpose v2 account before publishing artifacts. Keep live objects, recovery vaults and restoration targets in separate containers. Do not apply an automatic expiry policy to registered live models or retained backup objects. Versioning does not protect against deleting an account or container; protect those resources through your access and retention policies. [Azure versioning and limitations](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-overview).

From an approved Azure operator environment, enable versioning with your account values:

```bash
az storage account blob-service-properties update --resource-group YOUR_RESOURCE_GROUP --account-name YOURACCOUNT --enable-versioning true
```

The command changes a storage-account setting. Review the storage charges and your retention policy first. [Enable versioning](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-enable).

Supply Microsoft Entra credentials through the Azure SDK's default credential chain. For local operation, authenticate with `az login` using an identity authorized for that account. For managed services, assign a managed identity and its scoped roles. Set `DMT_ARTIFACT_AZURE_CLIENT_ID` when choosing a user-assigned managed identity. Interactive browser authentication is disabled in the service adapter. [DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential).

Separate permissions by purpose:

| Identity | Required access |
|---|---|
| API and worker | Read and create/write blobs in the live container; no account administration or version deletion |
| Recovery operator | Read source versions, create objects in the independent vault/target, and inspect blob-service properties to verify versioning |
| Cleanup operator | Inspect the selected workspace inventory and delete specifically approved blob versions; keep these privileges out of normal application identities |
| Browser service | No storage or database permissions |

Checking versioning requires the management action `Microsoft.Storage/storageAccounts/blobServices/read`. Reading blob properties requires the separate blob data-read action. Deleting previous versions requires `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/deleteBlobVersion/action`; ordinary blob deletion alone is insufficient. Use scoped custom roles when a built-in role grants more access than required. [Authorization actions](https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory), [version permissions](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-overview#authorize-operations-on-blob-versions).

## Configure API and worker instances

Complete the [local installation](../README.md#run-on-your-computer) and [shared PostgreSQL setup](postgresql-guide.md), then install the optional storage dependencies in the same application environment:

```bash
python -m pip install -r requirements/azure-artifacts.txt
python -m pip check
```

Set these nonsecret values on every API/worker instance sharing the workspace:

```bash
export DMT_ARTIFACT_AZURE_ACCOUNT_URL=https://YOURACCOUNT.blob.core.windows.net
export DMT_ARTIFACT_AZURE_CONTAINER=artifacts
export DMT_ARTIFACT_AZURE_PREFIX=live
```

In PowerShell, use `$env:DMT_ARTIFACT_AZURE_ACCOUNT_URL='https://YOURACCOUNT.blob.core.windows.net'` and the same syntax for the other variables. Keep database credentials, identity credentials and workspace keys in the restricted operator environment described in the PostgreSQL guide.

Leave `DMT_ARTIFACT_S3_BUCKET` unset when choosing Azure; configuring both transports is refused. Restart the API/worker services after changing configuration. A workspace is bound to its storage descriptor, so pointing an existing workspace at a different container/prefix is refused. Use the recovery workflow to migrate or restore state deliberately.

Train and release a reviewed CPU baseline through the [application walkthrough](app-guide.md#first-project-walkthrough). A second instance using the same database and storage configuration can retrieve the released export into its own verified cache. `/ready` checks metadata and, when a registered model exists, one artifact manifest's availability. It does not verify every stored file or your domain model's quality.

The adapter stages bounded blocks, checks the complete source hash and conditionally commits a new object. An identical retry verifies existing content; conflicting content is refused. Failed or cancelled staging is never registered as a completed model. Azure retains uncommitted blocks temporarily under its service policy, so interrupted uploads can still have a storage footprint. [Block uploads](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).

## Back up and restore shared state

Configure an independent private vault in the same storage account:

```bash
export DMT_BACKUP_AZURE_ACCOUNT_URL=https://YOURACCOUNT.blob.core.windows.net
export DMT_BACKUP_AZURE_CONTAINER=backups
export DMT_BACKUP_AZURE_PREFIX=recovery
```

An optional `DMT_BACKUP_AZURE_CLIENT_ID` selects the backup operator's managed identity. Follow the existing [coordinated backup, verification and cold restore commands](shared-recovery-guide.md). The same commands select Azure from these variables; leave the corresponding S3 bucket variables unset.

Recovery pins exact blob versions and copies their ranges server-side with source ETag checks. It does not download model weights into the operator process. Configure a distinct target container/prefix before restoring, use a fresh database schema and private home, and then configure every reopened instance with the returned storage prefix. Cross-account copies are refused by this profile; use an approved independent vault container and account-level disaster-recovery controls appropriate to your organization.

## Clean up deliberately

Use the [shared-object cleanup workflow](shared-object-retention-guide.md) with a separately authorized operator. Cleanup targets the version recorded in its approved plan, rather than deleting a replacement blob by name. Storage errors leave the operation unconfirmed for reconciliation.

Azure soft delete, legal holds and immutability policies can delay or prevent physical removal. A successful version-delete request does not prove that retained data or storage charges have disappeared. Review those policies, backups and account/container retention separately. [Azure deletion and retention](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-delete-python).

Before adopting the configuration, check actual managed-identity permissions, private networking, regional service availability, cold recovery and representative model behavior in your account. SDK/HTTP fixtures and a fictional object service exercise repository mechanics; they do not establish deployed Azure behavior.
