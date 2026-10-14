# Bring a remotely trained decision model into the platform

[All guides](README.md) · [Project overview](../README.md)

Bring a remotely trained model into the local platform using **frozen study → operator staging → runtime validation → owner release**. Native Laya and Unsloth use four data files; Vega uses eight, including applicability negatives. Choose a [provider workflow](remote-training-guide.md) to train the frozen inputs. Managed cloud controllers accept Laya/Unsloth studies; Vega requires its separate operator route and is rejected by those managed launchers.

Use this workflow **before training**. A pre-existing checkpoint without the frozen study request cannot enter this route by relabeling it or changing its report. Local training remains available through the normal worker. The current transfer route requires an authenticated operator with filesystem access to the workspace; a browser upload of multi-gigabyte weights is not implemented.

## 1. Freeze an owner-approved remote study

For Vega, follow [the eight-file route below](#vega-eight-file-studies-and-independent-admission). Its request, trainer settings and uncertainty measurements differ from the native Laya/Unsloth examples in this section.

Create the project, review data, prepare its immutable four-role snapshot and save release/data-sufficiency criteria. In **Training jobs → Train on a cloud GPU**, select native Laya or Unsloth, model revision and training settings; record the approved provider, region/environment, GPU/instance identity and qualified image digest. Confirm that the provider may receive the domain data, then choose **Prepare remote training study**.

The same action is available through the authenticated API:

```json
{
  "kind": "remote-study",
  "request_key": "support-study-001",
  "config": {
    "snapshot": "YOUR_PREPARED_SNAPSHOT_ID",
    "backend": "laya-native",
    "model": "convaiinnovations/laya",
    "revision": "7b928d828b7b0e022f929d9bd2e44165aa270148",
    "epochs": 1,
    "batch_size": 2,
    "grad_accum": 4,
    "max_length": 256,
    "provider": "aws",
    "region": "us-east-1",
    "gpu": "ml.g5.xlarge",
    "image_digest": "YOUR_REGISTRY/trainer@sha256:YOUR_64_CHARACTER_DIGEST"
  }
}
```

POST this JSON to `/v1/projects/{project}/jobs`. Substitute actual IDs and a complete image digest. These settings illustrate a bounded encoder experiment; they do not establish domain quality, current GPU availability or a sufficient budget. An admin must request preparation. The first local training **or remote-study submission** permanently freezes that project's acceptance criteria, including when the job later fails or is cancelled. Use a successor project to revise the policy.

An ordinary worker handles this preparation without loading GPU packages. It verifies the snapshot and class sufficiency, writes the exact four JSONL files and seals `request.json` plus a deterministic ZIP. The request binds tenant/project, snapshot/schema, owner-policy ID/hash, selected configuration, package Python-source fingerprint and actual file-byte hashes. The fingerprint covers `decision_workbench/**/*.py`; it is not an image signature or a full dependency lock. Matching hashes/report fields establish declared-profile consistency; they do not cryptographically prove that a remote trainer executed those bytes or avoided holdout exposure. Controlled provider execution, attestations and audit remain required for that assurance.

## 2. Download and train the frozen inputs

After preparation succeeds, retrieve the study in the browser or GET `/v1/projects/{project}/remote-studies/{study}/download` with an admin credential. The study ID is the preparation job ID. Keep `request.json` unchanged, preserve all four files and retain their hashes outside the training resource. A recovered workspace can reconstruct the exact ZIP from its immutable database; a corrupted existing archive is refused.

Use the matching packaged source and qualified Linux image. A bare image tag, the old local package version number or a moving Git branch does not establish a matching source profile. Image provenance remains operator-attested until a provider adapter/attestation verifier exists. Freeze and test a separate Linux dependency profile; the executed Windows CUDA profile is not a portable lock.

Add the frozen request to the training command:

```bash
python -m decision_workbench.training.laya_runner \
  --data /job/study/data --out /job/export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 1 --batch-size 2 --grad-accum 4 --max-length 256 \
  --study-request /job/study/request.json
```

Use the **settings actually recorded in your request**, rather than replacing them with this example. For Unsloth select `decision_workbench.training.unsloth_runner` and its recorded rank/full-precision/step settings. Native Laya rejects Unsloth-only options. The trainer checks frozen input hashes, source fingerprint and backend/configuration before downloading/loading the model. Reports record `source_sha256` and `remote_study_sha256`; both are required for admission.

For provider recipes that stage only a `data/` prefix, copy the unchanged request into `data/request.json` and transfer it alongside the four files. Add `--study-request` using the actual mount path:

| Provider | Additional request transfer and flag |
|---|---|
| AWS | Include it in the S3 input channel; `--study-request /opt/ml/input/data/domain/request.json` |
| Azure | Include it in the URI-folder input; `--study-request ${{inputs.domain}}/request.json` |
| GCP | Download/copy `data/request.json` separately from the recipe's `*.jsonl` staging; `--study-request /tmp/dmt-data/request.json` |
| Modal | Include it in the run's Volume data prefix; `--study-request /runs/runs/RUN_ID/data/request.json` |
| RunPod | SCP `request.json` separately from `*.jsonl`; `--study-request /workspace/RUN_ID/data/request.json` |

`load_splits` consumes the four named JSONL files; adding this request file does not change their bytes or split roles. Provider job metadata, secrets and transfer receipts remain separate from training examples. Observe the provider until terminal completion and retrieve the entire model export. Stop paid resources according to its guide even if training/admission fails.

## 3. Retain the export checksum and provider receipt

Retain the SHA-256 of the **source export's** `manifest.json` before copying or extracting it. Verify transfer against that checksum. The current importer accepts an extracted directory, avoiding automatic untrusted archive extraction; the provider's download/extraction procedure must preserve files and reject unsafe archive paths.

Save a separate receipt using exactly these fields:

```json
{
  "provider": "aws",
  "provider_job_id": "YOUR_ACTUAL_TRAINING_JOB_ID",
  "region": "us-east-1",
  "gpu": "ml.g5.xlarge",
  "image_digest": "YOUR_REGISTRY/trainer@sha256:YOUR_64_CHARACTER_DIGEST",
  "status": "succeeded",
  "manifest_sha256": "YOUR_RETAINED_EXPORT_MANIFEST_SHA256"
}
```

Provider/region/GPU/image must match the frozen study. Keep the real provider logs, measured resource usage and billing receipt in the operator's experiment record. This JSON is explicitly **operator-attested**: the platform does not query a provider to prove the job ran, verify its bill or establish a signed supply-chain attestation. Do not present a locally fabricated receipt as cloud acceptance.

## 4. Stage and queue admission as an authenticated operator

Run from the platform environment on the workspace host. Set `DMT_OPERATOR_TOKEN` to a valid admin credential through your operator's secret mechanism. Use the same trusted key/JWT mappings as the API; credentials stay out of command arguments and receipt files.

```bash
domain-platform import-remote \
  --home data/workspace --keys-file /operator/workspace-keys.json \
  --project PROJECT_ID --study STUDY_ID \
  --export retrieved/export --manifest-sha256 RETAINED_SHA256 \
  --receipt provider-receipt.json --request-key support-import-001
```

For enterprise access tokens use the matching trusted `--sso-file` configuration instead of an unrelated key file. Staging checks the initial identity and reauthenticates before durable submission. A server-scoped project, prepared study and frozen owner policy are mandatory. The CLI queues work and prints the job ID; it does not activate a model.

The input directory must be private and stable while copied. The importer rejects linked/nonregular content, unsupported executable/pickle formats, extra/missing/modified files, wrong schema/backend/model/data/source/request identity and mismatched receipts. Staging is bounded to 2,048 files, 32 GiB and ten minutes. Manifest JSON is capped at 1 MiB and report JSON at 16 MiB before parsing. It copies into a fresh private workspace location, then verifies the copy. Failed staging removes that partial copy. Identical idempotency keys/receipts return the same job; changed requests conflict.

Inspect the current job before retrying an interrupted command. A successful queued intake remains durable if the operator disconnects. Incoming copies and private logs currently require operator retention/cleanup; automated cleanup, distributed storage and browser weight transfers require separate operational validation.

## 5. Qualify, review and release

Run the worker in the qualified model environment. Admission acquires the same GPU lease as local training, pauses resident inference through the existing scheduler, launches a bounded offline model subprocess and preserves private logs. The local checkpoint loader does not accept executable model configuration. Qualification loads the actual export, measures **all reserved validation/calibration/test records**, then loads it again and checks validation probabilities and decisions at the fixed backend tolerance. It does not refit calibration or change training/holdout membership.

The supplied validation/test metrics are replaced by measured local metrics. The original report is retained as `training_report.json`; the resulting portable bundle receives a new exact manifest. Registration preserves both source and admitted checksums, study/job identities, frozen policy and provider attestation. Failed/cancelled or expired-owner jobs cannot register an eligible model. An admission failure retains the incoming source for operator investigation and removes its unregistered model copy.

A successfully admitted model is **inactive**. Review its metrics and use the existing owner release action; frozen acceptance gates, full artifact verification, serving preload and atomic release revision still apply. Its API uses the same typed answers, probability validation, authentication and rollback path as local models. A permissive mechanical-fixture policy is not enterprise-quality acceptance.

Generic optimizer resume, automatic provider retries, signed provider attestations and multi-host storage remain separate requirements.

For application-managed submission and artifact retrieval, follow [AWS training](managed-cloud-training.md) or [GCP training](managed-gcp-training.md). Both hand the export to this independent validation and release workflow. Check the selected account and image using [provider compatibility checks](verification.md#provider-compatibility-checks).

[Managed Azure training](managed-azure-training.md) also retrieves the named job's Blob export and queues the same validation and owner-release workflow.

For managed RunPod studies, follow the [v2 Pod workflow](managed-runpod-training.md). Output-ready/EXITED/ERROR is not terminal controller evidence: require bound termination, preserve unknown creation/deletion outcomes, and keep network-volume result/receipt identities through recovery. Volume content remains unversioned writer attestation; independent model admission and owner release still apply.

## Vega eight-file studies and independent admission

Vega uses `domain-remote-vega-study-v1`. Start with the [Vega guide](vega-guide.md), authenticated positive and negative review, paired reservations and explicit owner applicability limits. In Training jobs select Vega and its paired negative snapshot, then prepare a remote study using the approved provider/resource/image metadata. Preparation creates no compute and performs no data upload. Missing negatives or a required combined semantic audit refuse preparation. The exact pinned engine/backbone, sixty-family preflight, all eight input hashes and frozen owner policy are retained. With a [combined semantic reservation](semantic-data-guide.md#combined-positive-and-applicability-audits), both local and operator studies consume the derived positive copies and paired negatives; preserve that complete archive rather than substituting the original positive-only export.

The downloaded ZIP contains `request.json`, four positive `data/{role}.jsonl` files and four negative `data/gate_{role}.jsonl` files. The eight-file format has no four-file `data/manifest.json`. Preserve every byte. It is deliberately refused by the current managed Laya/Unsloth cloud runner. Use an independently qualified Vega environment and the unchanged request; do not pass `--local-study-request`, which is reserved for a supervised local application worker.

For a study prepared with one epoch, batch sixteen, rank four, six maximum optimizer steps and offline assets, the matching command is:

```bash
python -m decision_workbench.training.vega_runner \
  --data /job/study/data --out /job/export \
  --study-request /job/study/request.json \
  --epochs 1 --batch-size 16 --rank 4 --max-steps 6 --offline
```

Use every setting recorded in your actual request, including nondefault learning rate and gate weight. Offline mode requires both complete pinned assets already cached; choose it before freezing the study. Normal standalone execution owns its resource lease. Model/input/options/source drift refuses before model imports. No gradient-accumulation or native encoder options apply to this profile. The default engine/backbone identity is the separately qualified pair documented in the Vega guide.

Retrieve the complete engine, backbone, domain adapter, calibration, metadata and report, retain the manifest checksum, and use the operator import command above with the matching provider receipt. A job receipt remains operator-attested; source hashes cannot prove remote execution or holdout nonexposure. The importer verifies positive and negative provenance, stages bounded ordinary files and queues independent admission. Browser weight upload is still unavailable.

The admission worker owns the shared GPU lease and loads the staged export in offline mode. It remeasures validation/calibration/test decisions, positive accepted fractions and per-question negative gate opening using the exported adapter threshold. The supplied applicability metrics are replaced. A second load compares positive and negative probabilities, decisions, abstention, conformal sets, adapter selection, unbound/upstream refusal and gate values. It retains `training_report.json` and seals a new manifest whose report binds the original source export and frozen study. Admission measures the existing calibration; it does not refit on the locked test.

Owner release still enforces both ordinary quality and positive-coverage/negative-applicability limits. The registered version replays its authenticated reservations and admission identities during release and recovery. Qualification of this route is separate from actual cloud/image execution and representative operational model quality.
