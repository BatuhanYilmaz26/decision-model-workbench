# Model training and runtimes

[Package directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

These adapters consume reviewed domain data and produce complete model exports with calibration and reload checks. Choose a backend through the linked workflow rather than running a module with guessed options or dependencies.

| Path | Entry point | Required guide |
|---|---|---|
| Native Laya | [laya_runner.py](laya_runner.py) | [Laya training](../../docs/training-guide.md#native-laya-adapter) |
| Unsloth | [unsloth_runner.py](unsloth_runner.py) | [Unsloth adapter](../../docs/training-guide.md#unsloth-adapter) |
| Vega | [vega_runner.py](vega_runner.py) | [Vega data and execution profile](../../docs/vega-guide.md#vega-model) |
| Liquid native data | [liquid_data.py](liquid_data.py) | [CPU preparation for the official d1-3B notebooks](../../docs/models/liquid.md); standalone data export, not a managed trainer |
| Perplexity native data | [perplexity_data.py](perplexity_data.py) | [Choice/Noul preparation and publisher training handoff](../../docs/models/perplexity-native.md); standalone export, not a managed trainer |
| Perplexity native app/CLI | [perplexity_runner.py](perplexity_runner.py), [runtime](perplexity_runtime.py), [prediction](perplexity_predict.py) | [Experimental Linux/CUDA training](../../docs/models/perplexity-native.md#train-with-the-native-linux-launcher), [application study/release](../../docs/models/perplexity-native.md#train-through-the-application) and offline prediction; separate from the hosted connector |
| Liquid local training | [liquid_runner.py](liquid_runner.py), [liquid_runtime.py](liquid_runtime.py) | [d1-3B application training/release](../../docs/models/liquid.md#train-and-release-through-the-localhost-application) and [standalone training](../../docs/models/liquid.md#train-with-the-local-trl-runner) |
| Liquid omni | [liquid_omni_runner.py](liquid_omni_runner.py), [liquid_omni_runtime.py](liquid_omni_runtime.py) | [Native head adaptation, calibration and application use](../../docs/models/liquid-omni.md) |
| D3 family | [Pinned variants](d3_profiles.py), [native data](d3_data.py), [trainer](d3_runner.py), [runtime](d3_runtime.py) | [Model/resource selection](../../docs/models/d3-family.md), [local application and CLI](../../docs/models/d3.md), [five-provider cloud workflow](../../docs/remote-training/d3.md) |
| Semantic review | [semantic_runner.py](semantic_runner.py) | [Semantic audit](../../docs/reservation-guide.md#semantic-relatives) |
| Exported model serving | [runtime.py](runtime.py) | [Platform setup](../../docs/setup.md#configuration--configure-model-training-and-serving), [deployment](../../docs/application-hosting/README.md#native-model-serving) |

The [CPU comparison baseline](../platform/baseline.py) is available through the application without a GPU. Keep the selected [dependency profiles](../../requirements/README.md), [independent evaluation](../../docs/evaluation-guide.md#evaluation) and [frozen error policy](../../docs/evaluation-guide.md#error-cost-policy) when selecting a decision-head backend.

For rented GPUs, follow [remote training](../../docs/remote-training-guide.md#study-protocol) and its [provider directory](../../docs/remote-training/README.md). Returned exports require [remote admission](../../docs/remote-training-guide.md#returned-model-admission). Vega's additional applicability data and study route are explained separately in its guide.

For Liquid's separate Unsloth environment and `--trainer unsloth`, follow [FastVisionModel training](../../docs/models/liquid.md#train-with-fastvisionmodel-and-unsloth). It retains the native d1 readout and uses a different backend from the existing Unsloth decision-head adapter.
