# Configure shared workspace storage

<a id="share-model-artifacts-between-workspace-nodes"></a>

[Documentation](README.md) · [Project overview](../README.md)

PostgreSQL stores shared metadata. Add one artifact transport when API and worker nodes need independent verified caches. Select S3, Azure Blob or GCS; configuring multiple live transports is refused.

## Choose a section

- [S3 model artifacts](#s3-artifacts)

- [PostgreSQL metadata setup](#postgresql-metadata)

- [Azure Blob artifacts](#azure-blob)

- [Google Cloud Storage artifacts](#google-cloud-storage)

<a id="s3-artifacts"></a>

## S3 model artifacts

<a id="s3-artifacts--share-model-artifacts-between-workspace-nodes"></a>

Configure an S3 object repository with PostgreSQL metadata to publish trained
exports once and retrieve them into separate API/worker caches. This experimental
route preserves the model's schema, evaluation report, calibration and file
hashes. Personal SQLite mode continues to use local artifacts.

For Azure or Google Cloud storage, follow [the Azure Blob profile](shared-artifacts-guide.md#azure-blob)
or [the GCS profile](shared-artifacts-guide.md#google-cloud-storage).
They use the same model publication, verified-cache and recovery workflows with
different storage credentials and versioning permissions. The setup examples below use S3.

This is a model-artifact workflow, not a complete enterprise deployment. Shared
identity, [coordinated recovery](recovery-guide.md#shared-workspace-recovery), operational acceptance
and cloud application deployment need their own configuration. Verified remote-admission inputs can
also move between these nodes; model environments and unfinished job directories
remain local to their worker.

<a id="s3-artifacts--prepare-storage-and-a-new-workspace"></a>

### Prepare storage and a new workspace

Follow [PostgreSQL setup](shared-artifacts-guide.md#postgresql-metadata), using a **new database schema
without registered models**. Existing SQLite or metadata-only model registries
are not automatically migrated. Stop all API/worker processes before upgrading:
this route uses PostgreSQL workspace protocol 14 and older services must not
continue writing during the change. SQLite remains on its existing protocol.

Install the optional clients in both API and worker environments:

```bash
python -m pip install -r requirements/postgres.txt -r requirements/aws-sdk.txt
python -m pip check
```

Use a private general-purpose S3 bucket. Give the application identity access to
`GetObject`, `PutObject` and `AbortMultipartUpload` within its selected prefix.
Configure server-side encryption, retention/versioning, restricted bucket access
and a lifecycle rule to expire abandoned multipart uploads. Prevent other
writers from overwriting/deleting active model objects. Follow AWS's
[conditional-write policy guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html),
including its multipart exceptions; a blanket rule requiring the header on every
multipart request can block part uploads.

The writer creates objects with `If-None-Match: *`, including multipart
completion. Readers verify SHA-256 and size; ETags are not used as model hashes.
Existing content-addressed objects are read and verified before reuse, avoiding
another full upload. Bucket policies using customer-managed encryption keys also
need the corresponding key permissions for the application identity.
[Conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html),
[multipart completion](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html).

<a id="s3-artifacts--configure-each-node"></a>

### Configure each node

Use the same database/schema, bucket, region, endpoint and prefix on every node.
Each node may use a different local `--home` cache. Provide the workspace's
operator-controlled identity configuration on every API/worker as described in
[authentication](authentication-guide.md#workspace-credentials).

```bash
export DMT_DATABASE_URL='YOUR_POSTGRESQL_CONNECTION_URI'
export DMT_KEYS_FILE='YOUR_PRIVATE_OPERATOR_KEYS_FILE'
export DMT_ARTIFACT_S3_BUCKET='YOUR_PRIVATE_BUCKET'
export DMT_ARTIFACT_S3_REGION='YOUR_BUCKET_REGION'
export DMT_ARTIFACT_S3_PREFIX='decision-workbench'
```

In PowerShell use `$env:VARIABLE = 'value'` for each setting. AWS credentials come
from the SDK's credential chain; prefer scoped workload roles on cloud hosts.
The database URI and AWS credentials belong in the operator environment, never
in the browser or model records. See [AWS credentials](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html).

An optional `DMT_ARTIFACT_S3_ENDPOINT` selects another S3 endpoint. HTTPS with
certificate verification is required, except for an explicit loopback HTTP test
address. Alternative services must independently support the same conditional
write, multipart and checksum contracts; configuring a URL does not establish
compatibility.

Start the API and worker using the [operator example](../examples/platform/postgres_workspace.py):

```bash
python examples/platform/postgres_workspace.py api --home data/api-cache
python examples/platform/postgres_workspace.py worker --home data/worker-cache
```

Run those commands on their respective hosts/environments when using separate
nodes. Keep model environments, profiles and resource limits compatible with
each selected backend. Connect Streamlit to the API using its workspace token
and follow the [application workflow](app-guide.md#screen-manual).

Storage settings bind to the database workspace identity. A conflicting bucket,
prefix or endpoint is refused; a node cannot silently register local-only models
in a workspace configured for shared artifacts. Do not remove the local
secret-free database binding to point a cache at another workspace.

<a id="s3-artifacts--train-release-and-warm-a-cache"></a>

### Train, release and warm a cache

For disk retention after warming, use [inactive cache cleanup](storage-maintenance-guide.md#model-caches).
It preserves registry/shared objects and released caches; stop the selected
node before previewing or applying eviction.

Train/evaluate through the normal workflow. The worker uploads content-addressed
files and an immutable manifest **before** creating the model registry record.
Cancellation and ownership checks fence publication; an interrupted transfer
cannot expose a partial model as a registered release. Orphaned objects can still
remain after cancellation or an uncertain database commit.

Small cache misses, up to 8 MiB total, download automatically. Larger native
exports require explicit operator warming on each serving node, outside the web
request. Allow enough disk space for the verified cache and temporary transfer.
Use the tenant, project and model IDs from the registry:

```bash
python examples/platform/postgres_workspace.py warm-cache --home data/api-cache --tenant YOUR_TENANT --project YOUR_PROJECT_ID --model YOUR_MODEL_ID
```

The command verifies the files and prints the cache directory. Then activate the
eligible model and send predictions normally. A cold large-model request returns
503 with cache-warming guidance; it does not start an unbounded weight download
inside the request. Warming does not install a model environment or satisfy its
hardware requirements.

Successful downloads publish their local directory atomically. Cached manifests
are pinned by the registry hash, so warm predictions need no object-storage
request; model files still undergo the applicable integrity checks. A corrupt
cache or object is refused. Inspect the node and restore a verified cache before
retrying; do not bypass its hashes or modify a registered model in place.

<a id="s3-artifacts--retrieve-and-admit-remote-training-results-on-separate-nodes"></a>

### Retrieve and admit remote training results on separate nodes

Use the same PostgreSQL schema and object repository on the API, retrieval worker
and admission worker. The five managed training providers keep their own
credentials and retrieval protocols. After retrieval, the workbench verifies the
export and publishes an immutable input manifest before queuing admission.
The worker that claims admission retrieves and verifies that input in its own
`incoming` cache, then runs the normal [independent qualification](remote-training-guide.md#returned-model-admission--5-qualify-review-and-release).
An object-storage checksum does not replace model evaluation or owner release.

For operator-supplied exports, configure `DMT_DATABASE_URL`,
`DMT_ARTIFACT_S3_BUCKET` and the matching region/prefix/endpoint as above, then run:

```bash
domain-platform import-remote --home data/operator-cache --database-schema decision_workbench --keys-file /operator/workspace-keys.json --project YOUR_PROJECT_ID --study YOUR_STUDY_ID --export retrieved/export --manifest-sha256 YOUR_RETAINED_SHA256 --receipt provider-receipt.json --request-key YOUR_UNIQUE_IMPORT_KEY
```

Supply `DMT_OPERATOR_TOKEN` through your secret mechanism. Use your actual
database schema; `DMT_DATABASE_SCHEMA` supplies its default. Keep the same trusted
authentication configuration as the API. See [operator admission](remote-training-guide.md#returned-model-admission--4-stage-and-queue-admission-as-an-authenticated-operator)
for the receipt format, permissions and input limits.

Keep a logical import's idempotency key unchanged when recovering a lost response.
Identical scoped requests on different nodes return the same job and input
identity. Conflicting requests are refused. An uncertain submission preserves
its staged input rather than deleting files that a committed job may need.
Cancelled or expired retrieval workers cannot queue a new admission.

The admission node needs space for both the verified input cache and a separate
qualification copy, plus its evaluation data and logs. Cache hydration and the qualification copy are
preflighted with a 64 MiB reserve; this is not a reservation against concurrent
disk use. Input transfer checks cancellation and ownership between chunks.
Insufficient space, unavailable objects or failed integrity checks stop admission;
no model is activated. In personal SQLite mode, intake remains local to that
workspace and uses no object service.

<a id="s3-artifacts--retention-recovery-and-checks"></a>

### Retention, recovery and checks

Configure [shared-workspace monitoring](shared-workspace-operations-guide.md) to inspect cached manifest connectivity and readiness. The probe does not download model weights or verify every object.

Do not delete objects solely because a recent upload has no registry row:
publication may be in progress. Shared-object garbage collection and coordinated
database/object recovery require an operator policy. Use the [shared recovery commands](recovery-guide.md#shared-workspace-recovery) for consistent capture and fresh-workspace restoration. SQLite backup commands do
not back up this backend. Local caches can contain model/domain information;
include them in access control and deletion procedures.

The current bounds are 10,000 files, an 8 MiB manifest and 500 GiB per export.
Multipart transfers default to 64 MiB parts. Incomplete multipart uploads are
aborted when possible; a failed cleanup needs operator attention and the bucket
lifecycle policy.

For local contract checks, install the clients and run:

```bash
python -m unittest discover -s tests -p test_shared_artifacts.py -v
python -m unittest discover -s tests -p test_enterprise_flows.py -v
```

Set `DMT_TEST_POSTGRES_URL` to a disposable database to include PostgreSQL
workflows and the separate-node hosted/five-provider flow checks. The local
directory transport checks actual file/cache behavior; S3
tests use the installed SDK with fictional credentials and validated responses.
They do not contact a paid account or establish real S3 billing, production scale
or native model quality. Follow the [CI checks](../.github/CI.md) and qualify your
storage account and selected model environment before deployment.

Use [shared-object retention](storage-maintenance-guide.md#shared-object-versions) to preview and reconcile deletion of old unreferenced S3 versions. Keep local staging, project deletion and coordinated backup recovery under their separate policies.

Partial transfers created by this release carry local ownership records and OS leases. Use [owned local transfer cleanup](storage-maintenance-guide.md#transfer-stages) after a process dies; active transfers and unmarked directories are preserved.

For native Google Cloud Storage, follow [GCS artifact setup](shared-artifacts-guide.md#google-cloud-storage). It uses the same verified-cache and coordinated recovery workflows with ADC and generation-pinned object copies.

<a id="postgresql-metadata"></a>

## PostgreSQL metadata setup

<a id="postgresql-metadata--run-workspace-metadata-on-postgresql"></a>

Use the experimental PostgreSQL backend for projects, reviewed datasets,
reservations, job ownership, releases and audit events. Personal localhost mode
continues to use SQLite.

**With metadata-only configuration, keep API and worker on one host with the same
workspace directory.** Optional shared-artifact profiles publish model exports to
[S3](shared-artifacts-guide.md#s3-artifacts), [Azure Blob Storage](shared-artifacts-guide.md#azure-blob) or
[GCS](shared-artifacts-guide.md#google-cloud-storage) and support separate node caches. PostgreSQL alone
does not move job/staging files. Configure [organization sign-in](authentication-guide.md#organization-sign-in)
and [coordinated database/object recovery](recovery-guide.md#shared-workspace-recovery) separately.

<a id="postgresql-metadata--prepare-the-database"></a>

### Prepare the database

Install the [CPU application](../README.md#run-on-your-computer), then add the
optional Psycopg 3.3.6 driver in that environment:

```bash
python -m pip install -r requirements/postgres.txt
python -m pip check
```

Use a supported PostgreSQL server; automated contracts use PostgreSQL 18.6. Ask
your administrator to create a dedicated database and application role. After
creating the `workbench_app` role, create its schema as the database administrator:

```sql
CREATE SCHEMA decision_workbench AUTHORIZATION workbench_app;
```

The application role needs usage/create privileges and access to its tables and
sequences. Prevent untrusted roles from creating objects in this schema. Use a
separate schema for each workspace. Schema names must start with a lowercase
letter and contain only lowercase letters, digits and underscores. Public/system
schemas and missing schemas are refused. Existing SQLite workspaces are not
automatically migrated. [PostgreSQL schemas](https://www.postgresql.org/docs/18/ddl-schemas.html).

Stop API and worker processes before upgrading a PostgreSQL workspace. The current
metadata protocol is 14; an older service version must not continue writing
during an upgrade. Downgrading against newer metadata is refused. The personal
SQLite protocol is unchanged by the optional object-storage route.

<a id="postgresql-metadata--start-the-services"></a>

### Start the services

Provision a workspace key and save the bearer token displayed once:

```bash
domain-platform provision-key --file data/postgres-keys.json --tenant team --actor owner --role admin
```

Set these variables in **both the API and worker terminals**. Supply your own
connection URI and required TLS settings; percent-encode reserved characters in
URI credentials. Remote servers should use certificate verification, such as
`sslmode=verify-full` with the correct root CA.

```bash
export DMT_DATABASE_URL='postgresql://workbench_app:YOUR_ENCODED_PASSWORD@YOUR_DB_HOST/workbench?sslmode=verify-full'
export DMT_KEYS_FILE='data/postgres-keys.json'
```

In PowerShell use `$env:DMT_DATABASE_URL = 'YOUR_CONNECTION_URI'` and
`$env:DMT_KEYS_FILE = 'data/postgres-keys.json'`. Keep the connection string
server-side and outside Git. Never paste it into the browser.
[Connection URI and TLS parameters](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-CONNSTRING).

Start the API:

```bash
python examples/platform/postgres_workspace.py api --home data/postgres-workspace
```

Start the worker in the second configured terminal:

```bash
python examples/platform/postgres_workspace.py worker --home data/postgres-workspace
```

In a third terminal run `python -m streamlit run app.py`. Choose **Shared
workspace**, expand **Connect to an existing workspace**, enter
`http://127.0.0.1:8080` and your provisioned token, then
follow the [application guide](app-guide.md#screen-manual). Both services must use the same
`--home` and `--schema` settings. Stop with Ctrl+C and restart with the same
database, schema and directory.

This example explicitly selects PostgreSQL. Setting `DMT_DATABASE_URL` alone
does not redirect the ordinary personal launcher or migrate its SQLite data.
Connection failures do not silently fall back to SQLite.

Start with a new artifact directory. The backend refuses a directory containing
a SQLite database and records a secret-free binding to the PostgreSQL workspace
identity. Reopening it with another database/schema or as SQLite is refused.
Keep that binding with the directory; it is not a database backup or migration.

<a id="postgresql-metadata--transaction-and-recovery-limits"></a>

### Transaction and recovery limits

Short metadata transactions commit or roll back together. A database/schema
transaction advisory lock preserves quotas, compare-and-swap updates and job
fencing across connections/processes. Locks end on transaction completion or
connection death. Operations currently serialize per workspace; this is not a
high-throughput pooled backend. Model/provider work belongs outside these
transactions. [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PostgreSQL locks](https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS).

The bounded SQL adapter preserves numeric revision ordering, timestamp precision
and typed metadata fields. It is not a general SQLite SQL translator. Queries
keep values separate from SQL. [Parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html),
[JSON functions](https://www.postgresql.org/docs/18/functions-json.html).

Local SQLite backup commands **do not capture PostgreSQL**. With shared object artifacts,
use [coordinated backup and restore](recovery-guide.md#shared-workspace-recovery). For metadata-only
deployments, arrange database backups and retain the corresponding local artifacts.
Perform a restore drill before relying on either route.

<a id="postgresql-metadata--verify-a-disposable-database"></a>

### Verify a disposable database

For queue limits, node readiness, failure monitoring and the two-node functional workload, follow [shared workspace operations](shared-workspace-operations-guide.md).

Set `DMT_TEST_POSTGRES_URL` to a dedicated test database whose role can create/drop
schemas, then run:

```bash
python -m unittest discover -s tests -p test_postgres.py -v
```

Each case creates/removes its own random `dmt_test_...` schema. Checks cover
rollback, constraints, independent process claims, stale-worker fencing,
numerical JSON ordering and actual API/worker/CPU release workflows. Database
cases skip when the variable is absent; SQL-adapter checks still run. The
[CI job](../.github/CI.md) supplies a disposable server. These checks do not
establish production scale, cloud availability or domain model quality.

Use [shared-object retention](storage-maintenance-guide.md#shared-object-versions) to preview and reconcile deletion of old unreferenced S3 versions, Azure blob versions or GCS generations. Keep local staging, project deletion and coordinated backup recovery under their separate policies.

<a id="azure-blob"></a>

## Azure Blob artifacts

<a id="azure-blob--store-shared-model-artifacts-in-azure-blob-storage"></a>

Use a private Azure Blob container to share exported model files between API and worker instances. PostgreSQL holds the project, job, review and release history; each instance keeps a verified local model cache. This storage configuration is separate from submitting an Azure Machine Learning training job.

<a id="azure-blob--prepare-storage-and-identity"></a>

### Prepare storage and identity

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

<a id="azure-blob--configure-api-and-worker-instances"></a>

### Configure API and worker instances

Complete the [local installation](../README.md#run-on-your-computer) and [shared PostgreSQL setup](shared-artifacts-guide.md#postgresql-metadata), then install the optional storage dependencies in the same application environment:

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

Leave `DMT_ARTIFACT_S3_BUCKET` and `DMT_ARTIFACT_GCS_BUCKET` unset when choosing Azure; configuring multiple live transports is refused. Restart the API/worker services after changing configuration. A workspace is bound to its storage descriptor, so pointing an existing workspace at a different container/prefix is refused. Use the recovery workflow to migrate or restore state deliberately.

Train and release a reviewed CPU baseline through the [application walkthrough](app-guide.md#screen-manual--first-project-walkthrough). A second instance using the same database and storage configuration can retrieve the released export into its own verified cache. `/ready` checks metadata and, when a registered model exists, one artifact manifest's availability. It does not verify every stored file or your domain model's quality.

The adapter stages bounded blocks, checks the complete source hash and conditionally commits a new object. An identical retry verifies existing content; conflicting content is refused. Failed or cancelled staging is never registered as a completed model. Azure retains uncommitted blocks temporarily under its service policy, so interrupted uploads can still have a storage footprint. [Block uploads](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).

<a id="azure-blob--back-up-and-restore-shared-state"></a>

### Back up and restore shared state

Configure an independent private vault in the same storage account:

```bash
export DMT_BACKUP_AZURE_ACCOUNT_URL=https://YOURACCOUNT.blob.core.windows.net
export DMT_BACKUP_AZURE_CONTAINER=backups
export DMT_BACKUP_AZURE_PREFIX=recovery
```

An optional `DMT_BACKUP_AZURE_CLIENT_ID` selects the backup operator's managed identity. Follow the existing [coordinated backup, verification and cold restore commands](recovery-guide.md#shared-workspace-recovery). The same commands select Azure from these variables; leave the corresponding S3 bucket variables unset.

Recovery pins exact blob versions and copies their ranges server-side with source ETag checks. It does not download model weights into the operator process. Configure a distinct target container/prefix before restoring, use a fresh database schema and private home, and then configure every reopened instance with the returned storage prefix. Cross-account copies are refused by this profile; use an approved independent vault container and account-level disaster-recovery controls appropriate to your organization.

<a id="azure-blob--clean-up-deliberately"></a>

### Clean up deliberately

Use the [shared-object cleanup workflow](storage-maintenance-guide.md#shared-object-versions) with a separately authorized operator. Cleanup targets the version recorded in its approved plan, rather than deleting a replacement blob by name. Storage errors leave the operation unconfirmed for reconciliation.

Azure soft delete, legal holds and immutability policies can delay or prevent physical removal. A successful version-delete request does not prove that retained data or storage charges have disappeared. Review those policies, backups and account/container retention separately. [Azure deletion and retention](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-delete-python).

Before adopting the configuration, check actual managed-identity permissions, private networking, regional service availability, cold recovery and representative model behavior in your account. SDK/HTTP fixtures and a fictional object service exercise repository mechanics; they do not establish deployed Azure behavior.

<a id="google-cloud-storage"></a>

## Google Cloud Storage artifacts

<a id="google-cloud-storage--store-shared-model-artifacts-in-google-cloud-storage"></a>

Use Cloud Storage for immutable model exports shared by PostgreSQL-backed API and worker instances. Each node keeps a verified local cache; the bucket holds the durable artifacts. This configuration does not run training on a GPU or turn an instance's local files into shared storage.

<a id="google-cloud-storage--prepare-a-private-versioned-bucket"></a>

### Prepare a private, versioned bucket

Choose a bucket in your application's region. Use a dedicated bucket without dots in its name, uniform bucket-level access, public-access prevention and Object Versioning. Replace the uppercase values below:

```text
gcloud storage buckets create gs://ARTIFACT_BUCKET --project=PROJECT --location=REGION --uniform-bucket-level-access
gcloud storage buckets update gs://ARTIFACT_BUCKET --public-access-prevention --versioning
```

Bucket names are globally unique. Enable and verify versioning before using retention or recovery. Soft delete, old generations and recovery copies can continue to incur charges; they are separate from the application's reviewed deletion history. [Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning), [soft delete](https://docs.cloud.google.com/storage/docs/soft-delete).

Give the API/worker service account `roles/storage.objectCreator` and `roles/storage.objectViewer` **on the live bucket**, rather than a project-wide storage administrator role. Normal publication needs object creation and reading, without object deletion or bucket administration. Use the same approved identity on nodes that share this workspace. Tenant isolation inside that shared service remains enforced by the application.

Recovery and retention operators need additional scoped permissions: `storage.buckets.get` to inspect versioning; `storage.objects.get`, `storage.objects.list` and `storage.objects.create` on the relevant source/destination; and `storage.objects.delete` only where reviewed cleanup is authorized. Define a custom operator role or use your organization's established access policy. Keep backup identities independent from ordinary application access. [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles).

<a id="google-cloud-storage--install-the-storage-profile-and-configure-credentials"></a>

### Install the storage profile and configure credentials

In the environment running the API, worker or recovery command:

```text
python -m pip install -r requirements/gcp-artifacts.txt
python -m pip check
```

The client uses [Application Default Credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials). On Google Cloud, attach the approved service account to the workload. For a development operator environment, configure ADC through your organization's approved login or workload-identity procedure. Do not put credential documents in model exports, dataset files or browser configuration.

Configure these variables in the API/worker environment:

| Variable | Value |
|---|---|
| `DMT_DATABASE_URL` | Your shared PostgreSQL connection, configured through the deployment's secret procedure |
| `DMT_ARTIFACT_GCS_BUCKET` | Live bucket name, without `gs://` |
| `DMT_ARTIFACT_GCS_PROJECT` | Google Cloud project used to initialize the client |
| `DMT_ARTIFACT_GCS_PREFIX` | Canonical lowercase prefix, such as `live` |

Configure one live transport: GCS, S3 or Azure Blob. Mixed live settings are refused; the backup transport has its own configuration. Managed GCS configuration refuses `STORAGE_EMULATOR_HOST` rather than silently sending artifacts to an emulator.

Start the services through your existing shared-workspace deployment. Train and release the CPU baseline first, then verify a cold node can warm the registered export and make the same predictions. Follow [shared cache warming](shared-artifacts-guide.md#s3-artifacts--train-release-and-warm-a-cache) for native model exports; the GCS transport uses the same verified-cache commands and lifecycle.

<a id="google-cloud-storage--publication-and-storage-costs"></a>

### Publication and storage costs

The adapter verifies source size and SHA-256, uploads to a unique staging object, verifies the bytes consumed by the SDK, then copies that exact generation into its final immutable name. Both upload and final publication use a create-only generation precondition. An identical retry checks existing bytes; different content cannot replace the final object.

Uploads use CRC32C and bounded resumable chunks for larger files. Downloads resolve an exact generation before reading ranges, so a progressive read cannot combine different object versions. Recovery copies stay inside Cloud Storage rather than downloading foundation-model weights through the operator computer. [Generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions), [Python Blob operations](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob).

**Staging objects temporarily add storage usage.** They remain under the workspace's object prefix as unreferenced objects, including after successful publication. Ordinary application identities cannot delete them. Use the [reviewed retention workflow](storage-maintenance-guide.md#shared-object-versions) with an authorized operator to remove sufficiently old unreferenced generations. Choose retention intervals and cost budgets accordingly. Bucket soft-delete policy can retain deleted generations beyond application cleanup; do not add broad lifecycle rules that delete registered models or recovery copies.

<a id="google-cloud-storage--back-up-and-restore"></a>

### Back up and restore

Prepare an independent versioned backup bucket, with the same privacy controls, then configure:

| Variable | Value |
|---|---|
| `DMT_BACKUP_GCS_BUCKET` | Independent recovery bucket |
| `DMT_BACKUP_GCS_PROJECT` | Recovery client's project |
| `DMT_BACKUP_GCS_PREFIX` | Recovery prefix, such as `vault` |

Run the existing [coordinated backup, verification and restore commands](recovery-guide.md#shared-workspace-recovery). Both live and backup stores must use GCS for this server-side copy path. Different buckets are supported when the operator identity has the required access; overlapping prefixes in one bucket are refused.

The recovery manifest pins generation, ETag, size and SHA-256. Restore into a fresh PostgreSQL schema, home directory and object prefix. Preserve the required source generations and current trust policy until cold prediction, role isolation and recovery checks pass in your environment.

For orphan cleanup, inventory is bounded and deletion targets an exact generation with a metadata precondition. A new replacement generation is not deleted by an older approved cleanup operation. If the operation reports an uncertain result, inspect and reconcile the saved operation before retrying.

Before adopting this configuration, verify ADC permissions, service connectivity, quotas, billing, retention and cold recovery in your account. Local SDK HTTP fixtures and CPU/PostgreSQL workflows establish repository behavior; they do not establish deployed Google Cloud acceptance.
