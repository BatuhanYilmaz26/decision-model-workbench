# Guide directory

[Project overview](../README.md)

Choose a workflow below. Start with local setup for the browser application, API integration for an existing project, or remote training when you need a rented GPU. Each guide explains its prerequisites, inputs, steps and relevant limits.

## Start and choose a model

| Guide | Use it to |
|---|---|
| [Streamlit application user guide](app-guide.md) | Follow every screen, optional workflow, role and recovery action, starting with a first project |
| [Local setup](setup.md) | Install the application, provision a credential and start its services |
| [Complete project workflow](product-workflow.md) | Describe a domain, prepare data, train, evaluate and release a model |
| [Support-routing walkthrough](domain-adoption-guide.md) | Follow a concrete domain example with clear data and evaluation requirements |
| [Capabilities](capabilities.md) | Understand the supported local workflows and their limits |
| [Decision-model concepts](concepts.md) | Understand typed outcomes, probabilities and decision policies |
| [Model landscape](model-landscape.md) | Compare hosted and open-weight model options |
| [Benchmarks](benchmarks.md) | Interpret published scores, costs, latency and uncertainty |

## Build and review domain data

| Guide | Use it to |
|---|---|
| [Synthetic candidate generation](synthetic-generation-guide.md) | Preview and approve requests, inspect outcomes, review candidates and retry explicitly |
| [Data preparation and synthetic generation](data-guide.md) | Define questions, import files, review labels and add training-only synthetic candidates |
| [Preview and import](import-guide.md) | Map files, validate all records and save or append a batch for review |
| [Teacher-assisted schema drafting](schema-assistant-guide.md) | Request, review and edit proposed decision questions before creating a project |
| [Reviewer assignments](review-workflow.md) | Assign annotation work and review immutable dataset revisions |
| [Temporal boundaries](temporal-data-guide.md) | Keep future information and related source families out of held-out evaluation |
| [Explicit evaluation reservations](reservation-guide.md) | Assign source groups or date cutoffs, preview counts and confirm an immutable study |
| [Semantic review](semantic-data-guide.md) | Inspect possible related examples before reserving a study |
| [Category discovery](taxonomy-guide.md) | Review proposed categories and confirm a successor schema |
| [Applicability data](applicability-guide.md) | Prepare positive and negative examples for deciding when a model applies |
| [Policy versions](policy-versioning-guide.md) | Change a domain policy while retaining earlier project and release contracts |

Configure the teacher in [setup](setup.md#configure-a-teacher), then follow [synthetic generation](data-guide.md#generate-and-review-synthetic-candidates). The [example directory](../examples/README.md) provides data formats and configuration templates.

## Train, evaluate and integrate

Browse the [model and workflow directory](models/README.md) for delivered routes
and separate references to models whose integrations are still in development.

| Guide | Use it to |
|---|---|
| [Laya and Unsloth training](training-guide.md) | Select a compatible local model environment and produce a complete export |
| [Vega training](vega-guide.md) | Use Vega's separate engine, domain-adapter and applicability contracts |
| [Liquid hosted d1/d1-ultra](models/liquid-hosted.md) | Call the direct API or evaluate and release a hosted domain policy |
| [Perplexity Full/Flash v1.2](models/perplexity.md) | Configure your key, evaluate joint-question decisions and release a calibrated hosted policy |
| [Perplexity open native workflow](models/perplexity-native.md) | Convert reviewed data on CPU; use the experimental Linux/CUDA full-weight trainer and offline domain predictions |
| [Liquid d1-3B](models/liquid.md) | Prepare native domain data, run standalone local LoRA training and use a calibrated export |
| [Liquid d1-omni-600M](models/liquid-omni.md) | Adapt the native decision head on text/JSON, calibrate and use a domain export in the local app |
| [Liquid remote GPU training](remote-training/liquid.md) | Prepare model-specific images/studies for five providers and independently admit returned exports |
| [Decision 3.0 family](models/d3-family.md) | Choose among six pinned native profiles, prepare variant-specific data and provision their CPU/GPU/cloud resources |
| [D3 Edge](models/d3.md) | Prepare native targets, train locally or through the [five-provider cloud workflow](remote-training/d3.md), and release/use a calibrated model |
| [Evaluation and calibration](evaluation-guide.md) | Assess class-level quality, probabilities, abstention and independent reload |
| [Error costs and critical outcomes](error-policy-guide.md) | Freeze domain-specific release criteria before training |
| [API integration](api-integration-guide.md) | Call decision providers, validate responses and handle failures |
| [Application blueprints](application-blueprints.md) | Add a bounded decision to an existing application's workflow |
| [Hosted decision studies](hosted-decisions-guide.md) | Evaluate a provider and fit a local calibration policy through the platform |

## Train on remote GPUs

Follow the [shared remote-training protocol](remote-training-guide.md) and [provider selection](remote-training/provider-selection.md) first. The [provider directory](remote-training/README.md) indexes all five direct walkthroughs.

| Provider | Direct submission | Application-managed workflow |
|---|---|---|
| AWS | [SageMaker AI](remote-training/aws.md) | [Managed AWS](managed-cloud-training.md) |
| Azure | [Machine Learning](remote-training/azure.md) | [Managed Azure](managed-azure-training.md) |
| Google Cloud | [Custom GPU jobs](remote-training/gcp.md) | [Managed GCP](managed-gcp-training.md) |
| Modal | [GPU Functions](remote-training/modal.md) | [Managed Sandboxes](managed-modal-training.md) |
| RunPod | [GPU Pods](remote-training/runpod.md) | [Managed RunPod](managed-runpod-training.md) |

Use [remote model admission](remote-admission-guide.md) to verify and register a returned export. Provider adapters are experimental; complete the [account-specific checks](verification.md#provider-compatibility-checks) before sending enterprise data.

## Deploy and operate

| Guide | Use it to |
|---|---|
| [Deployment](deployment-guide.md) | Plan local serving and custom-container enterprise cloud integration |
| [CPU containers](container-guide.md) | Run the local API, worker and browser with persistent storage |
| [Model containers](model-container-guide.md) | Run the separate Linux Vega/semantic GPU image |
| [Authentication](authentication-guide.md) | Provision and rotate credentials or configure trusted access tokens |
| [Audit exports](audit-export-guide.md) | Capture and verify a bounded immutable project audit window |
| [Backup and recovery](recovery-guide.md) | Capture a workspace and restore it into a fresh directory |
| [Encrypted recovery](encrypted-recovery-guide.md) | Encrypt a backup and verify it against a retained receipt |
| [Scheduled backups](scheduled-backups-guide.md) | Configure local capture, durable receipts and retention |
| [Backup monitoring](backup-monitoring-guide.md) | Publish and verify signed backup-health summaries |
| [Windows process ownership](windows-process-guide.md) | Understand training, inference and cancellation process controls |

## Verify and extend

| Guide | Use it to |
|---|---|
| [Testing and compatibility](verification.md) | Check an installation and distinguish fixtures from real model/provider qualification |
| [Runtime compatibility and upgrades](compatibility-and-upgrades.md) | Select compatible versions and review current interfaces and advisories |
| [Architecture](architecture.md) | Understand the API, persistence, worker, browser and model boundaries |
| [Optional tools](tools.md) | Select teacher, registry, tracking or enterprise-data integrations |

See [dependency profiles](../requirements/README.md), [helper commands](../scripts/README.md), [tests](../tests/README.md), [research](../research/README.md) and [contributing](../CONTRIBUTING.md) for the supporting files.
