# Tests and compatibility fixtures

[Project overview](../README.md) · [All guides](../docs/README.md)

Follow [testing and compatibility](../docs/verification.md) to install the test environment and run the suite. Tests exercise local contracts and selected runtime behavior; provider fixtures do not establish real cloud execution, model quality or billing.

| Area | What is checked | Related guide |
|---|---|---|
| Data and domain contracts | Import, review, preparation, held-out roles and synthetic lineage | [Data](../docs/data-guide.md), [evaluation](../docs/evaluation-guide.md) |
| Explicit reservations | Group/time policies, fixed historical roles, owner preview/approval and recovery integrity | [Reservation workflow](../docs/reservation-guide.md) |
| Platform services | HTTP authorization, persistence, worker ownership, releases and inference | [Architecture](../docs/architecture.md), [authentication](../docs/authentication-guide.md) |
| Browser workflows | Guided forms and API-backed state through Streamlit AppTest | [Testing scope](../docs/verification.md) |
| Training and provider adapters | Frozen request construction, export contracts and failure handling | [Training](../docs/training-guide.md), [provider checks](../docs/verification.md#provider-compatibility-checks) |
| Recovery and operations | Backup integrity, cold recovery, ownership, monitoring and audit windows | [Recovery](../docs/recovery-guide.md), [audit exports](../docs/audit-export-guide.md) |
| Public repository | Exclusion of private files and reusable source/configuration eligibility | [Contributing](../CONTRIBUTING.md) |

[Fixture documentation](fixtures/README.md) explains the included provider-schema excerpts. Helper transports and process runners in this folder are test infrastructure, rather than production endpoints. Keep fictional test data separate from actual domain records.

Use the [container verifier](../scripts/README.md) for the separate image lifecycle checks. Complete the [domain and provider qualification](../docs/verification.md#provider-compatibility-checks) required by your deployment, and report platform-specific skips separately from executed tests.
