# Benchmark evidence and its limits

Compare decision models using the published benchmarks below. Each result belongs to a particular model version, dataset draw, scoring method and execution environment. The tables contain publisher-reported results and extracts from benchmark artifacts dated **9 October 2026**; they are not new measurements made by this repository.

The [comparison dated 12 October](../research/decision-model-refresh-2026-10-12.md) adds Vega's 3,841-decision author-run study and the v1.6.6/v1.6.7 artifact identities. Keep these studies separate from earlier draws and fitted-model results. The fixed versions provide reproducible comparison references rather than a live leaderboard.

## What actually beats Jev?

Cloudflare's October 1 report supplies same-suite comparisons. Selected percentages:

| Task / metric | Clef | Clef-flash | Jev |
|---|---:|---:|---:|
| BFCL, case exact | 98.47 | 98.76 | 95.75 |
| BANKING77, macro-F1 | 94.20 | 90.93 | 79.74 |
| CLINC150+OOS, macro-F1 | 97.43 | 66.77 | 89.27 |
| When2Call, accuracy | 72.37 | 65.58 | 80.97 |
| BRIGHT, nDCG@10 | 45.91 | 39.26 | 47.52 |
| Median latency, ms | 209.3 | 38.8 | 524.1 |

Clef leads on several tasks, Jev leads on others, and Clef-flash trades some quality for latency. The reported timings belong to Cloudflare's run, not a universal local-versus-network comparison. [Cloudflare report](https://blog.cloudflare.com/clef-decision-models/)

