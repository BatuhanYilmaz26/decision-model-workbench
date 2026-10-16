# Model training and runtimes

[Package directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

These adapters consume reviewed domain data and produce complete model exports with calibration and reload checks. Choose a backend through the linked workflow rather than running a module with guessed options or dependencies.

| Path | Entry point | Required guide |
|---|---|---|
| Native Laya | [laya_runner.py](laya_runner.py) | [Laya training](../../docs/training-guide.md#native-laya-adapter) |
| Unsloth | [unsloth_runner.py](unsloth_runner.py) | [Unsloth adapter](../../docs/training-guide.md#unsloth-adapter) |
| Vega | [vega_runner.py](vega_runner.py) | [Vega data and execution profile](../../docs/vega-guide.md) |
| Liquid native data | [liquid_data.py](liquid_data.py) | [CPU preparation for the official d1-3B notebooks](../../docs/models/liquid.md); standalone data export, not a managed trainer |
| Semantic review | [semantic_runner.py](semantic_runner.py) | [Semantic audit](../../docs/semantic-data-guide.md) |
| Exported model serving | [runtime.py](runtime.py) | [Platform setup](../../docs/setup.md#configure-model-training-and-serving), [deployment](../../docs/deployment-guide.md) |

The [CPU comparison baseline](../platform/baseline.py) is available through the application without a GPU. Keep the selected [dependency profiles](../../requirements/README.md), [independent evaluation](../../docs/evaluation-guide.md) and [frozen error policy](../../docs/error-policy-guide.md) when selecting a decision-head backend.

For rented GPUs, follow [remote training](../../docs/remote-training-guide.md) and its [provider directory](../../docs/remote-training/README.md). Returned exports require [remote admission](../../docs/remote-admission-guide.md). Vega's additional applicability data and study route are explained separately in its guide.
