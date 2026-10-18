# Call Perplexity with your own API key

[Project overview](../../README.md) · [Example directory](../README.md) · [Perplexity guide](../../docs/models/perplexity.md)

Use [request.json](request.json) as a fictional single-question text/JSON request.
Replace its state, question and criteria with your domain. Install the
[local application requirements](../../requirements.txt) in a virtual environment
and follow the guide's masked key prompt before running from the repository root:

```bash
python examples/perplexity/predict.py --request examples/perplexity/request.json
python examples/perplexity/predict.py --request examples/perplexity/request.json --model pplx-decider-flash-v1.2
```

Each command makes one potentially billable request to your account. The
[Python example](predict.py) validates the request and response and returns JSON
containing canonical probabilities, usage and identity limitations. It exits
with code 2 if the request cannot be completed or validated. Wire that exit to
your fallback; it never retries automatically.

The direct example has no persistent ledger or enforced daily quota across
invocations. Use the [application workflow](../../docs/models/perplexity.md#evaluate-and-release-in-the-localhost-application)
for consent, call accounting, calibration, release and idempotent inference.
The supplied record is illustrative, not training or evaluation gold. Perplexity
decision models score outcomes; use a separate [teacher](../../docs/synthetic-generation-guide.md)
to generate training candidates for human review.
