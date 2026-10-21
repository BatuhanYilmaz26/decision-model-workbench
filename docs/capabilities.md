# Use the local platform

[All guides](README.md) · [Project overview](../README.md)

Use the workflows below to create a domain project, review examples, train a model and release an authenticated decision endpoint. Each guide explains the required inputs, configuration, outputs and operating limits.

| Capability | How to use it | What you receive | Boundary |
|---|---|---|---|
| Persistent domain projects | [Create a project](setup.md#use-the-application), describe its policy and confirm its questions | A project you can reopen after browser or service restarts | One local workspace; distributed storage needs a separate design |
| Guided domain intake | Describe available evidence, policy exceptions and data needs; build category, yes/no or rating questions; [enter examples](data-guide.md) and optionally set [domain error criteria](error-policy-guide.md) | An owner-confirmed schema, saved requirements and reviewed examples | The owner defines the policy; natural-language text does not automatically become model labels or acceptance thresholds |
| Operator encrypted recovery | [Generate a key, capture, verify and restore](encrypted-recovery-guide.md) using a separately retained checksum; [schedule captures and verified retention](scheduled-backups-guide.md) | Authenticated encrypted all-tenant bundles, durable receipts and a fresh recovered workspace | Approved private storage and key custody required; single-host scheduling, offsite operation and enterprise supervision need qualification |
| Backup freshness and failure monitoring | [Publish signed health and configure its API trust profile](backup-monitoring-guide.md) | Scoped incidents and safe backup summaries; optional required-readiness behavior | Public-key verification; basic availability/freshness indicators, with complete verification/restore drills and external alert integration separately required |
| Immutable dataset revisions | [Import, append and review examples](data-guide.md) | Versioned rows, hashes and reviewer history; stale edits return a conflict | Imported approval flags are not trusted |
| Assigned annotation work | [Assign examples and complete reviews](review-workflow.md) | Persistent reviewer queues and immutable assignment/draft/completion history | Positive example collections; assignments do not grant access or establish label correctness |
| Synthetic coverage from training seeds | [Configure a teacher](setup.md#configure-a-teacher), reserve real holdouts and [request coverage](data-guide.md#generate-and-review-synthetic-candidates) | Queued, unapproved candidates with seed lineage | Review generated labels and measure the selected teacher's domain quality |
| Durable job submission and status | [Submit preparation or training](product-workflow.md) and inspect the job status | Persistent status and idempotent submission | An interrupted job is not permission to replay an uncertain external operation |
| Concurrent worker ownership | [Run API and worker separately](setup.md#run-services-individually) | Transactional job claims, heartbeats and stale-worker completion fencing | SQLite by default; experimental [PostgreSQL metadata](postgresql-guide.md) with artifacts still on one host |
| Native Laya training | [Use the native adapter](training-guide.md#native-laya-adapter) with four reviewed splits | A trained decision head, independent calibration, FP32 export and reload report | The default freezes the encoder; validate memory and quality before changing hardware or training mode |
| CPU comparison baseline | [Select the CPU backend](setup.md#use-the-application) | Trained TF-IDF/logistic classifier, calibrated JSON export and reload checks | Fixed-schema comparison model; it does not train a foundation decision head |
| Tenant-scoped access | [Provision credentials](authentication-guide.md#workspace-key-lifecycle) and configure trusted identities | Server-derived tenant/role checks on workspace resources | Interactive SSO and real enterprise issuer deployment need separate validation |
| Domain error costs and critical labels | [Configure a frozen error policy](error-policy-guide.md) | Per-question policy-cost estimates, class-specific error rates and release refusal for failed limits | Forced-classification evaluation; independent operational gold and deployed-action costs need separate qualification |
| Frozen acceptance criteria | [Set quality and class-count gates](data-guide.md#inspect-readiness-before-training) before the first training submission | Immutable criteria bound to a model study and its release decision | Domain owners must select meaningful limits and independent gold |

## Other documented paths

Use the [model directory](models/README.md) for the complete backend list, including [Liquid](models/liquid.md), [D3 family](models/d3-family.md), [hosted decision studies](hosted-decisions-guide.md) and [remote training](remote-training-guide.md). Match the selected backend's dependency, data, provider and approval requirements.

Use [testing and compatibility](verification.md) for installation checks, provider checks and domain evaluation before deployment.
