# Store shared model artifacts in Google Cloud Storage

[Documentation](README.md) · [Shared artifacts](shared-artifacts-guide.md) · [Coordinated recovery](shared-recovery-guide.md)

Use Cloud Storage for immutable model exports shared by PostgreSQL-backed API and worker instances. Each node keeps a verified local cache; the bucket holds the durable artifacts. This configuration does not run training on a GPU or turn an instance's local files into shared storage.

## Prepare a private, versioned bucket

Choose a bucket in your application's region. Use a dedicated bucket without dots in its name, uniform bucket-level access, public-access prevention and Object Versioning. Replace the uppercase values below:

```text
gcloud storage buckets create gs://ARTIFACT_BUCKET --project=PROJECT --location=REGION --uniform-bucket-level-access
gcloud storage buckets update gs://ARTIFACT_BUCKET --public-access-prevention --versioning
```

Bucket names are globally unique. Enable and verify versioning before using retention or recovery. Soft delete, old generations and recovery copies can continue to incur charges; they are separate from the application's reviewed deletion history. [Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning), [soft delete](https://docs.cloud.google.com/storage/docs/soft-delete).

Give the API/worker service account `roles/storage.objectCreator` and `roles/storage.objectViewer` **on the live bucket**, rather than a project-wide storage administrator role. Normal publication needs object creation and reading, without object deletion or bucket administration. Use the same approved identity on nodes that share this workspace. Tenant isolation inside that shared service remains enforced by the application.

Recovery and retention operators need additional scoped permissions: `storage.buckets.get` to inspect versioning; `storage.objects.get`, `storage.objects.list` and `storage.objects.create` on the relevant source/destination; and `storage.objects.delete` only where reviewed cleanup is authorized. Define a custom operator role or use your organization's established access policy. Keep backup identities independent from ordinary application access. [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles).

## Install the storage profile and configure credentials

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

Start the services through your existing shared-workspace deployment. Train and release the CPU baseline first, then verify a cold node can warm the registered export and make the same predictions. Follow [shared cache warming](shared-artifacts-guide.md#train-release-and-warm-a-cache) for native model exports; the GCS transport uses the same verified-cache commands and lifecycle.

## Publication and storage costs

The adapter verifies source size and SHA-256, uploads to a unique staging object, verifies the bytes consumed by the SDK, then copies that exact generation into its final immutable name. Both upload and final publication use a create-only generation precondition. An identical retry checks existing bytes; different content cannot replace the final object.

Uploads use CRC32C and bounded resumable chunks for larger files. Downloads resolve an exact generation before reading ranges, so a progressive read cannot combine different object versions. Recovery copies stay inside Cloud Storage rather than downloading foundation-model weights through the operator computer. [Generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions), [Python Blob operations](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob).

**Staging objects temporarily add storage usage.** They remain under the workspace's object prefix as unreferenced objects, including after successful publication. Ordinary application identities cannot delete them. Use the [reviewed retention workflow](shared-object-retention-guide.md) with an authorized operator to remove sufficiently old unreferenced generations. Choose retention intervals and cost budgets accordingly. Bucket soft-delete policy can retain deleted generations beyond application cleanup; do not add broad lifecycle rules that delete registered models or recovery copies.

## Back up and restore

Prepare an independent versioned backup bucket, with the same privacy controls, then configure:

| Variable | Value |
|---|---|
| `DMT_BACKUP_GCS_BUCKET` | Independent recovery bucket |
| `DMT_BACKUP_GCS_PROJECT` | Recovery client's project |
| `DMT_BACKUP_GCS_PREFIX` | Recovery prefix, such as `vault` |

Run the existing [coordinated backup, verification and restore commands](shared-recovery-guide.md). Both live and backup stores must use GCS for this server-side copy path. Different buckets are supported when the operator identity has the required access; overlapping prefixes in one bucket are refused.

The recovery manifest pins generation, ETag, size and SHA-256. Restore into a fresh PostgreSQL schema, home directory and object prefix. Preserve the required source generations and current trust policy until cold prediction, role isolation and recovery checks pass in your environment.

For orphan cleanup, inventory is bounded and deletion targets an exact generation with a metadata precondition. A new replacement generation is not deleted by an older approved cleanup operation. If the operation reports an uncertain result, inspect and reconcile the saved operation before retrying.

Before adopting this configuration, verify ADC permissions, service connectivity, quotas, billing, retention and cold recovery in your account. Local SDK HTTP fixtures and CPU/PostgreSQL workflows establish repository behavior; they do not establish deployed Google Cloud acceptance.
