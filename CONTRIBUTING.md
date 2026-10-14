# Contributing

[All guides](docs/README.md) · [Project overview](README.md)

Contributions should help readers integrate a decision API, prepare trustworthy domain data, train a model or operate the local platform. Describe the problem, resulting behavior and relevant validation in each pull request. Keep changes focused and preserve existing data/model contracts.

## Documentation

Write for someone who has not read a development conversation. Explain the purpose, prerequisites, inputs, steps, expected output and relevant limits. Keep commands and examples consistent with the actual APIs. Link related guides instead of repeating operational details across pages.

Add each guide to its folder's README and keep every public Markdown page reachable from the root README. Include return navigation to the relevant index and project overview. Link to existing headings when directing readers to a specific step. The repository checker refuses missing files/headings, unreachable pages and omitted entries in the guide, provider and research indexes.

Start a guide with the reader's task and expected result. Place version requirements beside installation or configuration steps, and dates beside claims that can change. Describe limitations as supported behavior, missing functionality or checks the reader must perform. Keep development chronology and execution reports out of walkthroughs. Use the compatibility guide for experimental adapter status and test scope; retain those limits when simplifying the prose.

Cite primary sources next to claims about model behavior, APIs, licenses and benchmarks. Record review dates for prices, rankings and moving interfaces. Benchmark comparisons must identify the model/code revision, draw, denominator, scoring method, hardware, latency boundary and cost basis. Distinguish reported results from independently reproduced measurements, and zero-shot results from fitted results.

Keep task ledgers, agent instructions, local paths, execution diaries and private receipts out of public documentation. Public research metadata may retain URLs, dates and hashes; it must not depend on an ignored raw capture. Use fictional examples, and keep credentials, enterprise data, downloaded weights and generated artifacts outside Git.

Training source identity and benchmark extract checksums bind exact file bytes. The Git attributes preserve those files across checkouts. Keep that boundary when changing formatting or regenerating artifacts; a changed fingerprint requires a new frozen study, and historical receipts retain their original identity.

## Verification

Follow [testing and compatibility](docs/verification.md). Run the repository checker, affected behavioral tests and any relevant container/model check. Verify documentation from a clean public checkout with no ignored local files. Do not describe fixtures as real provider execution, a passing local suite as hosted CI, or fictional model quality as enterprise acceptance.

Retain notices for third-party content and follow [the project license](LICENSE) and [third-party terms](THIRD_PARTY_NOTICES.md). Avoid copying full source articles or redistributing model weights with documentation changes.
