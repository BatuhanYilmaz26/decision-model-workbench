# Model landscape

[All guides](README.md) · [Project overview](../README.md)

Choose a model by its decision contract, training path, deployment requirements and license. The comparison below summarizes primary sources dated **9 October 2026**; announcement dates and checkpoint versions are listed separately.

See the [comparison dated 12 October](../research/decision-model-refresh-2026-10-12.md) for Vega 0.8.0, its updated author-run benchmark and API context/identity details. Earlier entries retain their dated source versions.

## Releases and training relevance

| Model or approach | Evidence and approximate scale | Self-hosting / domain training | Principal caveat |
|---|---|---|---|
| Jev 1.13 | Jev launched 15 September; proprietary size | Hosted reference; current docs explicitly rule out customer LoRA/fine-tuning | Typed choices can be semantically wrong |
| Laya | ModernBERT-large 421M; multilingual mmBERT-base 322M | Open weights, Apache-2.0; native training and Unsloth route | Context and option-description budgets need testing |
| Bespoke Nimble | September releases; Qwen3.5-9B | Open training data and recipe | Runtime sharing and calibration vary by checkpoint |
| Kev | Qwen-based family; Kev-27B v2 card dated 30 September | Apache-2.0 weights and pointer head | Several unrelated projects use the name Kev |
| Strands Decider | Qwen3.5-2B-Base adapter plus readout head; v19 is the reviewed checkpoint | Apache-2.0; substantial reference training recipe | Rubric and long-document transfer are explicitly weak |
| Clef / Clef-flash | 1 October; 27B / 9B Qwen backbones | Apache-2.0; text, image and video decisions | Hosted tariffs exceed Jev's input tariff |
| Perplexity Decider v1 / v1.1 | 27B Qwen backbone; current v1.1 lifts the causal mask | Apache-2.0 card; downloadable/custom-code path, confirm actual access | Hosted API and self-hosted benchmark costs differ |
| Liquid d1 | Hosted vision announcement 5 October | Hosted comparator | Billing can repeat the state across questions |
| Liquid d1-3B / d1-omni-600M | Open release 7 October; 3.1B / 587M | Open weights; LFM Open License v1.0 | Omni is experimental; verify enterprise license terms |
| OpenAI Decisions | Preview at DevDay, public beta 6 October; GPT-6 Luna | Hosted comparator | Distinct API shape; no local weight-training route documented here |
| Interfaze lev | Qwen3.5-4B with LoRA | Apache-2.0 implementation and training harness | Stronger on some tasks, weaker on others |
| Aplomb 1 | October announcement; 5.3B total, Qwen3.5-4B base | Open weights, custom EmpirioLabs license | Long-context fast mode differs from open runtime |

