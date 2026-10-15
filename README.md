# Decision Model Platform

A local-first application and practical guide for using decision models in your own domain. Add a hosted model to an existing application, or build your own model from a natural-language description, uploaded examples and reviewed synthetic training data.

Use it for bounded decisions such as routing support tickets, assigning invoice categories or choosing a review queue. Define the allowed outcomes, evaluate the probabilities and connect the result to your application's rules and fallback.

## Run on your computer

Install [Git](https://git-scm.com/downloads) and [Python](https://www.python.org/downloads/) first. Use Python **3.13 on Windows** or **3.12 on Linux** for the application profiles below; the Linux/macOS commands assume `python3.12` is installed. A GPU, cloud account and workspace key are not required for the local CPU application. macOS instructions use the same Python workflow, but end-to-end macOS support has not been qualified.

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

If PowerShell blocks activation, use the virtual environment directly instead of changing your execution policy. After creating `.venv`, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m decision_workbench.platform.cli run
```

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

If Linux reports that `venv` or `ensurepip` is unavailable, install the matching Python 3.12 venv package using your distribution's package manager, then recreate the environment. If the Python command is missing, install the selected version before continuing.

### Open the application

Open the address printed in the terminal, normally [http://127.0.0.1:8501](http://127.0.0.1:8501) (localhost). The launcher starts the API, worker and browser server and connects your personal workspace automatically. Keep the terminal running while you use the application.

Create a domain project, describe your decision, then enter examples or upload CSV, XLSX, TSV, JSON or JSONL. Follow [the domain-model workflow](#train-your-own-domain-model) to review data, reserve evaluation sets and train the **CPU baseline**. This baseline is a local classifier; Laya, Unsloth and Vega use their separate [model environments](docs/setup.md#configure-model-training-and-serving). Synthetic generation additionally needs a [local or hosted teacher](docs/setup.md#configure-a-teacher).

Projects persist in `data/local-workspace`. Press **Ctrl+C** in the terminal to stop the services. Closing the browser tab does not stop them. To use the application later, open a terminal in the cloned folder, activate the same environment and run `domain-platform run` again. Run `deactivate` after stopping to leave the environment. If the default ports are occupied, use `domain-platform run --api-port 8081 --ui-port 8502` and open the new displayed address.

[requirements.txt](requirements.txt) installs the editable project and its CPU/UI dependencies from [pyproject.toml](pyproject.toml); you do not need a second project-install command. It resolves compatible version ranges rather than freezing an operating-system-specific environment. For exact versions and hashes, use the matching [dependency profile](requirements/README.md). Keep GPU packages in their own environment. See [shared workspaces and advanced setup](docs/setup.md) for team access, providers, configuration and persistence.

Reference: [Python virtual environments](https://docs.python.org/3/library/venv.html), [pip requirements files](https://pip.pypa.io/en/stable/reference/requirements-file-format/).

## Choose your path

Browse the [complete guide directory](docs/README.md), or choose a workflow below.

| What you want to do | What you will use | Start here |
|---|---|---|
| Use a decision model without training one | Hosted API requests, typed response validation and fallbacks | [API integration](docs/api-integration-guide.md), [application blueprints](docs/application-blueprints.md) |
| Train a model for your domain | Browser application, reviewed data, model training and release API | [Local setup](docs/setup.md), [complete workflow](docs/product-workflow.md) |
| Generate additional training examples | A configured local or hosted teacher and your reviewed training seeds | [Synthetic data workflow](#generate-synthetic-training-data) |
| Train when local VRAM is insufficient | A custom training container on one of five GPU providers | [Remote training](docs/remote-training-guide.md), [provider walkthroughs](#train-on-a-remote-gpu) |
| Choose a model and compare results | Model contracts, dated benchmarks and primary sources | [Concepts](docs/concepts.md), [model landscape](docs/model-landscape.md), [benchmarks](docs/benchmarks.md) |

## Train your own domain model

Start with [local setup](docs/setup.md#install-and-start). Install the application and run `domain-platform run` to open a personal localhost workspace without an account or pasted credential. You can also run `streamlit run app.py --server.address 127.0.0.1` and select **Start local workspace**. CPU baseline training needs no GPU; Laya, Unsloth and Vega need their compatible model environments. Use a [shared workspace](docs/setup.md#personal-and-shared-workspaces) for team roles and stable application API credentials.

1. **Describe your use case.** Create a project with your domain, decision policy and data requirements in natural language. Define category, yes/no or rating questions and confirm the allowed outcomes. Follow the [project workflow](docs/product-workflow.md#1-describe-the-decision).
2. **Supply examples.** Enter a few examples or upload CSV, XLSX, TSV, JSON or JSONL. Preview the data, map its columns to the decision format and review the labels. The [data guide](docs/data-guide.md) explains the format and review controls.
3. **Reserve evaluation data.** Prepare independent training, validation, calibration and test sets from reviewed operational records. Inspect coverage and set your quality requirements before training. Follow [data readiness](docs/data-guide.md#inspect-readiness-before-training).
4. **Fill training gaps.** Optionally generate and review synthetic candidates for missing cases. Extend only the training portion of the reserved study, preserving the evaluation sets. Follow [synthetic generation](#generate-synthetic-training-data).
5. **Train and evaluate.** Start with the CPU baseline, then use [Laya or Unsloth](docs/training-guide.md), or the separate [Vega workflow](docs/vega-guide.md). Inspect calibration, class-level results and export/reload checks using the [evaluation guide](docs/evaluation-guide.md).
6. **Release and use the model.** Activate an eligible version, call its authenticated decision endpoint and route abstained or failed decisions to your fallback. Follow [release and integration](docs/product-workflow.md#6-release-and-integrate) and [deployment](docs/deployment-guide.md).

For a concrete support-routing walkthrough, use the [domain adoption guide](docs/domain-adoption-guide.md). Its [domain specification](examples/support/spec.json), [CSV examples](examples/support/seeds.csv), [canonical JSONL examples](examples/support/seeds.jsonl) and [decision request](examples/support/request.json) show the expected inputs. These fictional examples illustrate the format; use independently reviewed operational records to evaluate your own domain.

## Generate synthetic training data

Configure a local or hosted generative teacher using [teacher setup](docs/setup.md#configure-a-teacher). Describe the cases you need, such as uncommon outcomes, missing evidence or different writing styles. In the application, select a reserved snapshot and explicitly approve the generation request; its worker supplies only the reviewed training seeds to that teacher.

Generation creates unapproved candidate records for review. Approve, correct or reject them, then extend the original training snapshot with the reviewed version. Validation, calibration and test data stay reserved. A hosted teacher receives the selected policy and seeds, so use an approved provider or a local teacher when those records must stay on your machine.

Follow [generate and review synthetic candidates](docs/data-guide.md#generate-and-review-synthetic-candidates) for the complete workflow. Generated labels require review, and teacher confidence does not establish domain accuracy.

## Use an existing model through its API

Follow [API integration](docs/api-integration-guide.md) for Python and TypeScript request examples, provider-specific contracts, probability validation, timeouts and fallback handling. The guide covers TypeSafe/Jev, OpenRouter, Perplexity, Cloudflare and TypeLLM, with separate local Vega examples. You need the selected provider's credentials; hosted calls can incur charges and transmit the submitted state.

Use [application blueprints](docs/application-blueprints.md) to add a decision boundary to an existing project. For provider evaluation, calibration and release through this application's UI, follow [hosted decision studies](docs/hosted-decisions-guide.md) and adapt the [hosted profile template](examples/hosted-profiles.json).

## Train on a remote GPU

Prepare the data and custom container using the [shared remote-training guide](docs/remote-training-guide.md), then select a provider. Direct walkthroughs explain operator submission; managed workflows use the application's approval, job-history and artifact-validation controls.

| Provider | Submit directly | Manage from the application |
|---|---|---|
| AWS SageMaker AI | [Boto3 training job](docs/remote-training/aws.md) | [Managed AWS training](docs/managed-cloud-training.md) |
| Azure Machine Learning | [CLI/SDK command job](docs/remote-training/azure.md) | [Managed Azure training](docs/managed-azure-training.md) |
| Google Cloud | [Custom GPU job](docs/remote-training/gcp.md) | [Managed GCP training](docs/managed-gcp-training.md) |
| Modal | [GPU Function and Volume](docs/remote-training/modal.md) | [Managed Modal Sandbox](docs/managed-modal-training.md) |
| RunPod | [GPU Pod and SSH](docs/remote-training/runpod.md) | [Managed RunPod training](docs/managed-runpod-training.md) |

Check [provider selection](docs/remote-training/provider-selection.md), [account compatibility](docs/verification.md#provider-compatibility-checks) and [returned model admission](docs/remote-admission-guide.md) before using enterprise data or releasing a remote export. Providers require their own accounts, permissions, quota and paid compute.

## What the application supports

The local application supports persistent domain projects, file/manual intake, reviewed dataset revisions, synthetic-candidate jobs, model evaluation, versioned releases and authenticated inference. It also provides reviewer queues, owner-defined release criteria, policy successors, audit exports and backup/recovery. The [capability guide](docs/capabilities.md) explains the supported behavior and limits.

Local training supports Laya, Unsloth and Vega through separate model environments. Hosted adapters and managed cloud controllers are experimental; validate provider behavior, model outputs and billing in your account before use. Review synthetic candidates and evaluate model quality on independent domain records. The application uses single-host SQLite and does not provide distributed enterprise storage or interactive enterprise SSO.

Follow [testing](docs/verification.md) and [runtime compatibility and upgrades](docs/compatibility-and-upgrades.md) to check your installation, validate provider access and plan a production rollout. The pinned GPU profiles have known dependency advisories and need remediation and runtime requalification before production use.

## Guide index

| Area | Guides |
|---|---|
| Data | [Collection and review](docs/data-guide.md), [review assignments](docs/review-workflow.md), [policy versions](docs/policy-versioning-guide.md), [temporal boundaries](docs/temporal-data-guide.md), [semantic relatives](docs/semantic-data-guide.md), [taxonomy](docs/taxonomy-guide.md), [applicability](docs/applicability-guide.md) |
| Models | [Laya and Unsloth](docs/training-guide.md), [Vega](docs/vega-guide.md), [evaluation and calibration](docs/evaluation-guide.md), [error costs and critical outcomes](docs/error-policy-guide.md) |
| APIs and remote runs | [Hosted studies](docs/hosted-decisions-guide.md), [provider selection](docs/remote-training/provider-selection.md), [remote artifact admission](docs/remote-admission-guide.md) |
| Managed training | [AWS](docs/managed-cloud-training.md), [Azure](docs/managed-azure-training.md), [GCP](docs/managed-gcp-training.md), [Modal](docs/managed-modal-training.md), [RunPod](docs/managed-runpod-training.md) |
| Operations | [Deployment](docs/deployment-guide.md), [CPU containers](docs/container-guide.md), [model containers](docs/model-container-guide.md), [Windows process ownership](docs/windows-process-guide.md), [authentication](docs/authentication-guide.md), [audit exports](docs/audit-export-guide.md), [backup and recovery](docs/recovery-guide.md), [encrypted recovery](docs/encrypted-recovery-guide.md), [scheduled backups](docs/scheduled-backups-guide.md), [backup monitoring](docs/backup-monitoring-guide.md) |
| Research | [Primary sources](research/sources.md), [10 October comparison](research/decision-model-expansion-2026-10-10.md), [12 October refresh](research/decision-model-refresh-2026-10-12.md) |

Model and benchmark references are dated **9–12 October 2026**; the [runtime and cloud API comparison](docs/compatibility-and-upgrades.md) is dated **14 October 2026**. Prices, aliases, APIs and rankings can change. Benchmark figures are attributed to their publishers; use the pinned versions and methods when comparing results.

## Repository layout

| Location | Purpose |
|---|---|
| [app.py](app.py) | Streamlit interface for projects, review, jobs and releases |
| [requirements.txt](requirements.txt) | Install the local CPU application and browser dependencies |
| [decision_workbench/](decision_workbench/README.md) | Data contracts, API, persistence, workers and model adapters |
| [docs/](docs/README.md) | Complete reader and operator guide directory |
| [research/](research/README.md) | Model comparisons, annotated sources and optional [provenance records](research/provenance/README.md) |
| [examples/](examples/README.md) | Fictional data and configuration templates |
| [requirements/](requirements/README.md) | Separate dependency profiles for platform and model environments |
| [docker/](docker/README.md) | CPU services and the separate Vega/semantic model image |
| [scripts/](scripts/README.md) | Training helpers, research capture and repository checks |
| [tests/](tests/README.md) | Behavioral tests and provider contract fixtures |
| [.github/](.github/CI.md) | Automated checks for pushes and pull requests |

Read [the architecture](docs/architecture.md) for the component flow and [contributing](CONTRIBUTING.md) for development and documentation standards.

## License

Original project code and documentation use [Apache-2.0](LICENSE). Models, libraries, provider schemas and cited content retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Private data, credentials, weights, generated artifacts and local planning records are excluded from Git.
