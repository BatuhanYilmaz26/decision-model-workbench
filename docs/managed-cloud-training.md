# Managed cloud training through the platform

Submit a frozen decision-model study to AWS SageMaker AI through the browser, authenticated API and independent worker. The controller stages inputs in S3, records the provider job and retrieves its export for local validation. Install Boto3/Botocore **1.43.113** in the control environment and prepare a compatible training image. For direct submission, use the [AWS walkthrough](remote-training/aws.md).

## Operator configuration

Install the AWS extra in the API/worker environments that use this feature. GPU admission still needs the separately qualified model environment; an API container is not a training image.

```bash
python -m pip install -e '.[ui,platform,aws]'
python -m pip check
```

Start from the [illustrative operator profile](../examples/cloud/aws-profiles.json). Replace every example resource, tenant, image digest and source hash. The zero hashes deliberately cannot approve a current study. Keep the file operator-owned outside versioned application artifacts and point `DMT_CLOUD_PROFILES_FILE` to it in the API and worker processes. In Compose, put it in the mounted operator config directory and use `/run/config/cloud-profiles.json`. Python entry points read process variables; they do not load `.env.example` automatically.

The profile binds one AWS account, commercial AWS region, same-account ECR digest, training role, S3 bucket/prefix, one GPU instance, volume and maximum runtime. Optional `vpc` contains `Subnets` and `SecurityGroupIds`; optional `kms_key_arn` binds input/output encryption and volume encryption to an approved same-account/region key. GovCloud/China, cross-account images/buckets, distributed jobs, network-isolated/offline weights and automatic spot/resume are not implemented profiles.

Provide **temporary** credentials to the worker using `<credential_prefix>_ACCESS_KEY_ID`, `<credential_prefix>_SECRET_ACCESS_KEY` and, when applicable, `<credential_prefix>_SESSION_TOKEN`. The example prefix is `DMT_CLOUD_AWS`. The selected adapter does not fall back to ambient AWS credentials or instance metadata, and ignores globally configured/per-service SDK endpoint overrides. Automatic credential refresh, IAM-role/default-chain profiles and enterprise secret-store integration remain required work. Rotate the selected environment values without changing the frozen public resource profile. API/UI requests never carry provider secrets; the launcher excludes `DMT_CLOUD_*` variables from the web process. STS checks the selected credential account before external operations. [GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html)

The submitter needs scoped `sts:GetCallerIdentity`, `sagemaker:CreateTrainingJob`, `DescribeTrainingJob`, `StopTrainingJob`, the applicable `iam:PassRole`, and input/output S3 operations. The execution role separately needs the approved ECR, S3, logs and optional KMS/VPC permissions. Provision resources and quotas under your organization's policies; this feature does not create buckets, roles, images or networks. Review the named resources, data residency, access and tariff before owner approval. [SageMaker training roles](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html)

## Qualify the training image

The ECR image must contain this exact `decision_workbench` package, the selected actual native Laya/Unsloth trainer dependencies and a compatible GPU runtime. Run the package hash command inside both the local source environment and the final image; set the profile's `source_sha256` to their matching value:

```bash
python -c 'from decision_workbench.training.provenance import source_fingerprint; print(source_fingerprint())'
```

The fingerprint binds package source bytes. Record the image digest and check CUDA, dependencies, checkpoint loading, peak memory, training, export/reload and archive packaging on the selected Linux GPU. A package-source change needs a new image/profile and frozen study; preserve the original provenance of existing exports. The source fingerprint alone does not authenticate the image or its build.

AWS invokes `python -m decision_workbench.training.cloud_runner` with the approved archive and request hashes. The S3 File channel mounts `study.zip` at `/opt/ml/input/data/study/study.zip`. The runner accepts exactly `request.json`, `data/manifest.json` and the four split JSONL files, verifies all bytes/source/snapshot/schema, and constructs the actual native/Unsloth command from the unchanged frozen request. Native training explicitly uses CUDA; Unsloth retains its own GPU checks. The export goes to `/opt/ml/model`. A preflight-only mode checks the same inputs/configuration without training:

```bash
python -m decision_workbench.training.cloud_runner --help
```

