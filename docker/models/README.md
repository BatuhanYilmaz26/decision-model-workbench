# GPU model images

[Container directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

[Dockerfile.vega](Dockerfile.vega) builds the separate Linux/amd64 Vega and semantic-encoder runtime with the selected CUDA dependency profile. The [model container guide](../../docs/model-container-guide.md) covers image construction, approved offline assets, device access, authentication and lifecycle verification.

| Task | Guide or file |
|---|---|
| Prepare native Perplexity full-weight training | [Dockerfile.perplexity](Dockerfile.perplexity), [five-provider cloud guide](../../docs/remote-training/perplexity.md); experimental Linux/CUDA image with large memory/storage requirements |
| Prepare a selected D3 training image | [Dockerfile.d3](Dockerfile.d3), [D3 cloud workflow](../../docs/remote-training/d3.md); experimental until qualified on your selected GPU/provider |
| Prepare a Liquid training image | [Dockerfile.liquid](Dockerfile.liquid), [Liquid cloud workflow](../../docs/remote-training/liquid.md); experimental until qualified on your selected GPU/provider |
| Train or serve a Vega domain model | [Vega workflow](../../docs/vega-guide.md) |
| Audit possible related examples | [Semantic data workflow](../../docs/semantic-data-guide.md) |
| Inspect dependencies and advisories | [Dependency profiles](../../requirements/README.md), [runtime compatibility](../../docs/compatibility-and-upgrades.md) |
| Run API/worker with the model image | [Compose override](../../compose.models.yaml), [container instructions](../../docs/model-container-guide.md) |
| Keep the browser in the CPU image | [CPU container guide](../../docs/container-guide.md) |

Native Laya and Unsloth use their own compatible model environments; this image does not provide those training paths. Follow [Laya/Unsloth training](../../docs/training-guide.md) and the [shared remote-training protocol](../../docs/remote-training-guide.md) when preparing another runtime.
