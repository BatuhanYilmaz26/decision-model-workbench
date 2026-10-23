# Model training reference

<a id="train-a-domain-decision-model"></a>

[All guides](README.md) · [Project overview](../README.md)

Training is part of the durable product workflow: confirm policy, import and review data, reserve independent holdouts, define acceptance criteria, queue training, inspect evaluation and release. Follow the [complete training walkthrough](product-workflow.md) for the common browser steps. Use this page for adapter-specific settings and the standalone file protocol. The CLI adapters share the same four-file protocol as the worker.

Before installing a GPU environment, review [runtime compatibility and dependency advisories](compatibility-and-upgrades.md). The historical Laya/Unsloth profile requires remediation and a new GPU qualification before production use. The CPU baseline runs in a separate application environment.

## Choose a training route

In the browser app, select a reserved **Training snapshot**, then choose a **Training backend**. The selector uses the names below. Install the matching environment before starting a model job; the application environment alone installs the CPU baseline.

| Training backend | What you train | Setup and workflow |
|---|---|---|
| CPU comparison baseline (`cpu-baseline`) | A fixed-schema lexical classifier on your CPU | [Local setup](setup.md), [first project](app-guide.md#first-project-walkthrough) |
| Laya (`laya-native`) | The native decision head, with the encoder frozen by default | [Model environment](setup.md#configure-model-training-and-serving), [native adapter](#native-laya-adapter) |
| Unsloth decision fine-tuning (`unsloth`) | A compatible decision checkpoint using its decision trainer | [Model environment](setup.md#configure-model-training-and-serving), [Unsloth adapter](#unsloth-adapter) |
| Vega domain adapter (`vega`) | An adapter with separate positive and out-of-domain examples | [Vega data, training and gate calibration](vega-guide.md) |
| Liquid d1-3B (`liquid-trl` or `liquid-unsloth`) | The same pinned model through one of two distinct training environments | [Liquid setup and training](models/liquid.md) |
| Liquid d1-omni-600M (`liquid-omni`) | The native text/JSON decision head | [Omni setup and training](models/liquid-omni.md) |
| Decision 3.0 variants | The selected variant's native readout, with variant-specific resource requirements | [D3 family and backend names](models/d3-family.md), [D3 workflow](models/d3.md) |
| Perplexity open v1.1 (`perplexity-native`) | The publisher's native full-weight model through an experimental Linux/CUDA profile | [Native setup, data and training](models/perplexity-native.md) |

Each model guide defines compatible questions, labels, input limits, devices and dependencies. A shared backend interface does not make their native formats or environments interchangeable. The **CPU baseline** is a separate choice; an unavailable model backend does not silently become that baseline. Hosted decision APIs use [hosted studies](hosted-decisions-guide.md) rather than local fine-tuning.

Prepare and review examples through the [data workflow](data-guide.md), reserve independent evaluation roles, and optionally add [reviewed synthetic candidates](synthetic-generation-guide.md) to training. The model adapters convert the reserved canonical records into their native formats. Vega also needs its paired applicability datasets; native Perplexity supports Choice/Noul rather than Score training. Follow the selected guide before exporting data for a standalone publisher recipe.

When a job succeeds, inspect its training and [evaluation report](evaluation-guide.md), including role counts, per-question results, calibration and export/reload comparison. Release an eligible version through the [application workflow](product-workflow.md#6-release-and-integrate); a standalone CLI export is not automatically registered. A failed job or export check must be resolved before release. For insufficient local VRAM, use the compatible [remote training workflow](remote-training-guide.md) and its provider/model-specific instructions.

Experimental profiles and unexecuted hardware/image combinations remain identified in their guides. Successful contract tests do not establish model quality on your domain or qualify a different device, image or dependency set.

## Select a comparison

Compare rules, the CPU specialist baseline, an unadapted decision checkpoint and the adapted model on identical domain data. The CPU backend is a fixed-schema lexical classifier; Laya and Unsloth train schema-conditioned decision heads. Fine-tuning cannot repair contradictory policy labels or leaked evaluation data.

## Reserve four splits

Export reviewed `train.jsonl`, `validation.jsonl`, `calibration.jsonl` and `test.jsonl`. Training may include approved synthetic records with training-seed lineage; the other roles require independent operational examples. The adapters reject known record, group and normalized-state overlap plus held-out seed descendants. Use [temporal checks](temporal-data-guide.md) and [semantic review](semantic-data-guide.md) before reserving the study, and verify source independence with the data owner.

Validation monitors training and qualifies export. Calibration alone fits temperatures. The final test report is computed from the exact exported runtime; no temperature fitting or hyperparameter selection uses that test. If findings from a test inform a policy/model change, use fresh independent gold for the successor study.

## Native Laya adapter

The product's native adapter freezes the encoder by default, updates decision-head weights and fits type/option-bucket temperatures only on the reserved calibration file. It exports a complete FP32 safetensors checkpoint, tokenizer, encoder configuration and calibration parameters. It then unloads the training model, reloads the export through the serving loader, compares up to 20 validation probes at a fixed probability tolerance of `1e-6`, and evaluates validation/test through that reloaded model.

```bash
python -m decision_workbench.training.laya_runner --data data/support --model convaiinnovations/laya --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 --out runs/support-native --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
```

Use the UI's **laya-native** backend to obtain registered, releasable artifacts. Standalone CLI exports do not automatically enter the project registry. Native full-encoder training is optional; check memory requirements on your target hardware before enabling it. Evaluate the trained model on independent operational examples before release.

Laya's upstream convenience CLI reserves calibration internally from the supplied training items. The adapter above implements the external four-file protocol explicitly. Sources: [pinned training implementation](https://github.com/NandhaKishorM/laya/blob/1adc59f7e371deb601fcfa18a14e25db238addcc/laya/train.py), [pinned calibration implementation](https://github.com/NandhaKishorM/laya/blob/1adc59f7e371deb601fcfa18a14e25db238addcc/laya/calibrate.py).

## Unsloth adapter

The Unsloth path applies encoder LoRA, trains with `DecisionTrainer`, fits temperatures from reserved calibration, exports a complete merged checkpoint and reloads it with the same serving adapter. Encoder decision checkpoints use full precision at load time and merged 16-bit export; their wrapper does not expose the language-head adapter-save API. Both loading and save behavior are qualified against the installed profile, rather than inferred from general LLM examples. [Official decision-model guide](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth).

```bash
python -m decision_workbench.training.unsloth_runner --data data/support --model convaiinnovations/laya --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 --out runs/support-unsloth --epochs 2 --rank 16 --batch-size 2 --grad-accum 4 --max-length 512
```

The reload gate fixes probability tolerance at `0.005` before training and requires identical decisions on validation probes. It does not promise bitwise equality after merging/casting. Final evaluation uses the merged runtime. The API preserves its trained token budget and refuses truncated evidence, instructions or options.

Public Unsloth prediction rounds probabilities to four decimal places. This project's **2026.10.3-specific** adapter reads calibrated raw logits and served temperatures for both evaluation and inference, keeping a normalized distribution. A version change must pass fresh qualification. Calibration must fit every represented question type; insufficient calibration data fails the run.

Choose a training budget from validation learning curves and domain coverage. Small fictional training runs establish mechanics, not an adequate training budget. Larger language backbones, Clef, four-bit execution and other hardware need separate validation. [Dependency profiles](../requirements/README.md)

## Pin and inspect each run

Remote models require an exact 40-character Hub revision; operator CLI experiments may use an existing local checkpoint directory. Workers accept Hub IDs and pinned revisions, not user-supplied executable checkpoint paths. Checkpoint downloads exclude Python/pickle weights and use local safetensors assets.

Each export has a complete file inventory, sizes and SHA-256 hashes in `manifest.json`; `report.json` records input hashes/counts, training configuration, installed package versions, hardware, optimizer updates, calibration, per-question metrics and reload qualification. Native reports also show a measured decision-head parameter change. Registry identity includes the pinned manifest, schema, model, snapshot and owner criteria.

Per-question reports include confusion, macro-F1, precision/recall, NLL/Brier against probability gold, ten-bin ECE, class counts and Wilson accuracy intervals. Ordinal questions also report absolute error.

## Train without local GPU capacity

Train on [AWS, Azure, GCP, Modal or RunPod](remote-training-guide.md) using the same four data files, pinned checkpoint and export/reload checks. Validate the selected image and account with a small study, then [admit the returned model](remote-admission-guide.md) before release. Use a decision-head trainer; generic managed chat fine-tuning does not implement this contract.

## Vega local application and standalone adapter paths

The [Vega guide](vega-guide.md) covers a separate engine/domain-adapter route with four additional applicability-negative files. The application supports paired reservations, local worker training, applicability criteria and resident serving. Its trainer pins the engine/backbone, checks complete prompts and separates probability/conformal calibration. The upstream recipe below explains the architectural differences; use the project adapter for the application's data contract. Domain efficacy and real cloud execution require separate validation.

The [pinned Vega recipe](https://github.com/NandhaKishorM/vegaml/blob/01d2850408084034e27174621c19e01f0f0b9e92/notebooks/vega_finetune.py) extracts frozen features, trains a gated engine adapter and refits its uncertainty parameters. Its input loader is hard-wired to emotion labels/news negatives; replacing a CSV path is insufficient. For a domain implementation:

1. Confirm a versioned question/criteria contract and map reviewed domain outcomes to its exact label order.
2. Replace the source loader with canonical reviewed rows; reserve independent training, model-selection validation, calibration and locked real test.
3. Cache features against the exact backbone/engine/question/option-order identity, retaining IDs and source-family grouping. Extract only the split used by each stage.
4. Include appropriate out-of-domain gate negatives; verify that the adapter routes only within its intended domain. Never draw those negatives from locked test.
5. Train on training features, select on validation, fit thermal/conformal parameters on calibration, then evaluate the final exported route on locked test.
6. Export adapter metadata/calibration with the base-engine hash and requalify the exact serving mode, long-input refusal, gate behavior and abstention/coverage.

Vega's `fit`/TTT mode is a different fitted readout and does not inherit the engine's calibration/conformal/abstention claims. The application integrates the separately pinned engine/domain-adapter profile. The author provides a Kaggle two-T4 notebook, but reports only a small laptop mechanics run for the training example; do not call the full cloud recipe reproduced. See the [dated source and benchmark review](../research/decision-model-expansion-2026-10-10.md).

## Memory preflight in containers

The hardware screen and the Liquid d1-3B, Omni and D3 CPU guards use the same available-memory check. On Linux, this conservatively combines host availability with inherited cgroup memory limits and current usage, including ancestor limits. Liquid d1-3B, Omni and D3 therefore check the capacity available to their container rather than assuming they can use all host RAM. Perplexity native retains its existing container-aware guard.

Keep the model guide's RAM/VRAM floors and context/batch estimates in view. Increase the container or host allocation, reduce a supported context/batch setting, or use a compatible remote GPU when capacity is insufficient. Unknown limit usage is not free memory, and unreadable limits do not trigger a fallback to unrestricted host capacity. CUDA checks continue to use measured free device memory.

This is a preflight check, not a reservation or a guarantee against an OOM as other workloads change. Keep host/container enforcement in place. [Linux cgroup memory controls](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)
