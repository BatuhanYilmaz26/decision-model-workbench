# D3 retrieval-evidence examples

[All examples](../README.md) · [D3 guide](../../docs/models/d3.md) · [Project overview](../../README.md)

These fictional examples illustrate a policy for selecting an answer, another
search or human review from a query and retrieved passages. The model also
identifies conflicting passages and rates evidence sufficiency. It does not
generate an answer, perform a search or authorize an action.

1. Read and adapt the [specification](spec.json) to your domain and decision policy.
2. Use the [CSV layout](examples.csv) to supply examples, or use the shared
   [file/manual intake](../../docs/import-guide.md). JSON evidence is quoted inside
   the CSV `state` column; labels use the specification's names and outcome keys.
3. Review each label before approving it. The six rows are pending synthetic
   illustrations, not operational gold or an adequate training/evaluation dataset.
4. Reserve independent real validation, calibration and test groups. Follow the
   [D3 data and training guide](../../docs/models/d3.md) for native conversion,
   fine-tuning, calibration and inference.

The [request](request.json) uses the same schema. Keep your trained model's exact
questions when replacing its evidence. `sufficiency` labels are zero-based.
Reuse a `group_id` for examples derived from the same source family; those
examples belong in one data role.

To request synthetic coverage, describe missing, partial, contradictory and clear
evidence to a configured generative teacher. Provide only reviewed training seeds,
review its output, and retain the reserved evaluation sets. The D3 exporter
converts canonical labels into answer-code targets; the teacher does not need
to generate token IDs or letter codes.
