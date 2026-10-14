# Decision-model research refresh — 12 October 2026

Compare the decision API contracts and Vega results published by **12 October 2026**. This update supplements the [10 October comparison](decision-model-expansion-2026-10-10.md); the [source metadata](expansion-sources-2026-10-12.json) identifies dated URLs and capture hashes.

## Sources and findings

| Source | 12 October review | Consequence |
|---|---|---|
| [Akshay Pachaar's X article](https://x.com/akshay_pachaar/status/2101037514945597645) | Bounded judgment/routing explanation; displayed publication date 18 September 2026 | Use its bounded-judgment explanation; verify launch-era availability/pricing separately |
| [OpenRouter rankings](https://openrouter.ai/rankings/decisions) | Request-volume ranking, data through 11 October 2026 | Discover routes; do not infer quality from adoption |
| [JevBench v1.6.6](https://benchmarkheaven.com/jev-models/v1.6.6) | Exact page/JSON recaptured; JSON matches the earlier artifact | Preserve draw, denominator and cost assumptions |
| [TypeLLM cookbook](https://typellm.ai/cookbook/discover-invoice-categories?v=2) | Enum plus conditional category-name generation | Review names before fixed-schema training |
| [TypeLLM notebook](https://github.com/TypeLLM/TypeLLM/blob/fd9b97a06386d1898d2c241796c3c81bd10db9c0/examples/notebooks/discover_invoice_categories.ipynb) | Current repository revision `fd9b97a06386d1898d2c241796c3c81bd10db9c0`; enum plus conditional name and sequential growth | Preserve canonical IDs, owner review and successor schemas |
| [Vega article](https://www.nandakishorm.com/writing/vega) | Frozen-perception/learned-engine architecture | Frozen perception/learned engine differs from text generation and encoder-LoRA training |
| [vegaml source](https://github.com/NandhaKishorM/vegaml/tree/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4) | Version 0.8.0; loader, prompt builder, gates and fitting inspected | Explicit engine mode and separate domain adapter required |
| [Vega card](https://huggingface.co/nandakishorm/vega-08b-public-intents/blob/42ac8d4fa9efb61f96e53d80962cf28f6dfe667e/README.md) | New author comparison: 3,841 decisions/system on 11 October | Do not combine with the earlier 5,096-decision run |

The original Jev launch, LangChain harness, Laya source and Unsloth training guide provide the foundations. They remain the foundation for [concepts](../docs/concepts.md) and [training](../docs/training-guide.md). A typed output can select the wrong allowed outcome; schema safety is distinct from factual/workflow correctness.

## API implications

Current primary references still document direct TypeSafe `POST /v1/systemone`, OpenRouter `POST /api/alpha/decisions`, Perplexity `POST /v1/decisions` and Workers AI's Clef Flash binding. Preserve each route's envelope, limits and identity semantics. OpenRouter's alpha route is not chat completions. [TypeSafe API](https://docs.typesafe.ai/api), [OpenRouter API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request), [Cloudflare model](https://developers.cloudflare.com/workers-ai/models/clef-flash/)

Perplexity states that response `model` echoes the requested name. Validate it, but do not describe it as an immutable weight attestation. Gateway identities, provider names and verified local hashes provide different assurance. [Perplexity response contract](https://docs.perplexity.ai/docs/decisions/quickstart)

Cloudflare's Clef Flash documentation dated 12 October lists **24,576 tokens**. Questions and media share the window, and text can be truncated to fit. Check complete-evidence handling against the selected route before using long documents. [Model limits](https://developers.cloudflare.com/workers-ai/models/clef-flash/)

The displayed request ranking puts Jev 1.13 first at approximately 1.05 billion requests. This measures usage, not correctness, calibration or quality per dollar. Qualify additional routes against primary vendor/card sources rather than assuming a ranked name offers fine-tuning. [OpenRouter usage definition and dated table](https://openrouter.ai/rankings/decisions)

The [API guide](../docs/api-integration-guide.md) supplies HTTP/SDK examples. New [application blueprints](../docs/application-blueprints.md) add existing-handler rollout and five proposed projects with state design, action boundaries, data needs and whole-workflow acceptance. These are recommendations, not demonstrated improvements.

## Vega's new comparison

The author reports a zero-shot engine comparison on two T4s, 3,841 decisions/system. Across its 43-task public menu, macro accuracy is **0.556 / 0.717 / 0.836** for Vega 0.8B / Vega 4B / hosted Jev. DecisionBench calibration error is **0.103 / 0.098 / 0.122**. This is a task/metric-dependent tradeoff, not a universal Vega win. Local and hosted latency boundaries differ. [Pinned author README and full context](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/README.md)

The card's adapted typed-decisions results are another study. Adapted, selected public and independent sealed evidence must remain separate. [Pinned card](https://huggingface.co/nandakishorm/vega-08b-public-intents/blob/42ac8d4fa9efb61f96e53d80962cf28f6dfe667e/README.md)

## Independent counterevidence

Both versioned artifacts remain byte-identical to the 10 October capture. The [19-row extract and method record](benchmark-expansion-method-2026-10-10.json) remain authoritative for those versions. Selected Capability scores and estimated USD/1,000 decisions:

| Version | System | Capability | Estimated cost |
|---|---|---:|---:|
| v1.6.6 | Wald 4B v2.1 | 69.27093 | 0.06172102 |
| v1.6.6 | Vega 0.8B | 32.99469 | 0.01923910 |
| v1.6.7 | Blink v0.3 26B-A4B | 69.94540 | 0.04775858 |

v1.6.6 uses 1,500 decisions, including 1,200 sealed; v1.6.7 adds Blink on the same draw. Capability is not percent accuracy; estimated costs are not vendor quotes. The historical Jev reference is not a current same-draw baseline. Preserve hardware, latency estimate, repeatability modification and refused-input accounting. [v1.6.6 artifact](https://benchmarkheaven.com/api/jevbench/v1.6.6), [v1.6.7 artifact](https://benchmarkheaven.com/api/jevbench/v1.6.7)

These versioned extracts are historical snapshots, not a complete current leaderboard. Model choice still requires a matched domain study with error cost, calibration, selective coverage and serving economics.

## Training and implementation

Train open-weight models using the [AWS, Azure, GCP, Modal and RunPod walkthroughs](../docs/remote-training-guide.md). Each covers staging, submission, observation, retrieval and stopping. Use [provider selection](../docs/remote-training/provider-selection.md) to compare capacity, total cost and retained-resource charges, and validate the selected image and account before a domain study.

Vega 0.8.0 changes default readout selection. Source inspection also finds inner prompt shortening and fitting minimums. [The Vega guide](../docs/vega-guide.md) records complete asset pins, applicability negatives and four-role adaptation requirements.
