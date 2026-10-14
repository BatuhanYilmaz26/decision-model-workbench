# Application package

[Project overview](../README.md) · [All guides](../docs/README.md)

This package implements the application's data contracts, domain workflows, authenticated API, workers and model adapters. Start with [local setup](../docs/setup.md) to use it through the browser; use [architecture](../docs/architecture.md) when extending the code.

| Component | Responsibility | Usage guide |
|---|---|---|
| [Platform](platform/README.md) | Persistence, credentials, API, jobs, releases and operational controls | [Complete workflow](../docs/product-workflow.md) |
| [Training](training/README.md) | Training, calibration, model export and independent runtime loading | [Model training](../docs/training-guide.md) |
| [Browser components](ui/README.md) | Guided intake, review and domain policy controls | [Data preparation](../docs/data-guide.md) |
| [data.py](data.py) | Typed questions, canonical records and import validation | [Data format](../docs/data-guide.md) |
| [synthetic.py](synthetic.py) | Bounded requests to a selected generative teacher | [Synthetic generation](../docs/data-guide.md#generate-and-review-synthetic-candidates) |
| [cli.py](cli.py) | Standalone domain-data commands | [Setup and helper commands](../scripts/README.md) |

The [root browser entry point](../app.py) connects to the authenticated platform API. Model dependencies live in separate [runtime profiles](../requirements/README.md). For an existing application, begin with [API integration](../docs/api-integration-guide.md) and [deployment](../docs/deployment-guide.md).
