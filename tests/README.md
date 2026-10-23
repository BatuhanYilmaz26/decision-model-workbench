# Tests and compatibility fixtures

[Project overview](../README.md) · [All guides](../docs/README.md)

Follow [testing and compatibility](../docs/verification.md) to install the test environment and run the suite. Tests exercise local contracts and selected runtime behavior; provider fixtures do not establish real cloud execution, model quality or billing.

| Area | What is checked | Related guide |
|---|---|---|
| PostgreSQL metadata | Real disposable database, isolated schemas, independent process claims, typed JSON queries, rollback and actual API/worker/CPU release workflows | [PostgreSQL setup and test command](../docs/postgresql-guide.md) |
| Shared model artifacts | Immutable file objects, atomic caches, cross-cache PostgreSQL release/prediction, conditional S3 SDK requests and multipart cleanup | [Shared artifact setup](../docs/shared-artifacts-guide.md) |
| Data and domain contracts | Import, review, preparation, held-out roles and synthetic lineage | [Data](../docs/data-guide.md), [evaluation](../docs/evaluation-guide.md) |
| Import previews | Whole-file validation, worksheet/mapping selection, preview consistency, atomic append and pending review | [Import workflow](../docs/import-guide.md) |
| Manual examples | Fifty-record append, draft retention after refusal, empty-row handling and strict JSON evidence | [Batch entry](../docs/import-guide.md#enter-examples-without-a-file) |
| Explicit reservations | Group/time policies, fixed historical roles, owner preview/approval and recovery integrity | [Reservation workflow](../docs/reservation-guide.md) |
| Synthetic generation | Server approvals, seed selection, usage/estimate accounting, supervised transport, explicit retry, review and recovery | [Generation workflow](../docs/synthetic-generation-guide.md) |
| Platform services | HTTP authorization, persistence, worker ownership, releases and inference | [Architecture](../docs/architecture.md), [authentication](../docs/authentication-guide.md) |
| Workspace roles | Read/mutation permissions for all four roles, unchanged state after denials, editor training, owner release and tenant boundaries | [Role table](../docs/authentication-guide.md#choose-a-workspace-role) |
| Shared model capacity | Different-workspace exclusion, cancellation, owned-child recovery and CPU independence | [Resource checks](../docs/verification.md#check-shared-model-capacity), [configuration](../docs/setup.md#shared-model-capacity-on-one-host) |
| Resident runtime lifetime | Reuse across independent request threads, orderly shutdown and broker-parent death | [Weight-free lifecycle checks](../docs/verification.md#check-shared-model-capacity) |
| Browser workflows | Guided forms and API-backed state through Streamlit AppTest | [Testing scope](../docs/verification.md) |
| Complete personal journey | One new project, imported/reviewed examples, fixed holdouts, reviewed synthetic extension, CPU training, release, prediction and service reopening | [Run the continuous journey](../docs/verification.md#run-the-continuous-personal-journey) |
| Training and provider adapters | Frozen request construction, export contracts and failure handling | [Training](../docs/training-guide.md), [provider checks](../docs/verification.md#provider-compatibility-checks) |
| Liquid hosted models | Both direct model names, all three primitives, isolated HTTP, study/calibration, browser consent, release/rollback, cold inference and recovery with a fictional provider | [Liquid API workflow](../docs/models/liquid-hosted.md); account access, billing and domain quality need independent checks |
| Perplexity hosted models | Full/Flash v1.2, echoed aliases and retired-name migration, typed requests/responses, failure paths, isolated HTTP, browser consent, study/calibration, release/rollback, restart and recovery using fictional HTTP | [Perplexity workflow](../docs/models/perplexity.md); fixtures do not establish upstream account access, billing or domain quality |
| Liquid native-data export | Role/lineage retention, soft-target refusal, tokenizer/readout boundaries, asset identities and no-overwrite behavior | [Liquid preparation](../docs/models/liquid.md); offline fixtures check mechanics, not GPU training |
| Liquid application workflow | Frozen TRL/Unsloth studies, separate operator-selected interpreters, native backend preservation in browser controls, role/data/config refusal and cancellation priority | [Application training](../docs/models/liquid.md#train-and-release-through-the-localhost-application); fixture checks do not establish GPU/domain quality |
| Liquid training contracts | Completion-only labels, padding, parameter settings, calibration gold/temperatures and approved runtime/config identities | [Local Liquid training](../docs/models/liquid.md#train-with-the-local-trl-runner); actual hardware/model qualification is separate |
| D3 family contracts | Variant/revision isolation, all-shard verification, geometry and storage limits, six-variant/five-provider frozen study combinations | [Family workflow](../docs/models/d3-family.md); larger runtime profiles need separate hardware qualification |
| D3 native/application contracts | Option/soft-target order, review/role lineage, frozen local/five-provider studies, cloud dispatch, owner-selected interpreters, browser controls, cancellation priority, source-only loading and whole-bundle guards | [D3 workflow](../docs/models/d3.md); model execution and domain-quality evaluation are separate checks |
| Recovery and operations | Backup integrity, cold recovery, ownership, monitoring and audit windows | [Recovery](../docs/recovery-guide.md), [audit exports](../docs/audit-export-guide.md) |
| Shared recovery | Consistent PostgreSQL archives, independent artifact copies, installed S3 SDK contracts and cold learned-model restore | [Operator guide](../docs/shared-recovery-guide.md); live S3/account qualification remains separate |
| Shared workspace operations | Two API/worker instances, tenant quota races, CPU studies, storage readiness and scoped failure recovery | [Workload and operator guide](../docs/shared-workspace-operations-guide.md) |
| AWS application hosting | Generated resource and permission contracts, installed Secrets Manager SDK, real PostgreSQL bootstrap/authorization, API/worker wiring and browser credential isolation | [AWS deployment](../docs/application-hosting/aws.md); local checks do not establish deployed AWS compatibility or billing |
| Public repository | Exclusion of private files and reusable source/configuration eligibility | [Contributing](../CONTRIBUTING.md) |

[Fixture documentation](fixtures/README.md) explains the included provider-schema excerpts. Helper transports and process runners in this folder are test infrastructure, rather than production endpoints. Keep fictional test data separate from actual domain records.

Use the [container verifier](../scripts/README.md) for the separate image lifecycle checks. Complete the [domain and provider qualification](../docs/verification.md#provider-compatibility-checks) required by your deployment, and report platform-specific skips separately from executed tests.

For shared workspaces, [test_enterprise_flows.py](test_enterprise_flows.py) exercises hosted policies, five-provider retrieval, independent admission caches, duplicate submissions, cancellation, low disk space and operator CLI configuration. Set `DMT_TEST_POSTGRES_URL` to an explicitly disposable PostgreSQL database; each case owns a temporary schema. Provider traffic and native qualification use fictional fixtures, while database transactions and file transfers run locally. See the [shared artifact guide](../docs/shared-artifacts-guide.md) for setup and limits.

[Enterprise authentication checks](test_enterprise_auth.py) cover the optional OIDC browser entry, signed API access, role/account changes, atomic revocation and real Streamlit/Authlib loopback code exchange. Install [the optional client profile](../requirements/enterprise-ui.txt); the native OIDC/UI cases skip when Authlib is absent. API-only token-lifecycle cases remain available with the platform dependencies. See [enterprise sign-in](../docs/enterprise-sign-in-guide.md).

[Shared-object retention checks](test_artifact_retention.py) use disposable PostgreSQL schemas, fictional immutable object versions and installed S3 SDK response fixtures. They exercise retained model/import roots, young manifests, stale plans, lost publication ownership and resumable version deletion. Read the [operator guide](../docs/shared-object-retention-guide.md) before any real storage cleanup.

[Owned local transfer cleanup](test_local_stage_cleanup.py) exercises actual process-held file locks, abrupt producer death, reviewed removal, partial receipts and backup compatibility. It preserves active/unmarked/registered paths. See [the operator guide](../docs/local-stage-cleanup-guide.md).

[Project lifecycle checks](test_project_lifecycle.py) cover archive/restore permissions, stale revisions, accounting retention, in-flight writes/predictions, backup history integrity and real localhost browser use. The cross-node case requires disposable PostgreSQL. See [the user guide](../docs/project-lifecycle-guide.md).

[Project deletion checks](test_project_deletion.py) cover database deletion and retained accounting, including shared cloud-account quotas, teacher-request sources, logical shared-content preservation, atomic rollback, receipt privacy and browser confirmation. The cross-node case uses disposable PostgreSQL. See [the user guide](../docs/project-deletion-guide.md).

[Cache retention checks](test_cache_retention.py) cover offline node leases, process death, active-release/intake preservation, remote availability, cold reload and interrupted quarantine cleanup. Shared metadata cases use disposable PostgreSQL; S3 availability uses fictional SDK responses. See [cache maintenance](../docs/cache-retention-guide.md).
