# Share model artifacts between workspace nodes

[All guides](README.md) · [PostgreSQL setup](postgresql-guide.md) · [Project overview](../README.md)

Configure an S3 object repository with PostgreSQL metadata to publish trained
exports once and retrieve them into separate API/worker caches. This experimental
route preserves the model's schema, evaluation report, calibration and file
hashes. Personal SQLite mode continues to use local artifacts.

This is a model-artifact workflow, not a complete enterprise deployment. Shared
identity, coordinated recovery, operational acceptance and cloud application
deployment need their own configuration. Verified remote-admission inputs can
also move between these nodes; model environments and unfinished job directories
remain local to their worker.

## Prepare storage and a new workspace

Follow [PostgreSQL setup](postgresql-guide.md), using a **new database schema
without registered models**. Existing SQLite or metadata-only model registries
are not automatically migrated. Stop all API/worker processes before upgrading:
this route uses PostgreSQL workspace protocol 10 and older services must not
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

## Configure each node

Use the same database/schema, bucket, region, endpoint and prefix on every node.
Each node may use a different local `--home` cache. Provide the workspace's
operator-controlled identity configuration on every API/worker as described in
[authentication](authentication-guide.md).

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
and follow the [application workflow](app-guide.md).

Storage settings bind to the database workspace identity. A conflicting bucket,
prefix or endpoint is refused; a node cannot silently register local-only models
in a workspace configured for shared artifacts. Do not remove the local
secret-free database binding to point a cache at another workspace.

## Train, release and warm a cache

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

## Retrieve and admit remote training results on separate nodes

Use the same PostgreSQL schema and object repository on the API, retrieval worker
and admission worker. The five managed training providers keep their own
credentials and retrieval protocols. After retrieval, the workbench verifies the
export and publishes an immutable input manifest before queuing admission.
The worker that claims admission retrieves and verifies that input in its own
`incoming` cache, then runs the normal [independent qualification](remote-admission-guide.md#5-qualify-review-and-release).
An object-storage checksum does not replace model evaluation or owner release.

For operator-supplied exports, configure `DMT_DATABASE_URL`,
`DMT_ARTIFACT_S3_BUCKET` and the matching region/prefix/endpoint as above, then run:

```bash
domain-platform import-remote --home data/operator-cache --database-schema decision_workbench --keys-file /operator/workspace-keys.json --project YOUR_PROJECT_ID --study YOUR_STUDY_ID --export retrieved/export --manifest-sha256 YOUR_RETAINED_SHA256 --receipt provider-receipt.json --request-key YOUR_UNIQUE_IMPORT_KEY
```

Supply `DMT_OPERATOR_TOKEN` through your secret mechanism. Use your actual
database schema; `DMT_DATABASE_SCHEMA` supplies its default. Keep the same trusted
authentication configuration as the API. See [operator admission](remote-admission-guide.md#4-stage-and-queue-admission-as-an-authenticated-operator)
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

## Retention, recovery and checks

Do not delete objects solely because a recent upload has no registry row:
publication may be in progress. Shared-object garbage collection and coordinated
database/object recovery require an operator policy. SQLite backup commands do
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
