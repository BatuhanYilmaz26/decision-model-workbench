# Evaluate decisions and release criteria

<a id="evaluate-decisions-and-the-application"></a>

[Documentation](README.md) · [Project overview](../README.md)

Evaluate independent domain records, calibration, abstention and the mistakes that matter to your application. Freeze meaningful owner criteria before the first study; a completed training job alone does not establish useful quality.

## Choose a section

- [Metrics, calibration and operating evaluation](#evaluation)

- [Error costs and critical outcomes](#error-cost-policy)

<a id="evaluation"></a>

## Metrics, calibration and operating evaluation

<a id="evaluation--evaluate-decisions-and-the-application"></a>

Evaluate the model and the application it supports. Set quality, error-cost and coverage thresholds with the domain owner before inspecting the locked test, then apply them to the exact exported serving runtime.

<a id="evaluation--four-dataset-roles"></a>

### Four dataset roles

| Split | Permitted use |
|---|---|
| Training | Update weights; include reviewed synthetic candidates |
| Validation | Choose prompts, schemas, hyperparameters and stopping rules |
| Calibration | Fit probabilities and select operating thresholds |
| Test | Estimate final frozen-model performance |

Group by source/customer family; add a later-time test for evolving policies. Repeated model selection against test turns it into validation.

Use the [temporal reservation contract](reservation-guide.md#time-boundaries) for timestamped operational studies. The worker and four-file runners require prior decisions and available labels to precede the next role, in addition to checking source families and exact states. Passing this contract validates declared chronology; it does not establish semantic independence or trustworthy source timestamps.

Use the [reviewed semantic workflow](reservation-guide.md#semantic-relatives) to propose related states with a pinned local encoder before reserving roles. Confirmed relationships stay together; pending candidate reviews and changed input versions cannot qualify a required reservation. Preserve the exact audit threshold, encoder files, package versions and owner review revisions. Candidate similarity is not an automatic label or proof that unflagged examples are independent. Qualify its recall and false positives on domain examples, including counterfactuals and negation. Independence review exposes state inputs to reviewers; it must not become repeated model selection on the locked test.

<a id="evaluation--measure-more-than-accuracy"></a>

### Measure more than accuracy

Record accuracy, macro-F1, critical-label precision/recall, confusion matrices and class prevalence. Compare majority/prior baselines. Report NLL and Brier against trustworthy labels, ECE with bin definitions and bin counts, and ordinal MAE/critical-level recall for score questions.

Keep metric definitions: multiclass Brier can sum or average over options; soft-target distance differs from hard-label Brier; expected-score error differs from modal-class error.

Slice results by input length, question/option count, language, policy version and evidence availability. A constant predictor can look calibrated yet ignore the input; calibration must accompany discrimination, coverage and business loss. [typed-decisions prior baseline](https://huggingface.co/datasets/LocalLLaMA/typed-decisions)

<a id="evaluation--calibrate-and-abstain"></a>

### Calibrate and abstain

Fit temperature on calibration data and measure on untouched test. A global positive temperature generally preserves categorical argmax but changes probabilities and expected ordinal scores. It does not repair wrong labels or guarantee transfer. [Calibration research](https://proceedings.mlr.press/v70/guo17a.html)

Sweep thresholds and plot coverage versus error among accepted cases. Binary probability-of-yes and confidence-of-correctness are different quantities. Record fallback frequency, quality and latency.

Estimate uncertainty by resampling source groups; repeated questions from one record are not independent. Compare models on paired cases. A few perfect samples say little about rare harmful errors.

Replace illustrative targets with domain requirements: critical recall, maximum accepted error, minimum automated coverage, p95 latency and cost per successful case.

<a id="evaluation--challenge-the-model"></a>

### Challenge the model

If generalization is required, test unseen schemas/labels, reordered options, paraphrased instructions, irrelevant text, negation, missing evidence, longer states and more questions. Include unseen customers, languages and policy boundaries.

Test prompt-injection text as input data. Verify that permissions, arithmetic and state changes remain correct even when the model selects a wrong valid outcome.

For workflows, measure final success, unsafe actions, recovery, human escalation, call count, total cost and episode latency. Fast local decisions alone do not establish composed-agent reliability. [JEVal system experiments](https://arxiv.org/html/2610.03935v1)

<a id="evaluation--measure-the-deployed-runtime"></a>

### Measure the deployed runtime

Separate cold load from warmed p50/p95/p99. Match inputs, concurrent load and error-counting rules. Include preprocessing, network, timeout, retry, abstention and fallback. Record hardware, precision, tokenizer, context, batching and utilization.

Quantized and merged exports need their own evaluation. Compare reloaded probabilities against the training runtime, especially near thresholds. A different head or ignored temperature changes the system.

Archive specification/policy, data hashes, model/head/tokenizer revisions, temperatures, dependencies, training configuration, frozen predictions, metric definitions, resource measurements and rollback artifacts.

The runner records model aggregates and preparation reports. Add the question-level and business-outcome measurements above to your experiment pipeline; they are not all produced automatically.

Category discovery is part of model selection. Keep [taxonomy-selection examples](domain-schema-guide.md#category-discovery) and known relatives out of validation, calibration and test, including successor studies. A human-approved taxonomy still needs independent domain gold and error-cost evaluation.

<a id="evaluation--costly-errors-and-critical-label-acceptance"></a>

### Costly errors and critical-label acceptance

Define [domain error costs and critical-label limits](evaluation-guide.md#error-cost-policy) before the first study. The frozen policy reports annotation-weighted forced-decision loss, positive/negative support, false-negative/false-positive rates and nominal Wilson intervals. Release checks preserve those limits; provider billing, deployed abstention/review behavior and real operational impact require separate measurement.

<a id="error-cost-policy"></a>

## Error costs and critical outcomes

<a id="error-cost-policy--evaluate-costly-mistakes-and-critical-outcomes"></a>

Use an error policy when average accuracy alone cannot express your domain's acceptance criteria. Define mistake costs and critical-label limits before the first training or hosted evaluation job, then compare the exported runtime on reserved validation and test data.

The application freezes this policy alongside accuracy, F1, calibration and sample-count gates. It applies to the CPU baseline, local Laya/Unsloth/Vega reports, calibrated hosted studies and independently remeasured remote exports. Hosted and GPU/cloud execution still require their respective [compatibility checks](verification.md).

<a id="error-cost-policy--choose-the-costs-and-limits"></a>

### Choose the costs and limits

For each selected question, define:

- A complete cost matrix: rows are **true labels**, columns are **predicted labels**. Correct predictions have zero error cost; other entries represent your declared loss scale.
- A unit such as `loss_points` or an owner-estimated monetary amount. These values are not provider prices or measured operational losses.
- A maximum mean error cost across the test examples.
- Optional critical outcomes, with maximum false-negative/false-positive rates and minimum positive/negative test-row counts.

A model that misses one critical event among 100 examples can have 99% accuracy. If that mistake costs 100 loss points, its mean error cost is 1 point. It fails a maximum mean cost of 0.5 even though its average accuracy is high. Cost-based evaluation can expose tradeoffs hidden by symmetric metrics. [Scikit-learn cost-sensitive evaluation](https://scikit-learn.org/stable/auto_examples/model_selection/plot_cost_sensitive_learning.html)

Use the question's exact option IDs. For yes/no questions, use `false` and `true`; ordinal score labels are the zero-based positions in the ordered criteria. [Data contract](data-guide.md#data-contract)

<a id="error-cost-policy--configure-a-policy"></a>

### Configure a policy

Before training, open **Acceptance criteria** in the project's preparation/training tab. Workspace owners use role `admin`. **Guided tables** is the default editor:

1. Select **Add domain error-cost and critical-label gates**. The displayed outcome IDs and meanings come from your confirmed project schema.
2. In **Question cost rules**, tick **Include costs** for each question you want to measure. Set a cost unit, a shared cost for incorrect predictions and the maximum acceptable mean cost. Correct predictions cost zero.
3. In **Specific error costs**, add any asymmetric exceptions using the question, true outcome ID, predicted outcome ID and cost. For a refund question, `false` → `true` represents an incorrect approval. Each pair can appear once. Removing its exception restores the shared cost; unchecking a question omits its costs and overrides.
4. In **Critical outcome limits**, add each critical question/outcome pair once. Set both error-rate limits, both minimum test counts and the rate-bound method. Critical-only policies do not require a cost matrix.
5. Save **acceptance criteria** to apply the selected editor and ordinary quality gates together. Incomplete, duplicate or invalid rows are refused without changing the server policy. Unmodified blank placeholder rows are ignored; editing a placeholder requires completing its IDs and limits.

The initial numbers illustrate the format. Choose and justify criteria for your domain. For yes/no questions, `true` means yes and `false` means no. Rating IDs are zero-based positions in your ordered criteria. A specific error pair must use different allowed IDs.

Choose **Advanced JSON** to import a complete policy or work with large schemas. Only the selected editor is saved. Switching editors discards unsaved changes; saved criteria reload from the workspace. If another owner replaces the policy while you are editing, the retired form cannot overwrite the replacement: review the refreshed settings before saving again.

This fictional refund policy assigns greater loss to an incorrect approval than an incorrect refusal:

```json
{
  "format": "domain-error-policy-v1",
  "questions": {
    "refund": {
      "cost_unit": "loss_points",
      "costs": {
        "false": {"false": 0, "true": 100},
        "true": {"false": 5, "true": 0}
      },
      "max_mean_cost": 1,
      "critical_labels": {
        "true": {
          "max_false_negative_rate": 0.1,
          "max_false_positive_rate": 0.05,
          "min_positive_rows": 100,
          "min_negative_rows": 100,
          "rate_bound": "wilson_95_upper"
        }
      }
    }
  }
}
```

You can configure costs, critical limits, or both for a nonempty subset of project questions. Cost configuration requires `cost_unit`, a complete `costs` matrix and `max_mean_cost` together. Cost values must be finite numbers from 0 to 10¹². Policies are limited to 64 KB after normalization; for large outcome sets, a critical-label-only policy may fit better than a full matrix.

Critical limits require both rate limits and both sample minimums. Counts are integers from 1 to 20,000. `rate_bound` can be `observed` or `wilson_95_upper`; omission selects the latter. All rates are between 0 and 1. A zero Wilson-upper limit cannot pass with a finite sample, even if no errors are observed.

The API accepts this object in `error_policy` on `PUT /v1/projects/{project}/release-policy`, alongside the ordinary criteria and their expected policy ID. Training requests cannot supply or override it. The first training, remote-study or hosted-evaluation submission freezes the criteria. Create a successor project and reserve fresh independent test data if results inform a change.

<a id="error-cost-policy--read-the-results"></a>

### Read the results

The evaluated model panel includes **Domain error costs and critical outcomes**, with validation/test costs, class support and error counts. The complete `error_evaluation` object also records schema/policy hashes and the decision rule.

For every example, evaluation uses the maximum-probability label, with the backend's serving tie order. **Model abstention does not remove examples from these measures.** This prevents a model from concealing error exposure by declining cases. It also means these are forced-classification measures: they do not evaluate your deployed threshold, review queue, applicability fallback or cost-minimizing action policy. Test the full workflow separately. [Evaluation and abstention](evaluation-guide.md#evaluation)

Mean policy cost is the average of the declared error cost for each prediction. With soft gold, it uses normalized gold probability mass:

```text
example cost = sum(gold_probability[true_label] * costs[true_label][predicted_label])
mean policy cost = sum(example costs) / example count
```

This is an annotation-weighted loss estimate, not the model's forecast of financial harm. Question costs remain separate; the application does not add unlike units or treat them as actual provider billing.

For a critical label, false-negative rate is `FN / (TP + FN)` and false-positive rate is `FP / (FP + TN)`. Zero positive or negative support cannot pass. Critical release gates require hard-labeled test records; ambiguous soft-label rows make those gates fail rather than being silently discarded. Targets within 10⁻⁶ of a point mass are treated as hard by the shared metrics.

`observed` enforces the measured rates. `wilson_95_upper` enforces the upper endpoints of nominal, two-sided 95% Wilson intervals. These are per-label intervals, not simultaneous guarantees over many labels, and assume an appropriate binomial sampling model. Repeated, clustered or unrepresentative records and domain drift need additional study. The report keeps point estimates and both interval endpoints. [Wilson interval reference](https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html), [confusion-matrix definitions](https://scikit-learn.org/stable/modules/model_evaluation.html#confusion-matrix)

A model is registered even when evaluation shows a failed criterion, allowing owners to inspect its report. Release is refused if the frozen cost/rate/support criteria fail or the policy evaluation is missing/inconsistent. Registration is not approval to act on model answers.

<a id="error-cost-policy--analyze-a-standalone-report"></a>

### Analyze a standalone report

The following command reads a current report, project specification and error-policy file and prints diagnostics. It does not change files, allocate cloud resources, activate a model or establish that the policy was frozen before test inspection:

```bash
python -m decision_workbench.training.error_policy --report runs/domain/report.json --schema examples/support/spec.json --policy domain-error-policy.json
```

Reports need complete hard-confusion, normalized gold-mass confusion, label, class-count and soft-gold metadata. Older incomplete reports are refused; reevaluate the exact exported runtime to produce the required statistics. Remote admission remeasures the returned runtime and applies the frozen local policy rather than accepting a provider's supplied error claims. No policy fitting or threshold selection uses the locked test.

<a id="error-cost-policy--upgrade-safely"></a>

### Upgrade safely

Back up and stop API, browser and worker services before upgrading. Workspace schema **8** protects frozen error gates, backup-alert semantics and explicit reservation plans from older application versions. Do not run mixed versions against one workspace. Recovery accepts schema-3/4/5/6/7/8 backups and preserves frozen policy/report bindings. [Backup and recovery](recovery-guide.md#local-backups)

An error policy adds evaluation and release gates. It does not change the training objective, calibrate a business decision threshold, optimize actions for minimum expected cost, or establish enterprise data quality. Owners must justify costs, representative independent gold, subgroup adequacy and the deployed action/review policy.

[Primary reference metadata](../research/provenance/error-policy-sources-2026-10-14.json) records the official evaluation/statistical sources retrieved on 14 October 2026.
