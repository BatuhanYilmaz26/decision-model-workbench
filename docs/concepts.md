# What you are training

A domain decision model approximates a function:

`state + questions + allowed outcomes → probability distributions`

For example, a ticket becomes probabilities over support teams, a probability that a refund was requested, and a distribution over urgency levels. The application combines these outputs with policy, permissions and deterministic calculations.

The Jev launch introduced TypeSafe's System One interface and described proprietary RLCD training and parallel sampling. Its advertised speed and cost gains come from particular comparisons and are not universal multipliers. [Jev launch](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

## Three common primitives

| Primitive | Output space | Interpretation |
|---|---|---|
| `choice` | Named, mutually exclusive categories | Probability of each supplied option |
| `noul` | False and true | Probability of yes |
| `score` | Ordered, explicitly described levels | Distribution over levels; expected score can be fractional |

These are interface conventions, not proof that all models share an architecture. The Unsloth serving guide uses these primitives and cautions that provider confidence summaries differ. [Unsloth serving](https://unsloth.ai/docs/models/decision-laya)

An expected urgency score of 1.6 is not an integer class or a confidence. Select the appropriate decision rule: probability of a critical condition, most likely category, or expected cost. Keep monetary arithmetic and access authorization in ordinary code.

## Architectural families

1. **Encoder plus decision head.** Laya uses an encoder and a trained decision head. This is an attractive small-model route for domain classification. [Laya](https://github.com/NandhaKishorM/laya)
2. **Decoder backbone plus a dedicated head.** Clef scores option representations through a joint schema head. Non-generative inference does not imply a bidirectional encoder. [Clef model card](https://huggingface.co/Cloudflare/clef)
3. **Constrained option-token readout.** Nimble reads logits for allowed answer codes; some runtimes reuse a state prefill, while others process questions separately. [Nimble implementation](https://github.com/bespokelabsai/nimble)
4. **Serving adapters over existing models.** A wrapper can expose decisions without fine-tuning. Classify it as a runtime or conversion approach, rather than automatically treating it as a newly trained model. [decisio](https://github.com/aminry/decisio)

A single request and a single forward pass are different claims. Read the actual inference path when multi-question latency or billing matters.

## What calibration means

A calibrated probability should track observed correctness on a defined population. Softmax only normalizes numbers; it does not establish this property. Temperature scaling fits a post-processing parameter using held-out data. Its effectiveness is empirical and population-dependent. [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html)

Training with cross-entropy or a proper scoring rule and fitting temperatures are related but distinct operations. Laya's RLCD-named objective is an open implementation with noisy-logit policy-gradient and supervised terms; it is not evidence that TypeSafe's private recipe has been reproduced. [Laya training source](https://github.com/NandhaKishorM/laya/blob/main/laya/train.py)

## Choose the right task boundary

Use decisions for repeated bounded judgments over evidence available at inference. Use a generative teacher to create candidate training records, and a generator when the product needs explanations or new text. The decision model itself does not synthesize your dataset.

For agents, probabilities can select a branch, but the harness must validate the action and handle uncertainty. LangChain illustrates this separation in a Jev-backed loop. [LangChain harness](https://www.langchain.com/blog/building-a-harness-with-jev)

Do not equate a valid label with truth, a model score with legal authority, or local calibration with system-wide reliability. Missing evidence, novel policies, new languages and long multi-step workflows need explicit evaluation and fallback.
