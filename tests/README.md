# Tests and compatibility fixtures

[Project overview](../README.md) · [All guides](../docs/README.md)

Follow [testing and compatibility](../docs/verification.md) to install the test environment and run the suite. Tests exercise local contracts and selected runtime behavior; provider fixtures do not establish real cloud execution, model quality or billing.

| Area | What is checked | Related guide |
|---|---|---|
| Data and domain contracts | Import, review, preparation, held-out roles and synthetic lineage | [Data](../docs/data-guide.md), [evaluation](../docs/evaluation-guide.md) |
| Import previews | Whole-file validation, worksheet/mapping selection, preview consistency, atomic append and pending review | [Import workflow](../docs/import-guide.md) |
| Manual examples | Fifty-record append, draft retention after refusal, empty-row handling and strict JSON evidence | [Batch entry](../docs/import-guide.md#enter-examples-without-a-file) |
| Explicit reservations | Group/time policies, fixed historical roles, owner preview/approval and recovery integrity | [Reservation workflow](../docs/reservation-guide.md) |
| Synthetic generation | Server approvals, seed selection, usage/estimate accounting, supervised transport, explicit retry, review and recovery | [Generation workflow](../docs/synthetic-generation-guide.md) |
| Platform services | HTTP authorization, persistence, worker ownership, releases and inference | [Architecture](../docs/architecture.md), [authentication](../docs/authentication-guide.md) |
| Browser workflows | Guided forms and API-backed state through Streamlit AppTest | [Testing scope](../docs/verification.md) |
| Training and provider adapters | Frozen request construction, export contracts and failure handling | [Training](../docs/training-guide.md), [provider checks](../docs/verification.md#provider-compatibility-checks) |
| Liquid hosted models | Both direct model names, all three primitives, isolated HTTP, study/calibration, browser consent, release/rollback, cold inference and recovery with a fictional provider | [Liquid API workflow](../docs/models/liquid-hosted.md); account access, billing and domain quality need independent checks |
| Perplexity hosted models | Full/Flash v1.2, echoed aliases and retired-name migration, typed requests/responses, failure paths, isolated HTTP, browser consent, study/calibration, release/rollback, restart and recovery using fictional HTTP | [Perplexity workflow](../docs/models/perplexity.md); fixtures do not establish upstream account access, billing or domain quality |
| Liquid native-data export | Role/lineage retention, soft-target refusal, tokenizer/readout boundaries, asset identities and no-overwrite behavior | [Liquid preparation](../docs/models/liquid.md); offline fixtures check mechanics, not GPU training |
| Liquid application workflow | Frozen TRL/Unsloth studies, separate operator-selected interpreters, native backend preservation in browser controls, role/data/config refusal and cancellation priority | [Application training](../docs/models/liquid.md#train-and-release-through-the-localhost-application); fixture checks do not establish GPU/domain quality |
| Liquid training contracts | Completion-only labels, padding, parameter settings, calibration gold/temperatures and approved runtime/config identities | [Local Liquid training](../docs/models/liquid.md#train-with-the-local-trl-runner); actual hardware/model qualification is separate |
| D3 family contracts | Variant/revision isolation, all-shard verification, geometry and storage limits, six-variant/five-provider frozen study combinations | [Family workflow](../docs/models/d3-family.md); larger runtime profiles need separate hardware qualification |
| D3 native/application contracts | Option/soft-target order, review/role lineage, frozen local/five-provider studies, cloud dispatch, owner-selected interpreters, browser controls, cancellation priority, source-only loading and whole-bundle guards | [D3 workflow](../docs/models/d3.md); model execution and domain-quality evaluation are separate checks |
| Recovery and operations | Backup integrity, cold recovery, ownership, monitoring and audit windows | [Recovery](../docs/recovery-guide.md), [audit exports](../docs/audit-export-guide.md) |
| Public repository | Exclusion of private files and reusable source/configuration eligibility | [Contributing](../CONTRIBUTING.md) |

[Fixture documentation](fixtures/README.md) explains the included provider-schema excerpts. Helper transports and process runners in this folder are test infrastructure, rather than production endpoints. Keep fictional test data separate from actual domain records.

Use the [container verifier](../scripts/README.md) for the separate image lifecycle checks. Complete the [domain and provider qualification](../docs/verification.md#provider-compatibility-checks) required by your deployment, and report platform-specific skips separately from executed tests.
