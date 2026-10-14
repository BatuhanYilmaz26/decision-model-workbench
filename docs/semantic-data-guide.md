# Find and review semantic relatives before reservation

[All guides](README.md) · [Project overview](../README.md)

Different wording does not make two records independent. The platform supports a separate local embedding audit to propose related states, followed by authenticated adjudication. Its encoder does not train the decision head, generate examples or label business outcomes. It helps keep source relatives out of different evaluation roles.

Cosine similarity can propose paraphrases, but candidate selection depends on the model and threshold. Similarity alone does not establish shared source history. The project compares every pair within an explicitly bounded study; it refuses candidate overflow instead of returning a silently truncated top-k list. [Sentence Transformers paraphrase mining](https://sbert.net/examples/sentence_transformer/applications/paraphrase-mining/README.html)

## Operator setup

Use a model-capable worker environment with the optional `semantic` package extra or [semantic-models.txt](../requirements/semantic-models.txt). The ordinary application environment can run the API; the worker uses `DMT_SEMANTIC_PYTHON` for its separate CPU child. No GPU tensor is created.

The available runner supports standard BERT encoders with attention-mask mean pooling and L2 normalization. It loads a **local directory**, requires safetensors, disables Hub access/implicit tokens/telemetry and refuses remote-code configuration. Its child receives an allowlist of ordinary OS variables rather than provider credentials. It checks all six model/tokenizer file hashes before and after execution and checks exact installed model-library versions.

The worker gives each job a private Torch cache instead of inheriting cache locations or username variables. Include these directories in the workspace's retention policy. The setting follows the [pinned PyTorch cache implementation](https://github.com/pytorch/pytorch/blob/v2.10.0/torch/_inductor/runtime/cache_dir_utils.py).

Use [all-MiniLM-L6-v2 at revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) for the pinned profile. It produces 384-dimensional embeddings with masked mean pooling and normalization. The usual limit is 256 word pieces; this runner rejects inputs beyond the configured budget rather than truncating them. The card declares Apache-2.0; check dependency and backbone terms separately. [Pinned model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md)

Prepare an approved local snapshot using the model owner's distribution process. Inside the repository checkout, run the operator helper with the model environment:

```sh
python -m scripts.create_semantic_profile \
  --model-dir /absolute/approved/minilm-snapshot \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --tenant YOUR_TENANT --project YOUR_PROJECT_ID \
  --out data/config/semantic-profile.json
```

Replace the path and scope with your deployment's values. Repeating `--tenant`/`--project` adds explicitly approved scopes; project `*` applies to the selected tenants' projects. The helper hashes the local files and captures installed dependencies; it **does not authenticate a claimed Hub revision or download weights**. A directory path is an operator detail and is omitted from the public profile API. Keep that file outside Git.

Configure `DMT_SEMANTIC_PROFILE_FILE` in both API and worker. Configure `DMT_SEMANTIC_PYTHON` in the worker when its ordinary Python lacks the model libraries. These values are process environment settings, not automatically loaded from `.env` by Python. An unavailable/invalid configured profile fails visibly; an absent or unmatched scoped profile leaves the legacy unchecked-semantic path available with an explicit UI notice.

The helper defaults to a **required** audit, a 256-token budget, 5,000 records, 10,000 candidates and a 600-second child deadline. These are resource bounds, not adequacy recommendations. Operators can select supported bounded values. `--optional` permits legacy reservations for that scope; optional does not make them semantically qualified. The UI disables reservation when a required audit is absent or its candidate reviews are pending; the API and worker enforce the same requirement.

The Windows dependency profile uses `torch 2.10.0+cu126`, but the semantic audit runs on CPU. In a container, install the model-capable runtime and mount an approved model directory read-only before enabling the profile; the baseline image contains no model assets. Validate replacement checkpoints and CPU-wheel profiles separately. Monitor interrupted children and manage cache retention yourself; there is no fleet-wide capacity scheduler.

## Owner and reviewer workflow

1. Approve complete business labels on the selected immutable dataset versions. Explicitly rejected rows are excluded; pending records still block the study.
2. In **Prepare and generate**, select input dataset versions and, for a synthetic extension, the existing base snapshot.
3. Open **Find and review semantic relatives**, select a domain-reviewed cosine threshold and queue the local audit. The UI's illustrative default is not a recommended universal threshold.
4. Inspect the completed audit's record/comparison/candidate counts and paginated state pairs. Nothing is sent to a hosted provider.
5. An owner or reviewer records **related** or **independent** with a reason for every candidate. An editor can inspect and prepare reviewed data but cannot adjudicate pairs or change the threshold by creating an audit; audit creation requires an owner.
6. Select that exact reviewed audit and reserve the four roles. Preparation refuses an audit from different dataset hashes, a different base snapshot, a changed profile/source or a job that never completed successfully.

Related pairs join transitively with the existing source, exact-state and seed relationships before initial splitting. Records remain intact: neither their targets nor their states are overwritten, and independent judgments cannot override an already declared source family or exact duplicate. Different labels can still describe related counterfactuals that should stay in one role.

For a synthetic extension, the audit compares new candidates **and all original reserved states**. Existing heldouts retain their exact content. A new confirmed relationship crossing a reserved role causes refusal. Do not mark a relationship independent just to bypass this check; reject/correct the candidate or design a successor study with the domain owner.

Reviews are immutable revisions. Concurrent stale writes return a conflict and require refresh. A later review revision does not change an existing snapshot: preparation pins the exact review IDs and their content hashes. Initial report, row membership/content, profile, threshold, source, job and review bindings remain in the workspace audit history and backup.

## Application and portable contracts

The worker revalidates semantic bindings before generation, local training, hosted evaluation, remote-study creation and remote admission. It reconstructs the exact audited source records and checks the reservation's annotations/content, not just record IDs. Model/profile/package changes require a new audit before future study use. This is not automatic revocation of already released inference.

Backup/restore validates the preserved report/review/source relationships without importing an embedding model or requiring the current operator configuration. A successfully restored historical study still needs current runtime/profile qualification before new work.

Reserved records carry compact `metadata.semantic_review` bindings and `semantic_links` for confirmed relatives. The shared four-file validator refuses links crossing roles or referring to missing records. These portable links preserve declared relationships during native Laya, Unsloth and remote runner input validation. Standalone maintenance JSONL remains operator-supplied data: links alone are not cryptographic proof of authenticated review. Platform provenance/input hashes bind remotely returned exports to the owner-reviewed platform reservation.

| Endpoint/action | Purpose |
|---|---|
| `GET /v1/projects/{project}/semantic-profile` | Public scoped model/file/version/resource profile; excludes local model paths and tenant inventory |
| Job kind `semantic-audit` | `datasets`, optional `base_snapshot`, and numeric `threshold` from 0.5 to 1; operator profile and package source freeze at submission |
| `GET /v1/projects/{project}/entities/semantic-audit` | Bounded audit summaries, without full row hashes or pair lists |
| `GET /v1/projects/{project}/semantic-audits/{audit}/pairs` | Paginated states and current review; `limit` 1–100, `offset` nonnegative |
| `POST /v1/projects/{project}/semantic-audits/{audit}/reviews` | `pair_id`, `decision`, `expected_revision`, `expected_hash` and `note` |
| Job kind `prepare` | Add `semantic_audit` to bind an exact fully reviewed audit |

The encoder evaluates the state text or canonical JSON serialization only; question instructions, gold labels and review notes do not enter the embedding. It does not fit a vectorizer on heldouts. Semantic adjudication can still reveal test inputs to reviewers, so control their access and preserve the distinction between independence review and model selection.

## Combined positive and applicability audits

The applicability workflow uses a distinct owner-created `gate-semantic-audit` with `snapshot`, `datasets` and `threshold`. It freezes the exact positive reservation, ordered negative versions, reviewed included records, content hashes, profile, package source and threshold before the worker invokes the same bounded offline CPU encoder. Pending negative annotations block the audit; rejected records are excluded. Every included positive and negative state is compared together.

In Applicability, select the paired inputs and open **Combined Find and review semantic relatives**. Review all candidates using authenticated owner/reviewer accounts. Select the completed combined audit and reserve applicability roles. A required scoped profile refuses missing or pending audits. A positive-only audit, different collection version, changed profile/source or different positive snapshot cannot substitute for this audit. New review revisions cannot change a previously queued job's frozen review IDs and content hashes.

Positive role membership and stored snapshot content remain immutable. Confirmed new positive relationships appear only on portable derived copies. Cross-collection links live on negative copies so that the positive-only validator can still validate its four files. Negative relatives follow their positive anchors. Any transitive component joining two frozen positive roles refuses reservation. Known families, exact states, timestamps and positive/negative annotation contradictions remain independently enforced.

Vega training and admission consume those derived positive copies alongside the negative files. Calibration-family accounting includes the newly reviewed positive links and negative bridges; apparent sample counts cannot override the frozen negative test floor or the sixty positive calibration-family preflight. Eight file hashes bind the actual materialized inputs, while original snapshot hashes remain in provenance. Do not independently export the original positive snapshot and splice it into a combined study.

Cold backup/restore replays original audited records, immutable reviews and derived reservations without loading the encoder or requiring an active operator profile. New training/export work still requires current source/profile bindings. Links in standalone files retain relationships but do not independently attest authenticated platform review.

## Limits of the result

An exhaustive comparison covers all **included** pairs, but cosine similarity can still miss relatives or rank unrelated states highly. Evaluate negation, paraphrases, languages, structured fields, long inputs and templated records using domain examples. Choose the threshold with reviewers; do not treat the default as a universal rule.

Candidate/token/row/deadline overflow refuses the study. Larger corpora and longer states need a qualified alternative rather than silent sampling or truncation. CPU batching/chunking bounds temporary pair memory, but enterprise throughput and multi-host scheduling have not been established.

Combine semantic review with the [temporal protocol](temporal-data-guide.md) and [data workflow](data-guide.md). Evaluate model quality separately using independently reviewed operational gold.