The runner requires `--archive`, `--archive-sha256`, `--request-sha256`, a new `--work` directory and an empty/new `--out` directory; add `--check-only` for preflight. Obtain the two hashes from the immutable study entity, not a new unreviewed data export. Training-side report/manifest qualification remains mandatory. [Container entrypoint contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html), [managed paths](https://docs.aws.amazon.com/sagemaker/latest/dg/model-train-storage-env-var-summary.html)

## Owner workflow

1. Follow [frozen-study preparation](remote-admission-guide.md). Select AWS and exactly the region, GPU instance and ECR digest in the operator profile. Freeze owner criteria and prepared data before remote training.
2. In **Train on a cloud GPU**, choose the frozen study and managed profile. Review the public resources and limits; explicitly approve both data transfer and paid compute. **Approve cloud run** creates a local immutable approval only.
3. **Submit approved paid training** queues the worker action. With configured AWS credentials, this uploads domain data and creates paid compute in the selected account.
4. **Observe the same provider job** checks the frozen job/account/resource/input/output identities and records status plus reported training/billable seconds. A completed local control action does not mean training finished. Poll manually, confirm the provider's terminal state and reconcile its billing; this version has no unattended watchdog.
5. **Request provider stop** first checks job identity, then calls the provider stop API. Capacity remains held until a matching terminal status is observed. Cancelling a local worker/control job does not stop provider compute.
6. Once a matching **Completed** status is observed, **Retrieve and queue independent admission** downloads the returned approved S3 object, safely extracts it, validates provenance and queues the existing `remote-import` worker. Review the admission job in Training jobs. The model stays inactive until independent runtime/held-out checks and a separate owner release pass.

The API exposes `GET /v1/cloud-profiles`, owner `POST /v1/projects/{project}/cloud-runs`, paged `GET` of that collection, scoped run `GET`, and `POST /v1/projects/{project}/cloud-runs/{run_id}/actions`. All routes derive tenant/role from the configured authentication. Creation requires `study`, `profile_id`, literal-true `data_approved` and `compute_approved`, and an 8–128-character `request_key`. Action bodies contain `action` (`submit`, `observe`, `stop`, `abandon` or `retrieve`), the current integer `revision`, and a request key. Writes recheck authentication before durable submission; queued work also checks current operator profile availability. Stale queued actions fail without calling AWS.

## Interruption and spending limits

One frozen study has one logical cloud run and fixed `dmt-<run_id>` provider job name. Upload/create attempt markers are committed before the relevant external call. SDK request retries are disabled. An uncertain create, worker crash or cancellation retains the attempt and capacity; another submit is refused. Observe the same job. Never allocate a replacement solely because a request timed out. If upload alone was attempted and compute creation never started, the owner can abandon that run locally; uploaded objects still need retention/cleanup. Once compute creation was attempted, abandonment cannot waive its unresolved state.

SageMaker's optional `RetryStrategy.MaximumRetryAttempts` has a minimum of **1** in the pinned SDK; setting it to zero is invalid. The adapter omits the optional strategy and rejects an observed unapproved retry policy, retained warm-pool capacity or distributed/training-plan resources. It starts on-demand, requests one GPU instance and applies the profile maximum runtime. These controls do not establish a dollar spending cap or exact provider cessation deadline. [RetryStrategy](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_RetryStrategy.html), [CreateTrainingJob](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html)

Run-count and unresolved-capacity accounting is transactional across API/worker processes in **one local workspace**, grouped across tenant/profile aliases by provider, AWS account/GCP project number and region using the strictest configured limits. Unresolved attempts do not age out. These are local submission limits; other workspaces/accounts/direct AWS jobs, tariffs, upload/storage/log costs and provider-side retries/billing are outside the ledger. Account-wide budgets and representative load/ledger-growth measurements remain requirements.

Immutable approval and hash-chained state histories are validated during [backup/restore](recovery-guide.md). Recovery interrupts copied local jobs but preserves unresolved external cloud state. Restore profiles/credentials separately and observe the same job before taking any new external action. Changing/revoking a public profile prevents operations through this adapter; inspect/stop the original resource through an authorized operator path and retain evidence. Automated profile-change recovery/settlement is pending.

## Artifact bounds and admission boundary

Retrieval binds the observed S3 URI to the exact approved bucket/run/job output path, uses `ExpectedBucketOwner`, and pins a returned VersionId or conditional ETag before bounded download. It records actual downloaded SHA/size; these are transfer evidence rather than an independent signed producer attestation. Input upload requests an explicit SHA checksum, conditional create and approved encryption. [PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html), [GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)

The configured download/expanded-model ceiling is at most 32 GiB. Extraction allows at most 2,048 entries and 16 path levels, with a ten-minute checked deadline. These checks and socket timeouts do not guarantee wall-clock cancellation. Free-space admission reserves three artifact bounds plus 1 GiB. Apply retention and cleanup to partial transfers and successful staging; cleanup is not automatic.

The extractor accepts short-name regular-file/directory USTAR and compatible GNU headers. It rejects links, devices, sparse/PAX/long-name extensions, traversal, duplicate paths, excessive sizes and unsupported/executable model files. Check a returned SageMaker archive against this format before a production rollout; an unsupported archive is refused. [SageMaker output handling](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html)

Cloud history retains provider observations. Import receipts record operator attestations rather than signed provider or image-origin proofs. The importer verifies the frozen study, configuration and export, then reloads the model on held-out data. Use [provider compatibility checks](verification.md#provider-compatibility-checks) to validate the account, returned archive, billing and resource shutdown. [API reference metadata dated 11 October](../research/cloud-aws-sources-2026-10-11.json)

For other providers, use the managed [Azure](managed-azure-training.md), [GCP](managed-gcp-training.md), [Modal](managed-modal-training.md) or [RunPod](managed-runpod-training.md) guides. Each has its own identity, storage and shutdown contract.
