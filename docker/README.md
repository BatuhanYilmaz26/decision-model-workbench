# Container entry points

[Project overview](../README.md) · [All guides](../docs/README.md)

Choose the CPU application image for data review, preparation, comparison training and local API serving. Use the separate model image when you need the supported Linux Vega or semantic-encoder runtime.

| File | Purpose | Guide |
|---|---|---|
| [Application Dockerfile](../Dockerfile) | Build the Linux/amd64 CPU application image | [CPU container setup](../docs/container-guide.md) |
| [Regression Dockerfile](Dockerfile.checks) | Add Git to a test-only image for the full Linux suite | [Linux regression](../docs/container-guide.md#run-the-full-linux-regression) |
| [compose.yaml](../compose.yaml) | Run API, worker and browser with local persistence | [CPU services and credentials](../docs/container-guide.md) |
| [Model-image directory](models/README.md) | Build the separate Vega/semantic runtime | [Model containers](../docs/model-container-guide.md) |
| [compose.models.yaml](../compose.models.yaml) | Apply the model-runtime Compose configuration | [GPU container workflow](../docs/model-container-guide.md) |

Prepare private credentials and approved model assets using the selected guide. Build and run its lifecycle verifier on your own machine; a dependency lock does not establish that your GPU, volumes or deployment configuration work. These are single-host workflows. Use [deployment](../docs/deployment-guide.md) for enterprise cloud integration and [recovery](../docs/recovery-guide.md) for backups.
