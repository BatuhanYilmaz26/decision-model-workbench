# Evaluate decisions and the application

[All guides](README.md) · [Project overview](../README.md)

Evaluate the model and the application it supports. Set quality, error-cost and coverage thresholds with the domain owner before inspecting the locked test, then apply them to the exact exported serving runtime.

## Four dataset roles

| Split | Permitted use |
|---|---|
| Training | Update weights; include reviewed synthetic candidates |
| Validation | Choose prompts, schemas, hyperparameters and stopping rules |
| Calibration | Fit probabilities and select operating thresholds |
| Test | Estimate final frozen-model performance |

Group by source/customer family; add a later-time test for evolving policies. Repeated model selection against test turns it into validation.

Use the [temporal reservation contract](temporal-data-guide.md) for timestamped operational studies. The worker and four-file runners require prior decisions and available labels to precede the next role, in addition to checking source families and exact states. Passing this contract validates declared chronology; it does not establish semantic independence or trustworthy source timestamps.

Use the [reviewed semantic workflow](semantic-data-guide.md) to propose related states with a pinned local encoder before reserving roles. Confirmed relationships stay together; pending candidate reviews and changed input versions cannot qualify a required reservation. Preserve the exact audit threshold, encoder files, package versions and owner review revisions. Candidate similarity is not an automatic label or proof that unflagged examples are independent. Qualify its recall and false positives on domain examples, including counterfactuals and negation. Independence review exposes state inputs to reviewers; it must not become repeated model selection on the locked test.

## Measure more than accuracy

Record accuracy, macro-F1, critical-label precision/recall, confusion matrices and class prevalence. Compare majority/prior baselines. Report NLL and Brier against trustworthy labels, ECE with bin definitions and bin counts, and ordinal MAE/critical-level recall for score questions.

Keep metric definitions: multiclass Brier can sum or average over options; soft-target distance differs from hard-label Brier; expected-score error differs from modal-class error.

Slice results by input length, question/option count, language, policy version and evidence availability. A constant predictor can look calibrated yet ignore the input; calibration must accompany discrimination, coverage and business loss. [typed-decisions prior baseline](https://huggingface.co/datasets/LocalLLaMA/typed-decisions)

## Calibrate and abstain

Fit temperature on calibration data and measure on untouched test. A global positive temperature generally preserves categorical argmax but changes probabilities and expected ordinal scores. It does not repair wrong labels or guarantee transfer. [Calibration research](https://proceedings.mlr.press/v70/guo17a.html)

Sweep thresholds and plot coverage versus error among accepted cases. Binary probability-of-yes and confidence-of-correctness are different quantities. Record fallback frequency, quality and latency.

Estimate uncertainty by resampling source groups; repeated questions from one record are not independent. Compare models on paired cases. A few perfect samples say little about rare harmful errors.

Replace illustrative targets with domain requirements: critical recall, maximum accepted error, minimum automated coverage, p95 latency and cost per successful case.

## Challenge the model

If generalization is required, test unseen schemas/labels, reordered options, paraphrased instructions, irrelevant text, negation, missing evidence, longer states and more questions. Include unseen customers, languages and policy boundaries.

Test prompt-injection text as input data. Verify that permissions, arithmetic and state changes remain correct even when the model selects a wrong valid outcome.

For workflows, measure final success, unsafe actions, recovery, human escalation, call count, total cost and episode latency. Fast local decisions alone do not establish composed-agent reliability. [JEVal system experiments](https://arxiv.org/html/2610.03935v1)

## Measure the deployed runtime

Separate cold load from warmed p50/p95/p99. Match inputs, concurrent load and error-counting rules. Include preprocessing, network, timeout, retry, abstention and fallback. Record hardware, precision, tokenizer, context, batching and utilization.

Quantized and merged exports need their own evaluation. Compare reloaded probabilities against the training runtime, especially near thresholds. A different head or ignored temperature changes the system.

Archive specification/policy, data hashes, model/head/tokenizer revisions, temperatures, dependencies, training configuration, frozen predictions, metric definitions, resource measurements and rollback artifacts.

The runner records model aggregates and preparation reports. Add the question-level and business-outcome measurements above to your experiment pipeline; they are not all produced automatically.

Category discovery is part of model selection. Keep [taxonomy-selection examples](taxonomy-guide.md) and known relatives out of validation, calibration and test, including successor studies. A human-approved taxonomy still needs independent domain gold and error-cost evaluation.

## Costly errors and critical-label acceptance

Define [domain error costs and critical-label limits](error-policy-guide.md) before the first study. The frozen policy reports annotation-weighted forced-decision loss, positive/negative support, false-negative/false-positive rates and nominal Wilson intervals. Release checks preserve those limits; provider billing, deployed abstention/review behavior and real operational impact require separate measurement.