Perplexity's **v1** card reports an 11-benchmark aggregate of **85.71% versus Jev's 84.51%**, with Jev ahead on BBH and public-hard JevBench. The **v1.1** card reports Decision Index **61.56 versus 57.9** for Jev. These are different evaluations and versions. [v1 card](https://huggingface.co/perplexity-ai/pplx-decider-v1-27b), [v1.1 card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b)

These reviewed sources report higher scores and a lower hosted input tariff for Perplexity on selected comparisons; they do not establish universal superiority. The [$0.02 tariff reviewed on 9 October](https://docs.perplexity.ai/docs/decisions/quickstart) is separate evidence from the benchmark and may change.

## Benchmark map

| Evaluation | Measures | How to use it |
|---|---|---|
| TypeSafe workflow evals | Probabilistic agreement with large-model reference judgments in fixed workflows | Assess workflow fit; reference agreement is not human truth |
| Hugging Face Jev Decision Index | Multiple task families, aggregate and per-task views | Pin version, checkpoint, adapter, task subset and public/private split |
| LocalLLaMA typed-decisions | Four workflows, 400 test states and 2,000 questions | Keep zero-shot and train-fitted results separate |
| Benchmark Heaven JevBench | Capability, calibration, latency and modeled cost | Preserve method version and cost basis |
| JevBench coherence project | Metamorphic consistency, rather than the same ranking metric | Test invariance and logical coherence |
| S1Bench / Nimble public suite | Human-labeled task subsets | Inspect majority baseline, individual tasks and sample counts |
| JEVal research | Broad bilingual tasks plus composed system behavior | Treat the October preprint as evidence with a bounded model scope |

Primary methods: [TypeSafe launch](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [Decision Index](https://huggingface.co/spaces/multimodalart/jev-decision-index), [typed-decisions](https://huggingface.co/datasets/LocalLLaMA/typed-decisions), [Benchmark Heaven source](https://github.com/fstandhartinger/jevbench), [coherence project](https://jevbench.github.io/), [Nimble public suite](https://github.com/bespokelabsai/nimble/blob/main/docs/PUBLIC_BENCHMARKS.md), [JEVal paper](https://arxiv.org/abs/2610.03935).

## Decision Index snapshot — 9 October 2026

The captured **Decision Index 0.3.1** metadata at repository commit `32425c2` describes 37 indexed benchmarks. Public scores correct for chance and combine five weighted areas. The full score combines 20% public, 50% private same-skills and 30% private new-domain results; private parts are mapped to the public scale using stock models. It is not percentage accuracy. [Pinned methodology](https://huggingface.co/spaces/multimodalart/jev-decision-index/blob/32425c2/data/methodology.json)

Selected entries from the artifact generated October 7:

| Model | Public skill | Full skill | Coverage / pending |
|---|---:|---:|---|
| Jev 1.13 | 57.96 | 60.11 | 1.0000 / 0 |
| Perplexity v1.1 | 62.25 | 62.75 | 0.9993 / 0.0007 |
| Clef | 61.71 | 53.08 | 1.0000 / 0 |
| Clef-flash | 56.15 | 47.61 | 1.0000 / 0 |
| d1-3B | 48.99 | 40.08 | 1.0000 / 0 |
| d1-omni-600M | 17.90 | 9.45 | 1.0000 / 0 |

The pinned Decision Index results differ from the release-card snapshots above. Clef's public-score advantage reverses on the full score. Perplexity has a small pending share, and paired bootstrap uncertainty is not available in this artifact. Use the actual pinned results and generation metadata rather than treating the edition date or point estimates as a statistically established ranking. [Pinned results](https://huggingface.co/spaces/multimodalart/jev-decision-index/blob/32425c2/data/index.json), [extraction](../research/provenance/decision-index-snapshot.json)

## Domain fitting versus zero-shot

Laya reports typed-decisions accuracy **0.766** for its fitted checkpoint, **0.362** for base English Laya and **0.727** for published Jev. These are not interchangeable checkpoints. Its report also lists fitted Laya's ECE **0.213**, versus Jev's **0.144** in the typed-decisions table: better accuracy here does not mean better calibration. The headline ECE elsewhere uses a different run. [Laya benchmark report](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md)

The dataset card separates general zero-shot models from fitted models. Its captured table lists meraGPT Decider 1 at **0.768**, hosted d1 at **0.742**, and Jev at **0.727**, against teacher-derived labels. Human correctness is not established by teacher agreement; some fitted submissions were not rerun by the maintainer. Latencies also mix hosted, local and submitter-measured hardware. [typed-decisions card](https://huggingface.co/datasets/LocalLLaMA/typed-decisions)

Unsloth reports substantial gains after adding and training a decision head, including 0.8B Qwen moving from 36% to 73% on typed-decisions. This supports testing small adapted models; its hardware and accuracy figures are recipe results, not estimates for every enterprise dataset. The page describes more than one recipe setting, so freeze the actual configuration used. [Unsloth experiment](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth)

## Frozen JevBench extract

[benchmark-extract.csv](../research/provenance/benchmark-extract.csv) contains selected rows from **v1.6.1**, published October 6 and amended through October 8. The extraction preserves source identifiers, measurement dates, raw and adjusted latency, eligibility and price basis.

| System | Capability | Composite | Intelligence |
|---|---:|---:|---:|
| Quyet-1.0-Large | 81.69 | 71.39 | 73.40 |
| decisio on Gemma-4-31B | 79.57 | 71.69 | 70.42 |
| Jev 1.13.0 | 77.13 | 71.49 | 63.62 |
| H2O-Lightning-4B v1.1 | 75.00 | 72.52 | 60.04 |
| Perplexity Decider v1.1 | 82.79 | 20.62 | 75.72 |

Perplexity's high capability value is **outside the official cost cap**, so it is not an eligible capability leader in this artifact. Its self-hosting cost uses a frozen base-model market estimate, not today's $0.02 hosted tariff.

Capability averages Intelligence and Calibration subject to cost/latency caps. Composite uses a weighted harmonic mean and penalties. Neither is raw percentage accuracy. Some latency and cost fields are carried from earlier runs; inspect the CSV before comparing. [Versioned artifact](https://benchmarkheaven.com/api/decision-results/jevbench/v1.6.1/json), [manifest](https://benchmarkheaven.com/api/jevbench/manifest)

Do not reuse old v1.3 geometric-mean explanations for v1.6.1. Do not combine an API invoice, estimated GPU economics and a free promotional tier into a single cost claim.

## Important counterevidence

lev's author evaluated the same pinned 3,880 S1Bench items and reports macro accuracy **0.689 versus Jev's 0.761**. In that report, majority-class baselines beat both models on several imbalanced or rating subsets. Engine time and end-to-end API latency tell different stories. [lev measurements](https://github.com/InterfazeAI/lev)

Liquid's open d1 release reports d1-3B at **48.57 on public Decision Index v0.2.1**, and **8 ms** for a short single-question request on RTX 4090. Its experimental omni model scores **15.95** on that index. These public-only scores and hardware timings cannot be ranked directly against newer private-inclusive boards or hosted round trips. [Open d1 report](https://www.liquid.ai/blog/d1-open)

JEVal introduces 11,257 instances from 36 datasets across 10 domains. The paper predominantly studies Jev when discussing general decision models, rather than establishing results for every new alternative. It reports lower episode time but weaker task success in some long-horizon workflows as errors compound. [JEVal preprint](https://arxiv.org/abs/2610.03935), [full methods](https://arxiv.org/html/2610.03935v1)

## Benchmark review checklist

Before claiming “better and cheaper,” require the same request shape, schema, state length, output task, checkpoint, split and error handling. Distinguish a decision, a question, a state and a complete episode. Report sample counts, class prevalence, uncertainty, calibration definition, actual billing and p95 latency. Evaluate your domain's human-adjudicated data; public rankings are a shortlist.

## 10 October versioned update

The [new research review](../research/decision-model-expansion-2026-10-10.md) analyzes JevBench **v1.6.6** and the subsequently published **v1.6.7**, preserves all nine/ten rows in a [separate dated CSV](../research/provenance/benchmark-expansion-2026-10-10.csv), and records [method/scorer hashes and source conflicts](../research/provenance/benchmark-expansion-method-2026-10-10.json). These releases use a fresh draw and frozen field median; the earlier v1.6.1 extract above remains historical. Do not rank old Jev figures against the new draw or treat estimated base-model prices as invoices. OpenRouter decision rankings are request-volume data, not quality benchmarks. Vega selected task wins coexist with much weaker broad results; its current source claims require pin-aware context/license review.
