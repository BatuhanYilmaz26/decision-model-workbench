# Continuous integration

[Project overview](../README.md) · [Testing guide](../docs/verification.md)

[checks.yml](workflows/checks.yml) configures checks for pushes and pull requests. It installs the selected Linux and Windows CPU dependency locks, checks dependency consistency, validates public documentation and runs the unit suite. A separate Linux job builds and verifies the CPU application container.

The quick-start jobs create fresh virtual environments on Windows and Linux, install the root [requirements.txt](../requirements.txt), check dependencies and exercise personal workspace startup, the CPU workflow and both launch modes. These jobs keep the [README installation path](../README.md#run-on-your-computer) usable without depending on preinstalled provider SDKs or model packages.

This workflow does not submit paid cloud jobs or qualify every real GPU model. Follow [provider compatibility checks](../docs/verification.md#provider-compatibility-checks), [model container verification](../docs/model-container-guide.md) and [runtime upgrades](../docs/compatibility-and-upgrades.md) for those separate paths.

The Liquid data jobs install the optional CPU-only [data preparation profile](../requirements/liquid-data.txt) on Windows and Linux, check dependencies and exercise export failure/lineage contracts. They do not fetch model weights or run GPU training; offline fixtures do not establish native model quality.

Use [contribution guidance](../CONTRIBUTING.md) when changing the checks. Credentials, private data, generated caches and local execution receipts belong outside the public repository.
