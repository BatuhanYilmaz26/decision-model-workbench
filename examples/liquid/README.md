# Liquid d1-3B domain examples

[All examples](../README.md) · [Liquid guide](../../docs/models/liquid.md) · [Project overview](../../README.md)

These fictional examples describe a bounded operation-selection task. The model
chooses a permitted operation, flags a need for review and rates whether the
request contains enough information. Application code still authorizes and
executes any action.

1. Read [the specification](spec.json), then replace its policy/questions with
   your own domain and data needs.
2. Adapt [the CSV template](examples.csv), or use the application's manual entry
   and CSV/XLSX/TSV/JSON/JSONL [import workflow](../../docs/import-guide.md).
3. Review labels and reserve independent operational evaluation records. These
   six pending examples are not an adequate train/evaluation dataset.
4. Follow [native data export](../../docs/models/liquid.md#export-native-data) to
   produce the correct TRL and Unsloth representations for your reviewed study.

The [prediction request](request.json) uses this example policy. For a model
trained on your own policy, keep its original questions and replace the evidence;
follow [fine-tuned inference](../../docs/models/liquid.md#use-the-fine-tuned-export).

`operation` is a named choice; `needs_review` is true/false; `completeness` is a
zero-based rubric level. Each `group_id` identifies the original evidence family.
Keep repeated or derived examples from one family in one data role.

For synthetic coverage, describe the missing cases to a configured generative
teacher, use training-only seeds and review its candidates. The exporter derives
token aliases from reviewed canonical labels; the teacher does not need to
produce model-specific option codes.
