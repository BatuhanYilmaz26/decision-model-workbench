# Third-party terms and attribution

[All guides](docs/README.md) · [Project overview](README.md)

Apache-2.0 applies to this project's original code and documentation. It does not replace the terms for dependencies, model weights, backbones, provider schemas, benchmark data or cited articles. No downloaded model weights are distributed in this repository.

## Model and training components

| Component | Source and licensing boundary |
|---|---|
| Laya | The [pinned source](https://github.com/NandhaKishorM/laya/blob/1adc59f7e371deb601fcfa18a14e25db238addcc/pyproject.toml) and [selected checkpoint card](https://huggingface.co/convaiinnovations/laya/blob/7b928d828b7b0e022f929d9bd2e44165aa270148/README.md) declare Apache-2.0. Check the terms of any replacement checkpoint and its encoder. |
| Unsloth | The [repository-level license](https://github.com/unslothai/unsloth/blob/main/LICENSE) is Apache-2.0, while the [decision-model module](https://github.com/unslothai/unsloth/blob/main/unsloth/models/decision.py) carries `SPDX-License-Identifier: AGPL-3.0-only`. Inspect the exact installed package's component notices when distributing or serving an Unsloth-based system. |
| Vega | The [pinned vegaml metadata](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/pyproject.toml) declares Apache-2.0. Engine/adapter weights and the separately downloaded backbone have their own cards and terms; see the [execution profile](docs/vega-guide.md#freeze-a-complete-execution-profile). |
| Liquid d1-3B data preparation | The optional exporter downloads the exact publisher prompt code, tokenizer and [LFM Open License v1.0](https://huggingface.co/LiquidAI/d1-3B/blob/051bcc464b01b9f92942b364d9586b0ef5912432/LICENSE) at revision `051bcc464b01b9f92942b364d9586b0ef5912432`. Those assets are not distributed under this repository's Apache license. Review the model and backbone terms before training or redistribution; see the [Liquid guide](docs/models/liquid.md). |
| Liquid d1-omni-600M | The [omni profile](docs/models/liquid-omni.md) downloads pinned publisher model code, tokenizer, weights and [LFM Open License](https://huggingface.co/LiquidAI/d1-omni-600M/blob/02b55d7076f15129e59ab3f94783f32c4b088674/LICENSE) at revision `02b55d7076f15129e59ab3f94783f32c4b088674`. The runtime code and license are retained in domain exports; the repository does not relicense or distribute those downloaded weights. |
| Other decision models | Check the exact checkpoint and base-model licenses in the [model landscape](docs/model-landscape.md). Open weights, hosted access and permission to retrain are separate properties. |
| Decision 3.0 family | The [six pinned profiles](docs/models/d3-family.md) retain their publisher LICENSE files declaring Apache-2.0. Prompt rendering follows the publisher's `d3_format.py`, copyright 2026 vLLM Semantic Router Team. Downloaded runtime code, LICENSE and NOTICE remain in domain exports; weights are not distributed in this repository. See the [D3 guide](docs/models/d3.md). |

License declarations above were checked on **13–17 October 2026**. Links to `main` may change; preserve the license and notices from the exact installed artifact for a distribution.

## Libraries, schemas and research

Runtime dependencies are installed separately and retain their upstream notices. The [dependency profiles](requirements/README.md) identify selected versions; this page is not a complete dependency license inventory.

The [provider schema fixtures](tests/fixtures/provider-contracts/README.md) contain excerpts from upstream API specifications for offline compatibility tests. Their upstream terms remain applicable. Source URLs, revisions and capture hashes are retained in the research metadata.

Benchmark extracts are attributed to the publishers in the [benchmark guide](docs/benchmarks.md) and corresponding method files. Cited text, benchmark data and schemas are not relicensed as original project content. Review their terms before redistributing them separately. This repository does not reproduce full articles or claim endorsement by any model or infrastructure provider.
