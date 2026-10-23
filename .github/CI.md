# Continuous integration

[Project overview](../README.md) · [Testing guide](../docs/verification.md)

[checks.yml](workflows/checks.yml) configures checks for pushes and pull requests. It installs the selected Linux and Windows CPU dependency locks, checks dependency consistency, validates public documentation and runs the unit suite. A separate Linux job builds and verifies the CPU application container.

The quick-start jobs create fresh virtual environments on Windows and Linux, install the root [requirements.txt](../requirements.txt), check dependencies and exercise personal workspace startup, the CPU workflow and both launch modes. These jobs keep the [README installation path](../README.md#run-on-your-computer) usable without depending on preinstalled provider SDKs or model packages.

The [continuous journey](../tests/test_complete_journey.py) follows one newly created personal project through import, review, reservation, generation with a fictional localhost teacher, reviewed training-data extension, CPU training, release, prediction and reopening. It checks that evaluation data stays unchanged. Streamlit AppTest supplies the upload at the widget boundary, so this does not test a native file picker or visual accessibility. The teacher and data are fictional; no pretrained weights or paid service are needed.

Quick-start checks also exercise the [four workspace roles](../docs/authentication-guide.md#workspace-credentials--choose-a-workspace-role) and resident-process reuse across separate API request threads. The resident check uses a weight-free framed fixture and verifies shutdown and broker-parent death on Windows/Linux; it does not establish GPU or model quality.

This workflow does not submit paid cloud jobs or qualify every real GPU model. Follow [provider compatibility checks](../docs/verification.md#provider-compatibility-checks), [model container verification](../docs/model-container-guide.md#linux-model-image) and [runtime upgrades](../docs/compatibility-and-upgrades.md) for those separate paths.

The Liquid data jobs install the optional CPU-only [data preparation profile](../requirements/liquid-data.txt) on Windows and Linux, check dependencies and exercise export failure/lineage contracts. They do not fetch model weights or run GPU training; offline fixtures do not establish native model quality.

Use [contribution guidance](../CONTRIBUTING.md) when changing the checks. Credentials, private data, generated caches and local execution receipts belong outside the public repository.

The PostgreSQL job starts a digest-pinned disposable server, installs the
[optional metadata driver](../requirements/postgres.txt) and runs the
[database contracts](../tests/test_postgres.py). Cases use randomly named test
schemas and fictional data. They include actual API/worker/CPU release behavior,
independent process claims and SQL/transaction failure checks; they do not
establish shared artifact storage or production database operations. Follow the
[PostgreSQL guide](../docs/shared-artifacts-guide.md#postgresql-metadata) for configuration and limits.

That job also installs the pinned AWS SDK and exercises
[shared artifacts](../tests/test_shared_artifacts.py): actual PostgreSQL CPU
workflows across independent local caches, content integrity, conditional S3 SDK
contracts and multipart cancellation/completion. The object conformance backend
uses local files; SDK responses are fictional. It does not contact a paid bucket
or establish native model/cloud operational qualification.

The PostgreSQL job also runs [separate-node workflow checks](../tests/test_enterprise_flows.py): hosted contracts and quota accounting, all five managed providers handing verified exports to a different node, concurrent intake, cancellation, lost-response recovery and the authenticated operator CLI. It uses disposable PostgreSQL schemas, a local object transport and fictional HTTP/SDK responses; it makes no paid calls or model downloads.

The preparation jobs additionally install [the optional enterprise UI client](../requirements/enterprise-ui.txt). [Enterprise authentication checks](../tests/test_enterprise_auth.py) use a fictional loopback OIDC issuer, real signed tokens and the installed Streamlit/Authlib callback routes, together with Streamlit AppTest and the API. They test PKCE, nonce/state/replay refusal, role/account changes and atomic token revocation without an external identity-provider account.

The PostgreSQL job also runs [shared-object retention checks](../tests/test_artifact_retention.py): publication fencing, retained roots, stale plans, interruption/reconciliation and version-specific S3 SDK requests. Object bytes and credentials are fictional; the tests do not delete cloud-account data.

[Local staging checks](../tests/test_local_stage_cleanup.py) verify OS leases, producer-process death, stale plans, ownership/path fences and backup compatibility with tiny fictional files. They run without provider credentials or model weights; ordinary preparation jobs cover both operating systems.

[Project lifecycle checks](../tests/test_project_lifecycle.py) exercise owner confirmation, active-work refusal, concurrent archival/imports, real CPU prediction fencing, recovery and the browser controls. The PostgreSQL job checks state visibility and write refusal on a separate node.

[Project deletion checks](../tests/test_project_deletion.py) exercise confirmed database erasure, rollback, stale-request fencing, shared-record and tenant isolation, retained quotas, linked schema intakes, recovery and real browser controls. PostgreSQL checks deletion across independent nodes; no provider compute or model download is required.

[Cache retention checks](../tests/test_cache_retention.py) exercise node-user leases, cold reload, release/input preservation, CLI preview/apply and partial quarantine recovery. PostgreSQL provides real reference/fencing behavior; S3 uses SDK metadata-response fixtures without downloading model weights.

The PostgreSQL job also installs the Azure and Google Cloud application profiles.
[Azure storage contracts](../tests/test_azure_artifacts.py),
[Azure shared-workspace workflows](../tests/test_azure_shared_workflow.py) and
[GCS storage contracts](../tests/test_gcs_artifacts.py) and
[GCS shared-workspace workflows](../tests/test_gcs_shared_workflow.py) check
version/generation-pinned publication, recovery and retention.
[Azure deployment checks](../tests/test_azure_deployment.py)
and [GCP deployment checks](../tests/test_gcp_deployment.py) exercise secret loading,
role separation and generated infrastructure configuration. The container job builds
both cloud application images and validates the GCP Terraform configuration with
mocked provider plans. These checks do not allocate paid cloud resources or prove
account-specific IAM, networking, quotas or billing.
