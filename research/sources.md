# Annotated source register

[Research index](README.md) · [Project overview](../README.md)

Use this register to find the primary documentation, source code, model cards and benchmark artifacts behind the guides. The initial references are dated **9 October 2026**, with later additions grouped below.

[Source metadata](provenance/source-snapshot.json) records URLs, retrieval dates and SHA-256 hashes. Follow the original links for full documents. Hashes identify the retrieved version; they do not independently verify a publisher's claims.

For cloud interfaces, runtime versions and dependency advisories, use the [14 October compatibility guide](../docs/compatibility-and-upgrades.md) and its [source metadata](provenance/runtime-sources-2026-10-14.json). Historical benchmark extracts retain their original source identities and dates. The [provenance index](provenance/README.md) explains the machine-readable records; use this register for the readable references.

## Starting sources

- [Jev introduction — TypeSafe, 15 September](https://typesafe.ai/blog/introducing-system-one-models-and-jev): launch, RLCD claims and workflow-evaluation limitations.
- [Harness with Jev — LangChain, 17 September](https://www.langchain.com/blog/building-a-harness-with-jev): integrating decisions into an agent loop.
- [Laya repository](https://github.com/NandhaKishorM/laya): implementation, models and native training paths.
- [Unsloth training guide](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth): public head-training and export APIs.

## Availability, implementation and pricing

- [TypeSafe current models](https://docs.typesafe.ai/models): version, input tariff and no customer fine-tuning.
- [Unsloth local serving](https://unsloth.ai/docs/models/decision-laya): API aliases, primitive semantics and migration differences.
- [Unsloth decision source](https://github.com/unslothai/unsloth/blob/main/unsloth/models/decision.py): reviewed API signatures and preprocessing reports.
- [Laya trainer](https://github.com/NandhaKishorM/laya/blob/main/laya/train.py): target distributions, objective and internal calibration split.
- [Laya CLI](https://github.com/NandhaKishorM/laya/blob/main/laya/train_cli.py): training flags.
- [Nimble source](https://github.com/bespokelabsai/nimble): recipe and option-token readout.
- [Kev-27B card](https://huggingface.co/jaredpalmer/kev-27b): reviewed Kev identity, training and limitations.
- [Strands v19 card](https://huggingface.co/StrandsAgents/strands-decider-2B-hobson-v19): checkpoint, license and transfer limitations.
- [Strands recipe](https://github.com/strands-labs/strands-decider/blob/main/training/README.md): published retraining path.
- [Clef release — Cloudflare, 1 October](https://blog.cloudflare.com/clef-decision-models/): vendor comparison and open model release.
- [Clef card](https://huggingface.co/Cloudflare/clef) / [Clef-flash card](https://huggingface.co/Cloudflare/clef-flash): head, backbone and runtime.
- [Clef tariff](https://developers.cloudflare.com/workers-ai/models/clef/) / [Clef-flash tariff](https://developers.cloudflare.com/workers-ai/models/clef-flash/): current Workers AI prices.
- [Perplexity v1](https://huggingface.co/perplexity-ai/pplx-decider-v1-27b) / [v1.1](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b): separate model comparisons.
- [Perplexity API](https://docs.perplexity.ai/docs/decisions/quickstart): current served model and $0.02 tariff.
- [Hosted d1 — Liquid AI, 5 October](https://www.liquid.ai/blog/d1-decision-model): hosted vision model, pricing basis and experimental method.
- [Open d1 — Liquid AI, 7 October](https://www.liquid.ai/blog/d1-open): new open checkpoints and measured hardware.
- [d1-3B card](https://huggingface.co/LiquidAI/d1-3B) / [d1-omni card](https://huggingface.co/LiquidAI/d1-omni-600M): LFM license and modalities.
- [OpenAI changelog](https://developers.openai.com/api/docs/changelog): October 6 public beta.
- [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions): Luna endpoint, distinct schema and tariff.
- [lev implementation](https://github.com/InterfazeAI/lev): measured comparison and source-visible caveats.
- [Aplomb announcement](https://empiriolabs.ai/blog/introducing-aplomb-1): capabilities and custom license, attributed to developer.
- [Quyet card](https://huggingface.co/chinhnc/Quyet-1.0-Large), [H2O-Lightning](https://huggingface.co/h2oai/h2o-lightning-4b), [decisio](https://github.com/aminry/decisio): additional benchmark candidates.

## Benchmarks and methodological research

- [Decision Index](https://huggingface.co/spaces/multimodalart/jev-decision-index): community suite; preserve version and split.
- [typed-decisions](https://huggingface.co/datasets/LocalLLaMA/typed-decisions): teacher-derived workflow labels and fitted/zero-shot distinction.
- [Laya benchmarks](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md): checkpoint-specific results and calibration counterevidence.
- [Benchmark Heaven source](https://github.com/fstandhartinger/jevbench): evolving method and disclosed limits.
- [v1.6.1 artifact](https://benchmarkheaven.com/api/decision-results/jevbench/v1.6.1/json) / [manifest](https://benchmarkheaven.com/api/jevbench/manifest): exact extracted version and scoring basis.
- [Separate JevBench coherence project](https://jevbench.github.io/): different consistency evaluation, same similar name.
- [JEVal, October 2 preprint](https://arxiv.org/abs/2610.03935): broad and composed-system evidence; principally Jev.
- [Early evidence audit](https://arxiv.org/abs/2609.32160): additional evaluation checklist; useful follow-up reading, not a source of the guide's numerical claims.
- [Guo et al., ICML 2017](https://proceedings.mlr.press/v70/guo17a.html): calibration and temperature scaling.

## Enterprise hosting contracts

- [AWS SageMaker AI custom inference](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html)
- [Google Cloud custom prediction container](https://cloud.google.com/vertex-ai/docs/predictions/use-custom-container)
- [Azure ML custom container](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-deploy-custom-container?view=azureml-api-2)

Follow these serving specifications when wrapping a decision runtime for the selected cloud. Check health, prediction routes, identity and model loading in your target environment.

## Additional pinned evidence

- [Decision Index 0.3.1 methodology](https://huggingface.co/spaces/multimodalart/jev-decision-index/blob/32425c2/data/methodology.json) and [results](https://huggingface.co/spaces/multimodalart/jev-decision-index/blob/32425c2/data/index.json): pinned public/private weights, coverage and selected scores; retrieval hashes are in the [source snapshot](provenance/source-snapshot.json) and [Decision Index snapshot](provenance/decision-index-snapshot.json).
- [Unsloth Core installation](https://unsloth.ai/docs/get-started/install/pip-install): separate supported environment for the training recipe.

## Expansion reviewed 10 October 2026

API sources: [TypeSafe contract](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models), [OpenRouter Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request), [Perplexity](https://docs.perplexity.ai/docs/decisions/quickstart), [Cloudflare Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/) and [TypeLLM workflow semantics](https://typellm.ai/docs/workflows). Their explicit contracts underpin the [integration guide](../docs/api-integration-guide.md).

The [AWS](../docs/remote-training/aws.md), [Azure](../docs/remote-training/azure.md), [GCP](../docs/remote-training/gcp.md), [Modal](../docs/remote-training/modal.md) and [RunPod](../docs/remote-training/runpod.md) walkthroughs cite the job, storage and shutdown APIs next to their commands. Azure command-job schemas and Google aiplatform discovery excerpts also support the [offline provider-contract tests](../tests/fixtures/provider-contracts/README.md).

## Data methodology reviewed 11 October 2026

[scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage) explains prediction-time information boundaries. Its [cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html) describes the independence assumptions behind random splits and time-dependent evaluation. The project's [declared temporal protocol](../docs/temporal-data-guide.md) adds conservative four-role ordering and label-availability checks as an explicit project policy.

[Sentence Transformers semantic similarity](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html) and [paraphrase mining](https://www.sbert.net/examples/sentence_transformer/applications/paraphrase-mining/README.html) explain similarity scoring and candidate mining. They do not establish domain-specific duplicate thresholds or prove decontamination. Four retrieved primary pages and their hashes are recorded in [temporal-data-sources-2026-10-11.json](provenance/temporal-data-sources-2026-10-11.json).

The [pinned all-MiniLM-L6-v2 model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md) specifies 384-dimensional sentence embeddings, masked mean pooling, normalization and the usual 256-token limit. The [semantic workflow](../docs/semantic-data-guide.md) rejects over-limit states instead of truncating them and operates separately from decision-head training. [Reference metadata and asset hashes](provenance/semantic-data-sources-2026-10-11.json) identify the model, mining documentation and [PyTorch cache implementation](https://github.com/pytorch/pytorch/blob/v2.10.0/torch/_inductor/runtime/cache_dir_utils.py). Verify prepared assets against the approved upstream revision before generating an operator profile.

The 11 October [TypeLLM refresh](provenance/taxonomy-sources-2026-10-11.json) preserves current API/cookbook, package metadata and pinned repository/source/notebook captures, with source revisions and retrieval dates. The [taxonomy guide](../docs/taxonomy-guide.md) distinguishes constrained generation from calibrated heads and specifies this application's separate proposal/review/owner-confirmation protocol.

## Guided policy interfaces

The [guided criteria workflow](../docs/error-policy-guide.md) uses Streamlit's supported table editor and column configuration. [Official data-editor documentation](https://docs.streamlit.io/develop/api-reference/data/st.data_editor), [editor state](https://docs.streamlit.io/develop/concepts/design/dataframes) and [AppTest interfaces](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest) explain the UI and automated testing contracts. Retrieval dates and byte hashes are recorded in [guided-policy-sources-2026-10-14.json](provenance/guided-policy-sources-2026-10-14.json). Domain costs and critical-rate gates follow the separate [evaluation references](provenance/error-policy-sources-2026-10-14.json).

## Encrypted workspace recovery

The [encrypted recovery guide](../docs/encrypted-recovery-guide.md) cites Cryptography's AES-GCM/HKDF interfaces and Windows access-control management. [encrypted-recovery-sources-2026-10-14.json](provenance/encrypted-recovery-sources-2026-10-14.json) records the official page identities, dates and hashes. The project-specific framed file format preserves the separate workspace manifest and fresh-target validation contracts; primitive documentation does not certify the application's operational environment.

The [scheduled capture and retention guide](../docs/scheduled-backups-guide.md) uses local SQLite transactions and Windows/POSIX file locks. [scheduled-backup-sources-2026-10-14.json](provenance/scheduled-backup-sources-2026-10-14.json) records the official interfaces and SQLite online-backup guidance. Process ownership and catalog authentication have separate roles; these interfaces do not establish distributed scheduling or external anti-rollback guarantees.

[Backup health monitoring](../docs/backup-monitoring-guide.md) uses separate Ed25519 signing and public verification keys. [backup-monitor-sources-2026-10-14.json](provenance/backup-monitor-sources-2026-10-14.json) records the primary signing/serialization interface. Signature validity and snapshot freshness are distinct from encrypted payload verification and disaster-recovery acceptance.
