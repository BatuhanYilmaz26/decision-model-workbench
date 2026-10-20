# Testing and compatibility

[All guides](README.md) · [Project overview](../README.md)

Select a runtime profile, check your installation and validate the model before release. Installation tests, provider compatibility and domain evaluation answer different questions; complete the checks that apply to your deployment.

## Supported execution profiles

| Profile | Use it for | Check before use |
|---|---|---|
| Local CPU application | Data review, preparation, CPU classifier training, releases and HTTP inference | Credentials, persistence and recovery on one host; SQLite is not a multi-host backend |
| Native Laya and Unsloth | CUDA encoder/head training with four independent data roles | Pinned model environment, GPU memory, calibration and complete export/reload |
| Linux CPU container | API, worker and browser with CPython 3.12.15 on Linux/amd64 | Image build, volume access, service replacement and recovery |
| Linux Vega/semantic container | Vega training and semantic audits with CUDA 12.6 dependencies | GPU/device access, pinned assets, eight-file inputs and authenticated lifecycle checks |
| Hosted APIs and managed cloud controllers | Experimental provider adapters and remote job workflows | Account-specific API behavior, returned artifacts, shutdown and billing |

The detailed dependency constraints are in [requirements](../requirements/README.md). Review [current runtime versions and GPU advisories](compatibility-and-upgrades.md) before installation or upgrades. Use [CPU containers](container-guide.md), [model containers](model-container-guide.md) and [Windows process ownership](windows-process-guide.md) for platform-specific checks.

## Run repository checks

Install the application and the optional control-plane clients used by the test suite in an isolated environment:

```bash
python -m pip install -e ".[ui,platform,aws,azure,gcp,runpod,modal]"
python -m pip check
python scripts/check_repository.py
python -m unittest discover -s tests -v
```

The dependency locks provide a narrower repeatable environment than the version ranges in an editable install. Consult the [profile guide](../requirements/README.md) before changing Python, Torch or provider-client versions.

The unit suite includes HTTP services, SQLite state, worker/process ownership, authentication, storage and browser-widget tests. Cloud calls are fixtures; model contract tests do not download/train every real model. Native Windows job tests skip on Linux, and Linux kernel/descriptor tests skip on Windows. Report executed and skipped cases separately. GitHub Actions configuration is included, but a local pass does not establish a successful hosted CI run.

The [synthetic-generation workflow](synthetic-generation-guide.md) has separate local HTTP/worker and Streamlit form tests for transfer/cost approvals, request identity, training-only seeds, all-or-nothing candidate publication, deadline/cancellation, unknown outcomes, explicit retries and recovery. Their fictional teacher checks transport and lifecycle behavior; it does not measure a generative model's domain accuracy or actual provider billing.

## Run the continuous personal journey

After installing the root [requirements.txt](../requirements.txt), run:

```bash
python -m unittest discover -s tests -p test_complete_journey.py -v
```

This check creates one personal workspace and project through the browser controls, imports fictional examples, reviews a record in the browser and the remaining annotations through the authenticated API, then reserves the four data roles. A fictional localhost teacher proposes one training candidate. The browser reviews it, extends the original training set, trains the CPU baseline, activates a release and makes a prediction. The check then restarts the services and opens the same project and release again. It verifies that generation uses training seeds and that validation, calibration and test records remain unchanged.

The check starts and stops its own temporary API, worker and teacher. It requires no account, pretrained model download or GPU. AppTest injects the upload at the file-uploader boundary; use a browser for file-picker, layout and accessibility checks. Permissive fixture acceptance criteria exercise the release mechanism, not a policy suitable for your domain. The quick-start CI jobs also run this check on Windows and Linux; inspect the corresponding run before claiming a platform passed.

## Check shared model capacity

With the local application dependencies installed, run:

```bash
python -m unittest discover -s tests -p test_shared_resources.py -v
```

These checks use temporary lock files and small owned Python processes. They
exercise exclusion across different workspace roots, cancellation, recovery after
parent death on Windows/Linux, the existing workspace lock and rejection before
launching a competing inference process. A CPU training check runs while the
model resource is held. They do not allocate a GPU or download weights. Configure
the same [shared model lock](setup.md#shared-model-capacity-on-one-host) in all
participants and qualify the actual model image/filesystem before deployment.

## Verify a fresh container

```bash
docker build --platform linux/amd64 --tag domain-decision-platform:local .
python scripts/verify_containers.py --image domain-decision-platform:local
```

The maintained verifier creates its own fictional workspace and removes its owned resources. It checks authentication, real CPU training, release/inference, persistent service replacement and backup/restore. It does not use a configured teacher or cloud account. The [model container guide](model-container-guide.md#verify-authenticated-container-lifecycle) provides a separate GPU route and optional cancellation/parent-crash checks.

## Provider compatibility checks

Hosted and managed cloud adapters are experimental. Automated tests use local HTTP/SDK fixtures to check request construction, failure handling and state transitions. Those tests do not establish real provider job execution, billing, resource shutdown or the validity of returned model weights.

Before using an adapter with enterprise data:

1. Configure the selected account, region, identity and approved image. Check quota, permissions and data-use terms.
2. Run a small study with non-sensitive examples. Inspect the actual request, response model identity or provider job ID, logs and output location.
3. For training, retrieve the complete export, verify its hashes, reload it independently and evaluate the reserved data before admission.
4. Test cancellation and an interrupted operation. Observe the same provider resource until terminal; check retained storage and actual charges.
5. Record the accepted configuration and versions, then repeat the relevant checks after changes.

Direct provider walkthroughs are configurable examples, not account-certified deployments. Use the provider's current documentation alongside the [remote training guide](remote-training-guide.md).

## Validate your domain and deployment

Import-preview tests exercise the complete file through the authenticated API, including all five formats, selected canonical worksheets, missing and soft targets, duplicates, capacity, malformed content and stale append versions. Streamlit AppTest injects files at the uploader boundary to check mapping, preview invalidation and save behavior against a live HTTP service. File-picker selection and rendered tables need a separate browser check.

The guided criteria tests exercise the actual Streamlit application, a running local HTTP service and owner policy persistence. They also cover category/yes-no/rating builders, invalid input, unchanged-policy round trips, project isolation and retired-form refusal. AppTest currently has no table-cell interaction API, so table tests supply the documented data-editor widget state (`edited_rows`, `added_rows`, `deleted_rows`). Review pointer/keyboard behavior in your browser separately. [Streamlit data-editor state](https://docs.streamlit.io/develop/concepts/design/dataframes), [AppTest interfaces](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)

Pagination tests enumerate every public GET route exposing `offset`. Oversized, negative and noninteger values are rejected before route storage work. Supported offsets are 0–2,147,483,647; each endpoint has its own bounded page size. Direct storage/incident page tests also refuse invalid values. These bounds prevent invalid SQL binding and do not establish acceptable latency for deep offsets or enterprise-scale datasets.

Use independently reviewed operational gold to compare rules, a CPU baseline, an unadapted model and an adapted model. Freeze thresholds before test inspection; measure calibration, abstention coverage, critical errors, subgroup behavior and complete workflow cost. Evaluate the exact exported serving runtime and its longest legitimate inputs.

For a cloud run, retain the accepted image/configuration, provider job identity, logs, measured resource usage, complete export hashes, terminal resource observation and actual billing information. Independently reload and evaluate the returned model before admission. Validate enterprise identity, private networking, storage, monitoring, load, backup and rollback in the target environment. [Evaluation](evaluation-guide.md), [remote training](remote-training-guide.md), [deployment](deployment-guide.md)
