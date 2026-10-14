# Research provenance

These records identify the sources and versions behind the project's guides. They contain public URLs, retrieval dates, source/checkpoint revisions, hashes and selected benchmark results. You do not need to read or configure them to use the application.

| Files | Purpose |
|---|---|
| [Source snapshot](source-snapshot.json), [10 October expansion](expansion-sources-2026-10-10.json), [12 October expansion](expansion-sources-2026-10-12.json) | Identify the source versions used by the model comparisons |
| [JevBench extract](benchmark-extract.csv) and [method](benchmark-method.json) | Preserve selected published results and their scoring/cost assumptions |
| [Expanded benchmark extract](benchmark-expansion-2026-10-10.csv) and [method](benchmark-expansion-method-2026-10-10.json) | Preserve the later selected comparisons and their source identities |
| [Decision Index snapshot](decision-index-snapshot.json) | Record the pinned aggregate methodology and selected scores |
| [Current runtime sources](runtime-sources-2026-10-14.json) and [earlier runtime sources](runtime-sources-2026-10-13.json) | Identify official API, package and advisory references for the compatibility guide |
| [AWS](cloud-aws-sources-2026-10-11.json), [Azure](cloud-azure-sources-2026-10-11.json), [GCP](cloud-gcp-sources-2026-10-11.json), [Modal](cloud-modal-sources-2026-10-11.json), [RunPod](cloud-runpod-sources-2026-10-11.json) | Pin the provider documentation, schemas or SDK members used by the cloud walkthroughs |
| Other `*-sources-*.json` records | Identify references for individual data, evaluation, UI, API and recovery guides |

For example, the Modal record identifies the selected SDK wheel and hashes of its relevant source modules. That helps trace a version-specific API claim without distributing the downloaded SDK or its generated files. It is optional research metadata, not an application dependency lock.

Hashes identify the captured bytes; they do not establish that a publisher's benchmark claim is correct. Compare the scoring method, dataset, model revision, hardware, latency boundary and cost basis before combining results. Raw downloaded pages and local execution logs are kept outside the public repository.

Use the [annotated source register](../sources.md) for readable explanations and original links, the [benchmark guide](../../docs/benchmarks.md) for interpretation, and [research maintenance tools](../../scripts/README.md) when adding a new dated capture. Preserve historical records and extracts rather than overwriting their identities.
