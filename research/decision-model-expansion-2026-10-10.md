# Decision-model research expansion — 10 October 2026

This dated comparison covers decision APIs, taxonomy discovery, Vega and JevBench v1.6.6/v1.6.7. Results are attributed to their publishers, with pins and methods retained; they are not independently reproduced model evaluations.
The [source registry](provenance/expansion-sources-2026-10-10.json) records URLs, dates, status and source hashes. The [19 benchmark rows](provenance/benchmark-expansion-2026-10-10.csv) and [method metadata](provenance/benchmark-expansion-method-2026-10-10.json) identify model revisions, latency boundaries, cost assumptions and uncertainty. Hashes identify captured bytes; numerical results remain attributed to their benchmark publishers.

## Sources and findings

| Source | Subject | Implication for model selection |
|---|---|---|
| [Akshay Pachaar's X article](https://x.com/akshay_pachaar/status/2101037514945597645) | “Jev Clearly Explained”: bounded decisions, probabilities, routing, tool gating and a restrained reading of launch claims | Use as an explanation, then verify current endpoints/availability/limits against vendor documentation |
| [OpenRouter decisions ranking](https://openrouter.ai/rankings/decisions) | Request-volume table, data through 9 October 2026 | A route-discovery/adoption signal, not an accuracy, calibration or quality-per-dollar leaderboard |
| [JevBench v1.6.6](https://benchmarkheaven.com/jev-models/v1.6.6) | Exact page plus its `/api/jevbench/v1.6.6` artifact | A new draw must not be combined with the older v1.6.1 board |
| [TypeLLM invoice cookbook](https://typellm.ai/cookbook/discover-invoice-categories?v=2) | Growing categories, a closed enum plus conditional generated name | Useful taxonomy-discovery stage before owner-approved fixed-schema training |
| [Invoice notebook](https://github.com/TypeLLM/TypeLLM/blob/7c5b2805c9390377621b4c513bdaab39ff8b5413/examples/notebooks/discover_invoice_categories.ipynb) | Cells/code for 50 illustrative invoices, `typellm>=0.6.7`, `generate(...).result` | Example labels and automatic category appends need review, canonicalization and schema versioning |
| [Vega article](https://www.nandakishorm.com/writing/vega) | Author's 10 October architecture explanation | Frozen language perception plus learned dynamics/readout is a distinct decision architecture |
| [vegaml source](https://github.com/NandhaKishorM/vegaml/tree/01d2850408084034e27174621c19e01f0f0b9e92) | README, package metadata, actual public loader and fine-tuning script | Add a separately qualified backend; do not assume Unsloth/Laya compatibility |
| [Vega model card](https://huggingface.co/nandakishorm/vega-08b-public-intents/blob/c85f2338fcc965f5ab0050a5752200e86fa8f63b/README.md) | Selected benchmark results, calibration, sizes, multimodality and limitations | Report task-specific wins alongside independent counterevidence and exact pins |

The original notebook URL points to `main`; the snapshot pins commit `7c5b2805c9390377621b4c513bdaab39ff8b5413`. Vega code is pinned at `01d2850408084034e27174621c19e01f0f0b9e92` (package metadata version 0.6.1); the reviewed current Hub revision is `c85f2338fcc965f5ab0050a5752200e86fa8f63b`. Pins matter even when two source revisions share the same package version.

## What the new sources change

A decision model turns decision-time evidence and allowed outcomes into scores or distributions that application policy can consume. A constrained shape does not establish a correct label, calibrated uncertainty, security approval or workflow success. Deterministic checks and execution remain code responsibilities. The X explanation makes that distinction usefully, but its launch-era text-only/early-access and speed/cost descriptions are historical statements, not the current provider catalog. Current direct Jev documentation lists `jev-1.13.0`, a text/JSON API, input-only billing and account-specific limits. [Current TypeSafe model contract](https://docs.typesafe.ai/models)

Decision models use several mechanisms: encoder/head training such as Laya, decoder-derived heads, frozen-feature engines such as Vega, and constrained token/log-probability readouts such as Blink. The same `state/questions` interface can hide different execution paths. Multiple questions in one request do not imply one backbone pass or equal marginal cost; measure the full path for the intended workload.

The hosted request-contract guide covers direct Jev, OpenRouter, Perplexity and Workers AI, plus TypeLLM and self-hosted models. Provider availability is not permission to train proprietary weights. Open-weight domain adaptation and hosted inference selection are separate product routes. [API integration guide](../docs/api-integration-guide.md), [remote training guide](../docs/remote-training-guide.md)

### TypeLLM: discover, review, then freeze the taxonomy

The cookbook uses an enum containing existing names plus `new`; `when` requests a new category name only on that branch. This is typed generation with an open-ended field, rather than a non-generative head whose entire answer set is predefined. The notebook grows its in-memory list sequentially and does not implement enterprise taxonomy governance or model training.

For domain taxonomy discovery, keep proposed names separate from approved categories. Review aliases and granularity, assign stable IDs and version the taxonomy before converting labels into targets. Coordinate asynchronous workers through revision checks. A category change needs successor evaluation and recalibration; preserve old datasets and released schemas. Extend approved training seeds synthetically while keeping real holdouts independent. [Conditional field semantics](https://typellm.ai/docs/workflows)

### Vega: a distinct adaptation path

The article describes pooling state/question/options from a frozen backbone, projecting them into a 64-dimensional latent space, and integrating a damped trajectory through answer-dependent potential wells. A learned thermal readout maps the settled state to probabilities; conformal sets and abstention supplement the result. These are author-described mechanisms, not a validated physical world model. [Author's architecture explanation](https://www.nandakishorm.com/writing/vega)

The pinned implementation exposes `choice`, `boolean`, `score`, with an engine mode and a fitted `ttt` mode. The latter requires examples but lacks the engine's calibration/conformal/abstention claims. The README's context length is 73,728 tokens and its checkpoint contains 0.8B/4B frozen-backbone variants; these sizes/context claims do not establish usable VRAM or latency for every input. The source describes separate per-question reads except where long-state prefix reuse applies. [Pinned implementation/README](https://github.com/NandhaKishorM/vegaml/tree/01d2850408084034e27174621c19e01f0f0b9e92)

Vega's published adapter recipe caches frozen features, trains a gated adapter and refits thermal/conformal parameters. Its script uses emotion labels and news negatives, so adapting it to a domain needs a new loader and question. Reserve selection, calibration and test data independently rather than copying its combined validation procedure. The upstream README describes a 96-row laptop mechanics run, which is insufficient to establish performance for a full Kaggle two-T4 study. [Pinned training script](https://github.com/NandhaKishorM/vegaml/blob/01d2850408084034e27174621c19e01f0f0b9e92/notebooks/vega_finetune.py)

Conformal coverage depends on the calibration method and exchangeability assumptions. Before using abstention to automate a domain workflow, refit and measure coverage, selective error and review rate under representative traffic and domain shift.

## Benchmark analysis and counterevidence

### Usage ranking

The captured OpenRouter page ranks Jev first by requests and lists other routes including Luna Decisions, Clef Flash/Clef, Perplexity Decider, d1, Mercury Decide, Kev and Solar Decide. Request counts can reflect price, free tiers, integrations and exposure. They do not answer which model is correct on your domain. The captured period is through 9 October; do not present it as a live current leaderboard after this snapshot. [Usage methodology and table](https://openrouter.ai/rankings/decisions)

### Vega's selected author-run results

The checkpoint card dated 10 October reports phishing accuracy of 75.38% for Vega and 61.88% for live Jev on 800 balanced emails, with recall around 0.63 and 0.25. Overall ECE over 5,096 paired decisions is 9.5 and 9.3 in the card's scale; selected task wins do not imply better overall calibration. The local T4 median of 267 ms and hosted Jev median of 591 ms have different hardware and execution boundaries. These are author-run, selected-task results. [Pinned card](https://huggingface.co/nandakishorm/vega-08b-public-intents/blob/c85f2338fcc965f5ab0050a5752200e86fa8f63b/README.md)

### Exact JevBench v1.6.6 and the newer v1.6.7

Version 1.6.6 evaluates nine self-hosted systems on a fresh 1,500-item draw, including 1,200 sealed items. The benchmark's generic manifest still reports v1.6.1, so the versioned artifact is the authority for this review. Its board's cap-filtered capability headline is Wald; RSI-Jev has higher uncapped capability but misses the cost limit. Neither this draw nor its historic Jev reference establishes a same-draw win over current hosted Jev. [Versioned page](https://benchmarkheaven.com/jev-models/v1.6.6)

| v1.6.6 system | Intelligence score | Calibration score | Capability | Composite A | Estimated USD / 1,000 decisions |
|---|---:|---:|---:|---:|---:|
| Wald 4B v2.1 | 51.95 | 86.59 | 69.27 | 54.29 | 0.06172 |
| RSI-Jev v6.1-VL 27B | 61.56 | 85.35 | 73.46 | 21.59 | 0.19737 |
| Standard One 8B SH | 34.46 | 85.50 | 59.98 | 18.75 | 0.07816 |
| Clef-omni | 34.48 | 82.15 | 58.32 | 6.07 | 0.27557 |
| Liquid d1-3B | 22.14 | 76.87 | 49.51 | 8.86 | 0.04446 |
| Vega 4B | 16.69 | 58.68 | 37.69 | 3.60 | 0.05772 |
| Vega 0.8B | 3.05 | 62.94 | 32.99 | 0.04 | 0.01924 |

The full nine-row extraction, including Gutsy/d1-omni, is in the CSV. Capability is an average of benchmark axes; Composite A is a penalized weighted harmonic score. Neither is raw percent accuracy. Version 1.6.7 adds Blink on the **same** draw with unchanged earlier point scores: capability 69.95, Composite A 61.16 and estimated cost 0.04776/1,000. Retain revised uncertainty and the evaluator's repeatability modification. [v1.6.6 artifact](https://benchmarkheaven.com/api/jevbench/v1.6.6), [v1.6.7 artifact](https://benchmarkheaven.com/api/jevbench/v1.6.7)

The frozen field median changes public/sealed gap penalties; do not compare these Intelligence values directly with the older v1.6.1 board. Refusals/errors remain in the denominator. Raw local latency is separately adjusted by `2× + 0.15s`, an assumed hosted-equivalent surcharge, not measured API latency. Every cost here is an estimate based on a frozen market reference or override, not a model invoice or actual GPU total cost. The method metadata retains the scorer hashes, sample counts and bootstrap details. [Evaluator method](https://github.com/fstandhartinger/jevbench), [local method record](provenance/benchmark-expansion-method-2026-10-10.json)

### New candidate models and serving caveats

| Candidate | Reviewed mechanism / distinguishing detail | What to qualify before selection |
|---|---|---|
| [Wald 4B v2.1](https://huggingface.co/org2ai/Wald-4B/blob/ce3286f7efd332530e5dccd8a17d6496e029f714/README.md) | Qwen3.5-4B fine-tune; one-pass and thought-assisted modes | The shipped serving profile moves intermediate noul probabilities to decisive boundaries; evaluate raw versus served calibration and thought-mode repeatability |
| [RSI-Jev v6.1-VL 27B](https://huggingface.co/shgao/rsi-jev-v6.1-vl-27b/blob/d0dccc052ee844ab7481ddfacad1b7335ab394e0/README.md) | Merged fine-tunes with decision heads at three depths; early exit and multimodal input | Large memory profile; its card discloses trunk selection on the benchmark it reports, requiring fresh domain gold |
| [Blink v0.3 26B-A4B NVFP4](https://huggingface.co/PixilabAI/Blink-v0.3-26B-A4B-NVFP4/blob/08caad20c1574fb76341ca809ff38fdb081f7d4a/README.md) | Quantized Gemma-based option-letter readout using a generated token/log probabilities | Freeze prompt/token mapping/temperature; evaluator enabled batch-invariant serving after repeatability failures |
| [Vega](https://huggingface.co/nandakishorm/vega-08b-public-intents/blob/c85f2338fcc965f5ab0050a5752200e86fa8f63b/README.md) | Frozen backbone plus engine/adapter and uncertainty mechanisms | Broad transfer, context boundaries, domain calibration, operator runtime and exact revisions |

These are research candidates; none is a newly qualified platform trainer or serving adapter. Apache tags on cards do not eliminate separate backbone, dataset and distribution review. For an enterprise model shortlist, retain rules and the CPU baseline and compare these candidates against Jev and a domain-trained Laya model on matched operational gold.

## Source conflicts and implementation priorities

Two Vega issues require revision-aware handling. The evaluator pinned Hub revision `73a76a4f6c4fe672e0daa2d45ddc355afc5bc9dd`, whose retrieved card lacks a license field; the current `c85f233...` card adds Apache-2.0. That license-metadata discrepancy is explained by the pins. Package, engine/checkpoint and frozen backbone licensing still deserve separate checks.

The current author README/card says oversized inputs are refused; the evaluator records 29 right-truncated states on its vegaml 0.6.1/pinned-checkpoint run. This review cannot establish a universal no-truncation guarantee or its cause. Test the exact installed source/checkpoint and adapters at the boundary, preserving evidence instead of choosing the favorable claim. Both statements and pins are retained in the method record.

## API example review