Sources: [Jev models](https://docs.typesafe.ai/models), [Laya](https://github.com/NandhaKishorM/laya), [Nimble](https://github.com/bespokelabsai/nimble), [Kev-27B](https://huggingface.co/jaredpalmer/kev-27b), [Strands v19](https://huggingface.co/StrandsAgents/strands-decider-2B-hobson-v19), [Clef announcement](https://blog.cloudflare.com/clef-decision-models/), [Perplexity v1.1](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b), [Liquid open release](https://www.liquid.ai/blog/d1-open), [d1 license](https://huggingface.co/LiquidAI/d1-3B), [omni card](https://huggingface.co/LiquidAI/d1-omni-600M), [OpenAI changelog](https://developers.openai.com/api/docs/changelog), [lev](https://github.com/InterfazeAI/lev), [Aplomb announcement](https://empiriolabs.ai/blog/introducing-aplomb-1).

The current Liquid open release supersedes the October 5 statement that open weights were forthcoming. Do not assume hosted d1 and d1-3B are identical. [Hosted announcement](https://www.liquid.ai/blog/d1-decision-model)

## Hosted input tariffs

USD per million input tokens, retrieved on the review date. These rates are not costs per decision.

| Service | Input tariff | Source |
|---|---:|---|
| Jev | $0.042 | [TypeSafe models](https://docs.typesafe.ai/models) |
| Perplexity hosted Full v1.2 / Flash v1.2 | $0.02 / $0.01, refreshed 18 October 2026 | [Current API documentation](https://docs.perplexity.ai/docs/decisions/quickstart) |
| Liquid hosted d1 | $0.04 in October 5 evaluation methodology | [Liquid methodology](https://www.liquid.ai/blog/d1-decision-model) |
| Clef | $0.24 | [Cloudflare model page](https://developers.cloudflare.com/workers-ai/models/clef/) |
| Clef-flash | $0.09 | [Cloudflare model page](https://developers.cloudflare.com/workers-ai/models/clef-flash/) |
| OpenAI Luna Decisions | $0.10; regional and long-context modifiers apply | [Decisions pricing](https://developers.openai.com/api/docs/guides/decisions) |

Perplexity is about **52.4% lower** than Jev per input token: `1 - 0.02 / 0.042`. Older $0.04 Perplexity writeups are not the current tariff. This comparison does not resolve tokenization, per-question billing, retries, fallback or accuracy.

The Perplexity price refresh concerns its hosted v1.2 models, not the older open
v1.1 checkpoint or benchmark rows. Retired hosted names now select v1.2 while the
response echoes the requested name. Follow the [Perplexity integration guide](models/perplexity.md)
for the supported application workflow and migration; do not transfer old
benchmark scores or calibration thresholds to hosted v1.2.

Open weights eliminate a vendor inference tariff, not hardware and operating cost. A small specialist may be much cheaper at steady utilization; a lightly used cloud GPU can cost more than an API.

## Shortlist for your use case

These are this project's proposed selection rules:

- **Small text classifier with a stable schema:** Laya plus a simple classifier baseline; compare a 0.8B–4B Unsloth-converted backbone if context or reasoning exceeds the encoder's capabilities.
- **New policies and broader judgments:** evaluate Kev, Perplexity or Clef against domain gold; larger weights carry a substantial deployment cost.
- **Visual inspection or edge inference:** open d1-3B is a candidate; add Clef for a larger multimodal reference.
- **Audio decisions:** investigate d1-omni and Aplomb independently. The text-only pipeline here does not train them.
- **No training requirement:** compare hosted Jev, Perplexity and Luna with the same requests, including fallback cost.

## The wider ecosystem

The current benchmark extract also contains Quyet-1.0-Large, H2O-Lightning-4B and decisio. Quyet and decisio lead Jev on the extracted capability score; H2O-Lightning has a higher composite score. These are benchmark observations, not enterprise qualification. [Frozen results](https://benchmarkheaven.com/api/decision-results/jevbench/v1.6.1/json)

Read their actual artifacts: [Quyet card](https://huggingface.co/chinhnc/Quyet-1.0-Large), [H2O card](https://huggingface.co/h2oai/h2o-lightning-4b), [decisio source](https://github.com/aminry/decisio). Additional names such as JevK5, Tev1, GLiNER2.5-Decide, OpenDecider and frozen-model scoring shims appear on the [Decision Index](https://huggingface.co/spaces/multimodalart/jev-decision-index). Inclusion is not independent verification of training, calibration or deployment maturity.

## 10 October expansion: Vega and additional candidates

The [comparison dated 10 October](../research/decision-model-expansion-2026-10-10.md) covers Vega, Wald 4B v2.1, RSI-Jev v6.1-VL 27B and Blink v0.3 with source/checkpoint pins. Vega uses a frozen backbone and engine/adapter; TypeLLM invoice discovery uses typed generation with conditional category names. Match the training method to that architecture instead of applying a Jev/Laya recipe unchanged. Use the [Vega guide](vega-guide.md) for its platform adapter; Wald, RSI-Jev and Blink require separate integrations.

For hosted application use, see [API integration](api-integration-guide.md). For fitting your own open-weight domain model on remote GPUs, see [the five-provider training guide](remote-training-guide.md).
