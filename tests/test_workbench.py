import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from decision_workbench.data import (
    read_records,
    prepare,
    validate,
    jsonl,
    state_key,
    write_splits,
)
from decision_workbench.synthetic import generate

SPEC = json.loads(Path("examples/support/spec.json").read_text())


def records():
    return read_records(
        Path("examples/support/seeds.csv").read_bytes(), "seeds.csv", SPEC
    )


class DataTests(unittest.TestCase):
    def test_csv_roundtrip_and_all_primitives(self):
        rows = records()
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["gold"]["team"]["probabilities"]["billing"], 1)
        self.assertEqual(rows[0]["gold"]["refund"]["probabilities"]["true"], 1)
        self.assertEqual(rows[1]["gold"]["urgency"]["probabilities"]["2"], 1)
        self.assertEqual(read_records(jsonl(rows).encode(), "rows.jsonl"), rows)

    def test_invalid_probabilities(self):
        for value in (float("nan"), float("inf"), -0.1, 1.1, True):
            row = records()[0]
            row["gold"]["refund"]["probabilities"] = {"false": 0, "true": value}
            with self.assertRaises(ValueError):
                validate(row)

    def test_unknown_label_and_incomplete_targets(self):
        row = records()[0]
        row["gold"]["team"]["probabilities"]["unlisted"] = 0
        with self.assertRaises(ValueError):
            validate(row)
        row = records()[0]
        del row["gold"]["refund"]
        with self.assertRaises(ValueError):
            validate(row)

    def test_unnormalized_target(self):
        row = records()[0]
        row["gold"]["refund"]["probabilities"] = {"false": 0.3, "true": 0.6}
        with self.assertRaises(ValueError):
            validate(row)

    def test_groups_and_identical_states_cannot_leak(self):
        rows = records()
        extra = copy.deepcopy(rows[0])
        extra.update(id="duplicate", group_id="another-customer")
        rows.append(extra)
        split = prepare(rows)
        for field in ("group_id", "state"):
            values = [
                {state_key(r) if field == "state" else r[field] for r in part}
                for part in split.values()
            ]
            for i, a in enumerate(values):
                for b in values[i + 1 :]:
                    self.assertFalse(a & b)
        self.assertEqual(sum(map(len, split.values())), len(rows))
        self.assertEqual(prepare(rows), prepare(rows))

    def test_review_required(self):
        rows = records()
        rows[0]["metadata"]["reviewed"] = False
        with self.assertRaises(ValueError):
            prepare(rows)

    def test_conflicting_labels_rejected(self):
        rows = records()
        extra = copy.deepcopy(rows[0])
        extra["id"] = "conflict"
        extra["gold"]["refund"]["probabilities"] = {"false": 1, "true": 0}
        rows.append(extra)
        with self.assertRaises(ValueError):
            prepare(rows)

    def test_synthetic_seed_lineage_stays_in_train(self):
        rows = records()
        extra = copy.deepcopy(rows[0])
        extra.update(
            id="generated",
            group_id="generated-family",
            state="A fictional refund request",
        )
        extra["metadata"] = {
            "origin": "synthetic",
            "reviewed": True,
            "seed_ids": ["t01"],
        }
        rows.append(extra)
        split = prepare(rows)
        ids = {r["id"] for r in split["train"]}
        self.assertTrue({"t01", "generated"} <= ids)
        self.assertTrue(
            all(
                r["metadata"]["origin"] == "real"
                for name in ("validation", "calibration", "test")
                for r in split[name]
            )
        )

    def test_small_real_corpus_fails(self):
        with self.assertRaises(ValueError):
            prepare(records()[:3])

    def test_preserve_previous_split(self):
        with tempfile.TemporaryDirectory() as folder:
            write_splits(prepare(records()), folder)
            with self.assertRaises(ValueError):
                write_splits(prepare(records()), folder)

    def test_xlsx_import(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("optional xlsx extra")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            ["id", "group_id", "state", "team", "refund", "urgency", "reviewed"]
        )
        sheet.append(
            [
                "x1",
                "gx1",
                "Please refund the duplicate charge.",
                "billing",
                True,
                0,
                True,
            ]
        )
        workbook.create_sheet("Not the import sheet")
        workbook.active = 1
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        row = read_records(content.getvalue(), "example.xlsx", SPEC)[0]
        self.assertEqual(row["gold"]["refund"]["probabilities"]["true"], 1)


class GenerationTests(unittest.TestCase):
    def test_request_export_is_validated_and_unreviewed(self):
        sample = records()[0]

        def transport(request, timeout):
            self.assertEqual(timeout, 120)
            body = json.loads(request.data)
            self.assertEqual(body["model"], "teacher-local")
            content = json.dumps(
                {
                    "rows": [
                        {
                            "state": "Please refund my duplicated charge.",
                            "gold": sample["gold"],
                        }
                    ]
                }
            )
            return io.StringIO(
                json.dumps(
                    {
                        "model": "teacher-local",
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": content}}
                        ],
                    }
                )
            )

        result = generate(
            SPEC,
            [sample],
            base_url="http://localhost:8000/v1",
            model="teacher-local",
            count=1,
            request=transport,
        )
        self.assertEqual(result[0]["questions"], SPEC["questions"])
        self.assertFalse(result[0]["metadata"]["reviewed"])
        self.assertEqual(result[0]["metadata"]["seed_ids"], [sample["id"]])
        with self.assertRaises(ValueError):
            prepare(result + records())

    def test_bad_teacher_output_rejected(self):
        def transport(request, timeout):
            return io.StringIO(
                json.dumps({"choices": [{"message": {"content": '{"rows":[]}'}}]})
            )

        with self.assertRaises(ValueError):
            generate(
                SPEC,
                [],
                base_url="http://localhost:8000/v1",
                model="teacher",
                count=1,
                request=transport,
            )

    def test_remote_plaintext_and_invalid_count_rejected(self):
        for url, count in (
            ("http://remote.example/v1", 1),
            ("https://example.com/v1", 51),
        ):
            with self.assertRaises(ValueError):
                generate(SPEC, [], base_url=url, model="teacher", count=count)


if __name__ == "__main__":
    unittest.main()
