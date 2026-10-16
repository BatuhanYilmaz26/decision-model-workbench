# Preview and import your examples

[All guides](README.md) · [Data workflow](data-guide.md) · [Project overview](../README.md)

Import examples into a new collection or append them to an existing one. The application validates the entire file before saving and previews its first ten normalized records. Every imported record starts pending review, including records with complete labels or an approval flag.

## Before you start

Follow [local setup](setup.md), open your domain project and define its questions and allowed outcomes. Owners and editors can import; reviewers annotate saved examples through the [review workflow](review-workflow.md).

Use CSV, TSV, XLSX, JSON or JSONL. The project CSV template uses your question names. Canonical JSON/JSONL preserves `state/questions/gold/metadata`, including probability targets; see [the record format](data-guide.md#supply-examples-or-upload-real-data). JSON is an array of records; JSONL has one record per nonblank line.

## Import a file

1. Under **Collection for file import**, choose **Create a new collection** or an existing collection. Appending preserves its records and historical versions.
2. Upload your file. For XLSX, select the worksheet with your records; a blank cover sheet can be skipped. This selection also applies to canonical record columns.
3. For a table, enable **Use canonical state/questions/gold record columns** for canonical records. Otherwise select input evidence columns, optional IDs and source families, labels and label-value mappings. Several input columns become JSON with their original field names.
4. Choose observed operational or synthetic/hypothetical origin. Map decision/evidence/label availability timestamps where present. Include only information available at decision time as evidence.
5. Click **Validate and preview import**. Inspect normalized inputs, IDs, source families, outcomes, unknown-outcome counts and destination. Appends show the resulting collection size. Nothing is saved yet.
6. Correct errors and preview again. Validation checks the whole batch, including rows beyond the sample. Changing the file, mapping, source, worksheet or destination invalidates the preview. Another writer advancing the collection also requires a new preview.
7. Click **Save new collection for review** or **Append file to collection**. The uploader resets after a successful save. Open the collection below and approve, correct or reject records before preparation.

The preview displays each target's highest-probability outcome; canonical soft-label distributions are preserved in full when saved. Evidence is shortened to 500 characters only for display. Previewing checks format and append eligibility, not label correctness, source independence or model quality.

## Enter examples without a file

Open **Enter a few examples without a file**. Choose one text/JSON example or **A batch of examples** of up to 50 records. Unknown outcomes can be completed during review. Hypothetical examples default to synthetic origin. Choose a new or existing collection. Failed saves retain your draft input.

## Resolve import errors

| Situation | Action |
|---|---|
| Blank cover worksheet | Select the sheet with unique headers and data. |
| Unknown label | Map it to an allowed outcome, correct the source, or leave the label blank for later review. |
| Duplicate ID | Give distinct examples unique IDs. Correct an existing record through review instead of importing it again. |
| Collection changed | Refresh its latest version and preview again. Previews do not reserve or lock versions. |
| Invalid CSV or UTF-8 | Export a consistently quoted UTF-8 CSV/TSV. Quoted newlines are accepted; unmatched quotes are rejected. |
| Repeated JSON keys or nonfinite numbers | Correct the JSON. Duplicate names, `NaN` and `Infinity` are rejected. |
| Blank formula cells | Export formulas as values. XLSX reads cached values without calculating formulas. |

Invalid rows, duplicate IDs or exceeded capacity prevent the entire batch from being published. Missing labels are allowed in pending records; approval requires complete targets for every question.

Limits are 10 MiB and 20,000 rows per upload, 256 table columns, 64 KB of serialized evidence per record and 20,000 records per collection. XLSX expansion is bounded to 50 MiB and 4,096 archive entries. See [Python CSV rules](https://docs.python.org/3/library/csv.html#csv.Dialect.strict) and [openpyxl read-only behavior](https://openpyxl.readthedocs.io/en/stable/optimized.html#read-only-mode) for the underlying formats. These limits do not establish performance for every deployment.

## Use the API

Authenticate with an owner/editor credential using [the authentication guide](authentication-guide.md). These routes accept multipart form data containing `file`.

| Route | Optional fields and result |
|---|---|
| `POST /v1/projects/{project}/imports/inspect` | `worksheet`; returns raw table headers, sampled values and sheet choices for CSV/TSV/XLSX mapping. |
| `POST /v1/projects/{project}/imports/preview` | `mapping` JSON object, `worksheet`, and paired `dataset`/`expected_hash` for appends. Returns `preview_sha256`, counts, warnings and normalized sample records. Creates no dataset or audit event. |
| `POST /v1/projects/{project}/datasets` | Same fields plus optional `preview_sha256`. When supplied, it must match this actor, project, file, mapping and destination version. The complete batch is revalidated; appends also check the current version inside their transaction. |

The browser always previews before saving. Existing API callers may omit `preview_sha256`; full validation and append checks still apply. The hash is a consistency check, not a credential or signed authorization token.

For mapped XLSX, separate `worksheet` must match `mapping.worksheet`. For canonical XLSX, send `worksheet` directly. Other formats reject worksheet selection. Mapping must be a JSON object of at most 32,000 characters. Appends need both the immutable dataset version ID and expected SHA-256. Conflicts return HTTP 409; invalid data returns HTTP 400. Invalid form-field lengths can return HTTP 422.

Continue with [review](review-workflow.md), [independent reservations](reservation-guide.md), [synthetic generation](synthetic-generation-guide.md) and [data readiness](data-guide.md#inspect-readiness-before-training).
