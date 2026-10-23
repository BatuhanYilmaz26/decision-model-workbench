# Host Decision Model Workbench on Google Cloud

[Application hosting](README.md#application-hosting) · [Project overview](../../README.md) · [Google GPU training](../remote-training/gcp.md#direct-submission)

Run the API and Streamlit browser as Cloud Run services and the durable worker as a Cloud Run worker pool. Cloud SQL stores workspace metadata; private versioned Cloud Storage buckets hold artifacts and recovery copies. Secret Manager supplies approved configuration through each role's service account.

The generated Terraform configuration also includes finite initialization jobs, managed HTTPS endpoints, deletion protection and a database-storage alert. **Applying it creates billable resources**, including continuously running worker/API instances and Cloud SQL. Review capacity, availability, storage retention and notification settings before deployment.

## Prepare your project and operator environment

Install the [local application](../../README.md#run-on-your-computer), Docker with Linux/amd64 support, Google Cloud CLI and Terraform. The configuration pins Google provider `7.22.0`; Terraform `1.14.0` supports the included offline tests. Use a billing-enabled project and a region supporting Cloud Run worker pools, Cloud SQL PostgreSQL 18 and your selected database tier.

The operator must be able to enable APIs, manage the listed resources and IAM bindings, act as the application service accounts, and read the administrator secret. Use your organization's approved roles. App workspace roles do not grant Google Cloud permissions.

Authenticate the CLI and configure [Application Default Credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials) for Terraform. For an interactive developer environment:

```text
gcloud auth login
gcloud auth application-default login
```

Replace `PROJECT`, `REGION`, `REPOSITORY` and other uppercase placeholders throughout this guide:

```text
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com storage.googleapis.com artifactregistry.googleapis.com monitoring.googleapis.com iam.googleapis.com --project=PROJECT
gcloud beta services identity create --service=run.googleapis.com --project=PROJECT
```

The second command ensures the Cloud Run service agent exists before assigning its registry access. It uses the CLI's service-identity command, not a second Terraform provider. Create or select an Artifact Registry Docker repository in the deployment project and region. [Cloud Run service agents](https://docs.cloud.google.com/run/docs/reference/iam/roles), [Artifact Registry repositories](https://docs.cloud.google.com/artifact-registry/docs/repositories/create-repos).

This recipe exposes HTTPS service endpoints with **application authentication**, with Cloud Run invoker IAM checks disabled. Your organization policy must permit that ingress model. Browser and backend service accounts are separate; only the backend and bootstrap roles may connect to Cloud SQL. [Cloud Run public access](https://docs.cloud.google.com/run/docs/authenticating/public).

## Build and publish an immutable image

From the repository root:

```text
docker build --platform linux/amd64 -t dmt-base:release .
docker build --platform linux/amd64 --build-arg BASE_IMAGE=dmt-base:release -f docker/deployment/Dockerfile -t dmt-managed:release .
docker build --platform linux/amd64 --build-arg BASE_IMAGE=dmt-managed:release -f docker/deployment/Dockerfile.gcp -t dmt-gcp:release .
gcloud auth configure-docker REGION-docker.pkg.dev
docker tag dmt-gcp:release REGION-docker.pkg.dev/PROJECT/REPOSITORY/workbench:RELEASE
docker push REGION-docker.pkg.dev/PROJECT/REPOSITORY/workbench:RELEASE
gcloud artifacts docker images describe REGION-docker.pkg.dev/PROJECT/REPOSITORY/workbench:RELEASE --project=PROJECT --format="value(image_summary.digest)"
```

Use the returned digest in `REGION-docker.pkg.dev/PROJECT/REPOSITORY/workbench@sha256:HEX_DIGEST`. The template refuses mutable tags. The extension adds small hash-pinned storage/auth clients; it contains no foundation-model weights or GPU runtime.

## Prepare private configuration

Create an initial owner key, save the displayed bearer token in your password manager, then create a new private configuration bundle:

```text
domain-platform provision-key --file data/config/operator-keys.json --tenant your-team --actor owner --role admin
python -m decision_workbench.platform.deployment_gcp --keys-file data/config/operator-keys.json --output data/config/gcp
```

The bundle contains `backend.json`, `database-admin.json` and `database-app.json`; output contains paths only. The Cloud SQL administrator is `postgres`; the bootstrap creates the distinct `workbench_app` login and its owned workspace schema. Keep these files private and out of Git. Existing output directories are refused.

Create the secrets using the files, without printing their contents:

```text
gcloud secrets create workbench-backend --project=PROJECT --replication-policy=automatic --data-file=data/config/gcp/backend.json
gcloud secrets create workbench-database-admin --project=PROJECT --replication-policy=automatic --data-file=data/config/gcp/database-admin.json
gcloud secrets create workbench-database-app --project=PROJECT --replication-policy=automatic --data-file=data/config/gcp/database-app.json
gcloud secrets versions list workbench-backend --project=PROJECT
```

Inspect the actual enabled version numbers for all three secrets and use those numbers in the Terraform variables. For an existing secret, add an approved new version with `gcloud secrets versions add NAME --project=PROJECT --data-file=FILE`; do not replace a running database credential without coordinating its database login.

The backend accepts the same [typed provider, teacher, cloud and SSO configuration](aws.md#prepare-service-private-configuration) as the other managed deployments. Include only approved profiles and credentials. Secret Manager payloads are limited to 64 KiB. The startup adapter validates the returned payload's CRC32C before loading bounded JSON. [Access API](https://docs.cloud.google.com/secret-manager/docs/reference/rest/v1/projects.secrets.versions/access), [integrity checks](https://docs.cloud.google.com/secret-manager/docs/data-integrity).

For optional OIDC, create a separate `workbench-ui` secret containing only `secrets_toml` in the [enterprise sign-in format](../authentication-guide.md#organization-sign-in). Set `ui_auth_mode=oidc` and its exact version after registering the deployed HTTPS callback. Workspace-key mode needs no UI secret or UI secret grant.

## Generate and review the infrastructure

```text
python -m decision_workbench.platform.gcp_deployment --output data/deploy/gcp/main.tf.json
```

Copy [the variable example](../../examples/deployment/gcp-variables.example.json) into `data/deploy/gcp/private.tfvars.json`. Replace every project, registry, image and secret-version value. Keep `deploy_services=false` initially. Bucket names derive from your project/prefix and must be globally available.

**Terraform state and saved plans contain the database administrator password.** Sensitive-field redaction does not encrypt state. Keep the deployment directory on protected operator storage; for a team, configure an access-controlled encrypted remote backend with locking before the first apply. The generated configuration uses local state until you add a backend. State, plans, variable files and `.terraform/` are excluded from Git and Docker contexts. [Sensitive Terraform data](https://developer.hashicorp.com/terraform/language/manage-sensitive-data), [GCS backend and locking](https://developer.hashicorp.com/terraform/language/backend/gcs).

```text
terraform "-chdir=data/deploy/gcp" init
terraform "-chdir=data/deploy/gcp" validate
terraform "-chdir=data/deploy/gcp" plan "-var-file=private.tfvars.json" "-out=infrastructure.tfplan"
```

Review the plan before applying that exact saved plan:

```text
terraform "-chdir=data/deploy/gcp" apply infrastructure.tfplan
terraform "-chdir=data/deploy/gcp" output
```

The first phase creates persistence, identities, grants and initialization jobs, while API, worker and browser remain absent. Allow IAM propagation before retrying a failed job.

Cloud SQL uses PostgreSQL 18, 32 GiB initial storage, automatic growth, seven retained backups and point-in-time recovery. Availability defaults to `ZONAL`; choose `REGIONAL` for supported HA and its additional cost. The instance has a public IP but **requires the Cloud SQL connector**; the template adds no authorized networks for direct database access. The managed connector authenticates the workload and encrypts the database transport. Libpq connects only to its local `/cloudsql/...` Unix socket. [Cloud Run database connections](https://docs.cloud.google.com/sql/docs/postgres/connect-run).

Live and backup buckets have uniform access, public-access prevention, versioning and seven-day soft delete. Normal backend access is limited to read/create in the live bucket. Recovery and cleanup need separate operator permissions; see [GCS artifacts and staging costs](../shared-artifacts-guide.md#google-cloud-storage).

## Initialize, then start the application

Run the output `bootstrap_job` and wait for success:

```text
gcloud run jobs execute BOOTSTRAP_JOB --region=REGION --project=PROJECT --wait
```

Then run output `operator_job` and require success. Its initial arguments publish authorization revision `0`:

```text
gcloud run jobs execute OPERATOR_JOB --region=REGION --project=PROJECT --wait
```

Inspect the execution and Cloud Logging if either fails. Do not enable services before both complete. Bootstrap does not silently rotate an existing application database password.

Set `deploy_services=true`, create and review another saved plan, then apply it. Open output `web_url`, select **Shared workspace**, enter output `api_url` and the bearer token saved earlier. Follow [the first-project walkthrough](../app-guide.md#screen-manual--first-project-walkthrough) through CPU training, release and prediction. Confirm `api_url/ready` and workspace operations before enabling external teachers or GPU jobs.

Defaults keep two API instances, one browser instance and one worker instance. The worker has 2 vCPU/4 GiB and no public HTTP endpoint. Worker pools use manual positive instance counts rather than waiting for HTTP traffic; adjust `worker_replicas` within the supported bounds. [Cloud Run worker pools](https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run).

The browser uses one instance with session affinity. Cloud Run request/WebSocket timeout is set to one hour; reconnect if a browser session expires. Project data persists in PostgreSQL; unfinished files and local caches are ephemeral. This image supports CPU baseline and configured hosted/remote-training workflows. Native foundation-model execution still needs [matching runtimes and hardware](../setup.md#configuration--configure-model-training-and-serving); [Google GPU training](../remote-training/gcp.md#application-managed-training) remains a separate workflow.

## Monitor, update and rotate

Use API `/ready`, [workspace operations](../shared-workspace-operations-guide.md), Cloud Logging, job execution history and worker-pool instance state. A Monitoring alert triggers above 80% database-disk utilization; configure existing `notification_channels` for actual notification delivery and confirm them in your account.

For a read-only status report, override the operator job's arguments:

```text
gcloud run jobs execute OPERATOR_JOB --region=REGION --project=PROJECT --args=status --wait
```

For approved access changes, add a backend secret version, update `backend_version` and deploy the coordinated configuration revision. Publish the updated shared authorization using its current expected revision:

```text
gcloud run jobs execute OPERATOR_JOB --region=REGION --project=PROJECT --args=publish-auth,--expected-auth-revision,CURRENT_REVISION --wait
```

Restrict job execution/override permissions to infrastructure operators. Bootstrap can read the administrator secret; these permissions exceed ordinary tenant administration. Stale authorization revisions are refused, while identical publication can be retried safely. [Job execution overrides](https://docs.cloud.google.com/run/docs/execute/jobs).

Provider configuration and secret versions load at process startup. Updating a secret alone does not replace the pinned version or refresh existing process files. Drain work and update image/configuration together. Cloud Run's shutdown window can interrupt a long-running job; inspect uncertain provider operations before resubmission. Keep the API and worker on the same approved source/image. [Container runtime shutdown](https://docs.cloud.google.com/run/docs/container-contract).

Rotate database passwords and their matching Secret Manager versions together. `admin_version` also controls the password in the infrastructure configuration; keep it current after an intentional administrator rotation. Roll back by restoring an approved image/configuration and reviewing a coordinated plan; this does not undo database changes, authorization publications or external calls.

`deploy_services` is an initial creation gate, not a drain switch. Setting it false after deployment plans removal of application resources and encounters their deletion protection. Keep it true for updates. Use an approved maintenance procedure to pause submissions, drain work and control service/worker instances deliberately.

## Recover or remove resources

Use [coordinated recovery](../recovery-guide.md#shared-workspace-recovery) with the [GCS live/vault configuration](../shared-artifacts-guide.md#google-cloud-storage--back-up-and-restore) from an authorized operator environment connected to Cloud SQL. Database backups alone do not contain model objects. Keep recovery copies and their identities separate from ordinary application access.

Restore into a fresh schema, home and object prefix, then set `database_schema` and `artifact_prefix` to those restored values before exposing replacement services. When rebuilding Terraform management around retained resources, import them into the approved state rather than attempting to recreate or destroy their buckets/database. Publish current trust/revocation policy and verify cold predictions and tenant isolation before restoring traffic.

The configuration protects its database and buckets against accidental destruction and enables provider deletion protection on application resources. Deliberate removal requires a reviewed change to those protections, a drained workspace and verified recovery data. Existing Artifact Registry, secret versions, soft-deleted objects, logs, remote Terraform state and backups remain separately managed and may continue to incur charges.

Before production use, validate deployed IAM, networking, HTTPS, billing/quotas, model quality, capacity and cold recovery in your account. Provider-schema validation, mocked Terraform plans and local SDK/PostgreSQL/container checks establish repository behavior; they do not establish a live Google Cloud deployment.
