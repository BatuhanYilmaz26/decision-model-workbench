# Continuous integration

[Project overview](../README.md) · [Testing guide](../docs/verification.md)

[checks.yml](workflows/checks.yml) configures checks for pushes and pull requests. It installs the selected Linux and Windows CPU dependency locks, checks dependency consistency, validates public documentation and runs the unit suite. A separate Linux job builds and verifies the CPU application container.

This workflow does not submit paid cloud jobs or qualify every real GPU model. Follow [provider compatibility checks](../docs/verification.md#provider-compatibility-checks), [model container verification](../docs/model-container-guide.md) and [runtime upgrades](../docs/compatibility-and-upgrades.md) for those separate paths.

Use [contribution guidance](../CONTRIBUTING.md) when changing the checks. Credentials, private data, generated caches and local execution receipts belong outside the public repository.
