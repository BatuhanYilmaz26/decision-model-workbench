# Models and workflows

[All guides](../README.md) · [Project overview](../../README.md)

Choose a workflow by its delivered capability. Training, hosted API access and
data preparation are separate features; a model appearing here does not mean
every model, device or cloud combination is supported.

| Model or route | Available workflow | Start here |
|---|---|---|
| CPU comparison baseline | Local domain training, evaluation, release and predictions | [Complete application workflow](../product-workflow.md) |
| Laya | Native decision-head training and exported-model serving on compatible profiles | [Training](../training-guide.md#native-laya-adapter) |
| Unsloth decision training | Compatible decision-head fine-tuning, export and serving | [Unsloth adapter](../training-guide.md#unsloth-adapter) |
| Vega | Domain adapters, applicability data, evaluation and serving | [Vega workflow](../vega-guide.md) |
| Jev / TypeSafe | Direct hosted requests and application-managed evaluation | [API integration](../api-integration-guide.md), [hosted studies](../hosted-decisions-guide.md) |
| OpenRouter | Hosted decision requests through supported model routes | [OpenRouter integration](../api-integration-guide.md) |
| Perplexity Decider v1.2 / Flash v1.2 | Hosted evaluation and releases; open v1.1 has CPU data conversion and experimental Linux/CUDA app/CLI training, release and prediction. Native managed cloud admission is separate | [Hosted guide](perplexity.md), [native workflow](perplexity-native.md), [API example](../../examples/perplexity/README.md) |
| Cloudflare Clef | Hosted connector and integration guide | [API integration](../api-integration-guide.md) |
| Liquid hosted d1 / d1-ultra | Direct text/JSON API adapter, domain evaluation/calibration and released-policy predictions using your API key | [Liquid hosted guide](liquid-hosted.md) |
| Liquid d1-3B | CPU native-data export, separate TRL and FastVisionModel/Unsloth training profiles, calibrated release and inference | [Liquid data and training guide](liquid.md) |
| Liquid d1-omni-600M | Native text/JSON head adaptation on CPU/CUDA, soft labels, native data export, calibration, release and inference | [Omni guide](liquid-omni.md) |
| Decision 3.0 family | Six pinned native profiles with distinct weights, geometry, data identities and hardware floors; larger profiles are experimental | [Family guide](d3-family.md), [cloud training](../remote-training/d3.md) |
| D3 Edge | Local application/CLI and five-provider cloud studies, native-readout training, soft targets, calibrated exports, release and inference | [D3 workflow](d3.md), [cloud training](../remote-training/d3.md), [examples](../../examples/d3/README.md) |
| Local or hosted generative teacher | Reviewed synthetic candidates and optional schema assistance | [Synthetic generation](../synthetic-generation-guide.md), [schema assistant](../schema-assistant-guide.md) |
| TypeLLM category discovery | Review proposed categories before adopting a schema | [Taxonomy workflow](../taxonomy-guide.md) |

For every training workflow, use [reviewed data](../data-guide.md),
[independent evaluation](../evaluation-guide.md) and a compatible
[dependency profile](../../requirements/README.md). For rented GPUs, the
[five-provider directory](../remote-training/README.md) distinguishes direct
submission instructions from application-managed jobs. It does not establish
that a newly listed model has a qualified cloud runner.

Liquid's [shared cloud workflow](../remote-training/liquid.md) covers its three
text/JSON trainer profiles, explicit image capability and independent admission.
Its image recipe requires qualification on your selected GPU/provider.

## Additional model references

These publisher resources are useful for investigation. Dedicated application
training/serving adapters for the routes below are not delivered here yet.

| Family | Official resources | Important distinction |
|---|---|---|
| Perplexity open Decider | [v1 card](https://huggingface.co/perplexity-ai/pplx-decider-v1-27b), [v1.1 card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b), [current hosted API](https://docs.perplexity.ai/docs/decisions/quickstart) | Downloadable checkpoint versions and hosted aliases can differ. |
| Interfaze Lite | [Model card](https://huggingface.co/interfaze-ai/interfaze-1-lite) | A generative/perception companion, not an interchangeable typed-probability backend. |
| Amos 1 | [Publisher models](https://amos.scira.ai/models/), [API documentation](https://amos.scira.ai/docs/) | Verify model identity, request-retention terms and availability of training artifacts. |
| Decision 3.0 | [Official six-model collection](https://huggingface.co/collections/vllm-sr/decision-30) | [Pinned family profiles](d3-family.md) share the native training path; each variant/runtime/hardware combination needs its own qualification. |

Use the [model landscape](../model-landscape.md) and
[benchmark guide](../benchmarks.md) for dated comparisons. Check the publisher's
current model identity and terms before selecting a route.
