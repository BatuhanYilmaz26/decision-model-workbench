# Decision Model Workbench

Use a hosted decision API or train a model for your own domain. Define the allowed outcomes, bring examples, optionally generate more training candidates, then evaluate and use the result through the browser app or an API.

The workbench is local-first and designed to be model-agnostic and provider-agnostic through replaceable integrations. Each supported integration has its own data, runtime and hardware requirements. The CPU application needs no GPU or account; hosted services and native models need their selected credentials or model environment.

## Choose your path

| I want to… | Follow this walkthrough |
|---|---|
| Use a decision model in my application | [Make an API call and integrate the result](docs/api-integration-guide.md) |
| Train a model on my own data | [Create a project, train, evaluate and use it](docs/product-workflow.md) |
| Generate training data for my domain | [Configure a teacher, generate and review examples](docs/synthetic-generation-guide.md) |

For the browser workflows, install and start below. A direct hosted API call needs the Python installation but no running workbench server.

## Run on your computer

Install [Git](https://git-scm.com/downloads) and [Python](https://www.python.org/downloads/). Use **Python 3.13 on Windows** or **3.12 on Linux** for the application profiles. macOS follows the Unix commands, but end-to-end support has not been qualified. GPU packages belong in a separate model environment.

### Windows PowerShell

```powershell
git clone https://github.com/BatuhanYilmaz26/decision-model-workbench.git
cd decision-model-workbench
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
domain-platform run
```

<details>
<summary>PowerShell blocks virtual-environment activation?</summary>

After creating `.venv`, use its Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m decision_workbench.platform.cli run
```

</details>

### Linux and macOS (Bash or Zsh)

```bash
git clone https://github.com/BatuhanYilmaz26/decision-model-workbench.git
cd decision-model-workbench
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
domain-platform run
```

If Linux reports missing `venv`/`ensurepip`, install your distribution's matching Python 3.12 venv package and recreate the environment.

### Open the application

Open the printed address, normally **http://127.0.0.1:8501**. Your personal workspace connects automatically, without a workspace key. Keep the terminal running; **Ctrl+C** stops the services. Closing the browser tab does not stop them.

Projects persist in `data/local-workspace`. To return later, activate the same environment and run `domain-platform run`. Use `--api-port 8081 --ui-port 8502` if the default ports are occupied. Run `deactivate` after stopping to leave the environment.

`requirements.txt` installs the editable project and CPU/UI dependencies; `-e .[ui,platform]` is a pip requirement, not a standalone shell command. It does not install model weights or every optional integration. [Python venv reference](https://docs.python.org/3/library/venv.html), [pip requirements reference](https://pip.pypa.io/en/stable/reference/requirements-file-format/).

## Train your own domain model

Follow the [complete training walkthrough](docs/product-workflow.md). It covers examples, review, independent evaluation splits, optional synthetic data, local or cloud training, release and predictions. Choose the model you need; the CPU baseline is an optional way to learn the workflow without downloading weights.

## Generate synthetic training data

The [generation walkthrough](docs/synthetic-generation-guide.md) explains teacher configuration, describing coverage gaps, reviewing candidates and adding them to training. A generative teacher writes candidates; a decision-scoring model does not. Real evaluation data stays separate.

## Use an existing model through its API

The [API walkthrough](docs/api-integration-guide.md) starts with a runnable call using your own key, then explains how to adapt it and handle probabilities, errors and fallback. Hosted calls can incur charges and transmit the submitted evidence.

## Train on a remote GPU

Use the [five-provider directory](docs/remote-training/README.md) for AWS, Azure, GCP, Modal or RunPod. Follow only the selected model/provider instructions. Renting a training GPU is separate from hosting the application.

## Where decision models can help

Suitable tasks include document classification, search relevance, content review, agent workflow selection, feedback categorization and maintenance triage. Customer-support routing and chatbot escalation are further examples: your application supplies relevant evidence and invokes the model at the decision points you choose. These are application ideas, not prebuilt domain solutions. Application code owns authorization and actions; uncertain results need a fallback.

## What the application supports

The browser covers projects, imports and review, synthetic candidates, training or hosted evaluation, model releases, predictions and audit history. Shared deployments add PostgreSQL, organization sign-in and versioned object storage. Follow [application hosting](docs/application-hosting/README.md#application-hosting) to deploy on AWS, Azure or GCP.

Native models require their compatible runtimes, weights and resources. Hosted/cloud adapters and larger model profiles retain documented experimental limits. The historical GPU dependency profiles need advisory review before production use. Validate your chosen account, hardware and domain quality; repository tests do not establish production readiness.

<details>
<summary>Supported models and integrations</summary>

| Route | Guide |
|---|---|
| CPU comparison baseline | [Training walkthrough](docs/product-workflow.md) |
| Laya / Unsloth | [Training reference](docs/training-guide.md) |
| Vega | [Vega workflow](docs/vega-guide.md#vega-model) |
| Liquid d1-3B / d1-omni | [d1-3B](docs/models/liquid.md), [omni](docs/models/liquid-omni.md) |
| Decision 3.0 family / Edge | [Family](docs/models/d3-family.md), [Edge](docs/models/d3.md) |
| Open Perplexity Decider | [Native workflow](docs/models/perplexity-native.md) |
| Hosted providers | [TypeSafe/Jev, Liquid, OpenRouter, Perplexity and Cloudflare contracts](docs/api-integration-guide.md#choose-the-contract-before-the-model) |
| Teachers / category discovery | [Synthetic data](docs/synthetic-generation-guide.md), [TypeLLM category discovery](docs/domain-schema-guide.md#category-discovery) |

The [model directory](docs/models/README.md) lists exact capabilities, dependencies and additional publisher references. API-only integrations have no local trainer. Published models may support modalities that this workbench's adapters do not.

</details>

## Guide index

Open the [documentation home](docs/README.md) for the three walkthroughs and an expandable reference catalog. The [app manual](docs/app-guide.md#screen-manual) explains individual screens and troubleshooting; it is not required reading before starting.

<details>
<summary>Research, developer references and repository layout</summary>

Research comparisons are dated snapshots, with sources and methodology: [concepts](docs/concepts.md#decision-concepts), [benchmarks](docs/benchmarks.md), [model landscape](docs/concepts.md#model-landscape) and [research directory](research/README.md). Check current publisher terms and pricing before choosing a service.

<a id="repository-layout"></a>

| Location | Purpose |
|---|---|
| [decision_workbench/](decision_workbench/README.md) | Data contracts, API, workers and model adapters |
| [examples/](examples/README.md) | Fictional data and configuration templates |
| [requirements/](requirements/README.md) | Optional model/provider environments and pinned profiles |
| [docker/](docker/README.md) | CPU and model images |
| [scripts/](scripts/README.md) | Helper commands and checks |
| [tests/](tests/README.md) | Behavioral tests and provider fixtures |
| [.github/](.github/CI.md) | CI checks |

[Architecture](docs/architecture.md#architecture) · [Contributing](CONTRIBUTING.md) · [Compatibility and advisories](docs/compatibility-and-upgrades.md).

</details>


## License

Original code and documentation use [Apache-2.0](LICENSE). Models, dependencies and cited content retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Private data, credentials, weights and local planning files are excluded from Git.
