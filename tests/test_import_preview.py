"""Import previews validate the complete batch without publishing any records."""

import csv
import io
import json
import unittest

from openpyxl import Workbook

from decision_workbench.data import jsonl
from decision_workbench.platform.store import Store, Conflict
import test_guided_intake as fixtures
import test_platform as data


def workbook_bytes(headers, rows, sheet="Records", cover=True):
    book = Workbook()
    if cover:
        book.active.title = "Cover"
        page = book.create_sheet(sheet)
    else:
        page = book.active
        page.title = sheet
    page.append(headers)
    for row in rows:
        page.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def canonical_table(rows, delimiter=","):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), delimiter=delimiter)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: json.dumps(v) if isinstance(v, dict) else v for k, v in row.items()}
        )
    return output.getvalue().encode()


class ImportPreviewTests(unittest.TestCase):
    setUp = fixtures.GuidedIntakeTests.setUp
    tearDown = fixtures.GuidedIntakeTests.tearDown
    rows = fixtures.GuidedIntakeTests.rows

    def preview(self, filename, content, form=None, *, status=200, headers=None):
        response = self.client.post(
            self.prefix + "/imports/preview",
            files={"file": (filename, content)},
            data=form or {},
            headers=headers,
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def save(self, filename, content, form=None, *, status=201):
        response = self.client.post(
            self.prefix + "/datasets",
            files={"file": (filename, content)},
            data=form or {},
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_five_formats_preview_is_read_only_and_save_preserves_targets(self):
        rows = data.fixture_rows(2)
        rows[1]["gold"].pop("refund")
        values = [
            [json.dumps(v) if isinstance(v, dict) else v for v in row.values()]
            for row in rows
        ]
        sources = [
            ("records.json", json.dumps(rows).encode(), {}),
            ("records.jsonl", jsonl(rows).encode(), {}),
            ("records.csv", canonical_table(rows), {}),
            ("records.tsv", canonical_table(rows, "\t"), {}),
            (
                "records.xlsx",
                workbook_bytes(list(rows[0]), values),
                {"worksheet": "Records"},
            ),
        ]
        for name, content, form in sources:
            with self.subTest(name=name):
                before = self.client.get(self.prefix + "/entities/dataset").json()
                with self.app.state.store.transaction() as db:
                    audit_before = db.execute("SELECT count(*) FROM events").fetchone()[
                        0
                    ]
                report = self.preview(name, content, form)
                self.assertEqual(report["row_count"], 2)
                self.assertEqual(report["rows_with_missing_outcomes"], 1)
                self.assertEqual(report["missing_per_question"]["refund"], 1)
                self.assertEqual(report["origin_counts"], {"real": 2, "synthetic": 0})
                self.assertEqual(report["review_status"], "pending")
                self.assertIsNone(report["target"])
                self.assertEqual(
                    self.client.get(self.prefix + "/entities/dataset").json(), before
                )
                with self.app.state.store.transaction() as db:
                    self.assertEqual(
                        db.execute("SELECT count(*) FROM events").fetchone()[0],
                        audit_before,
                    )
                saved = self.save(
                    name, content, {**form, "preview_sha256": report["preview_sha256"]}
                )
                actual = Store(self.home).verify_dataset(saved["id"], "A", self.project)
                self.assertEqual([r["gold"] for r in actual], [r["gold"] for r in rows])
                self.assertTrue(
                    all(
                        not r["metadata"]["reviewed"]
                        and r["metadata"]["review_status"] == "pending"
                        for r in actual
                    )
                )
                self.assertEqual(
                    saved["payload"]["provenance"]["preview_sha256"],
                    report["preview_sha256"],
                )

    def test_mapping_aliases_multiple_inputs_missing_labels_and_soft_targets(self):
        content = b"ID,Family,Subject,Detail,Queue\n1,f1,Invoice,please help,Finance\n2,f1,App,fails,\n"
        mapping = {
            "state_columns": ["Subject", "Detail"],
            "state_mode": "json",
            "id_column": "ID",
            "group_column": "Family",
            "labels": {"team": "Queue"},
            "value_maps": {"team": {"Finance": "billing"}},
            "origin": "synthetic",
        }
        form = {"mapping": json.dumps(mapping)}
        report = self.preview("mapped.csv", content, form)
        self.assertEqual(report["origin_counts"]["synthetic"], 2)
        self.assertEqual(report["preview"][0]["outcomes"], {"team": "billing"})
        saved = self.save(
            "mapped.csv", content, {**form, "preview_sha256": report["preview_sha256"]}
        )
        actual = self.rows(saved)
        self.assertEqual(
            actual[0]["state"], {"Subject": "Invoice", "Detail": "please help"}
        )
        self.assertEqual(actual[0]["group_id"], actual[1]["group_id"])
        soft = data.fixture_rows(1)
        soft[0]["gold"]["team"]["probabilities"] = {"billing": 0.6, "engineering": 0.4}
        body = json.dumps(soft).encode()
        report = self.preview("soft.json", body)
        saved = self.save(
            "soft.json", body, {"preview_sha256": report["preview_sha256"]}
        )
        self.assertEqual(self.rows(saved)[0]["gold"], soft[0]["gold"])

    def test_all_rows_are_validated_beyond_the_display_sample(self):
        body = b"ID,Input,Label\n" + b"".join(
            f"{i},example {i},billing\n".encode() for i in range(25)
        )
        mapping = {
            "state_columns": ["Input"],
            "id_column": "ID",
            "labels": {"team": "Label"},
        }
        form = {"mapping": json.dumps(mapping)}
        report = self.preview("records.csv", body, form)
        self.assertEqual(report["row_count"], 25)
        self.assertEqual(len(report["preview"]), 10)
        self.assertIn(
            "Source row 27",
            self.preview(
                "records.csv", body + b"25,last,invalid-outcome\n", form, status=400
            )["detail"],
        )
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])
        long = b"state,team\n" + b"x" * 1000 + b",billing\n"
        report = self.preview("long.csv", long)
        self.assertEqual(len(report["preview"][0]["input_evidence"]), 500)
        self.assertTrue(report["preview"][0]["input_truncated"])
        saved = self.save(
            "long.csv", long, {"preview_sha256": report["preview_sha256"]}
        )
        self.assertEqual(len(self.rows(saved)[0]["state"]), 1000)

    def test_preview_binding_refuses_file_mapping_name_and_actor_changes(self):
        body = b"state,team\ninvoice,billing\n"
        report = self.preview("records.csv", body)
        form = {"preview_sha256": report["preview_sha256"]}
        self.save("renamed.csv", body, form, status=409)
        self.save(
            "records.csv",
            body.replace(b"invoice", b"invoice changed"),
            form,
            status=409,
        )
        self.save(
            "records.csv",
            body,
            {
                **form,
                "mapping": json.dumps(
                    {
                        "state_columns": ["state"],
                        "labels": {"team": "team"},
                        "origin": "synthetic",
                    }
                ),
            },
            status=409,
        )
        with self.assertRaisesRegex(Conflict, "differs from the preview"):
            self.app.state.service.upload(
                {"tenant": "A", "actor": "another-owner", "role": "admin"},
                self.project,
                body,
                "records.csv",
                preview_sha256=report["preview_sha256"],
            )
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])

    def test_append_checks_duplicate_capacity_stale_targets_and_history(self):
        old = self.save("first.csv", b"id,state,team\n1,initial,billing\n")
        body = b"id,state,team\n2,second,engineering\n"
        target = {"dataset": old["id"], "expected_hash": old["payload"]["sha256"]}
        report = self.preview("next.csv", body, target)
        self.assertEqual(report["target"]["resulting_rows"], 2)
        self.preview("duplicate.csv", b"id,state\n1,duplicate\n", target, status=400)
        self.preview("next.csv", body, {"dataset": old["id"]}, status=400)
        new = self.save(
            "next.csv", body, {**target, "preview_sha256": report["preview_sha256"]}
        )
        self.assertEqual([r["id"] for r in self.rows(new)], ["1", "2"])
        self.assertEqual([r["id"] for r in self.rows(old)], ["1"])
        self.save(
            "next.csv",
            body,
            {**target, "preview_sha256": report["preview_sha256"]},
            status=409,
        )
        self.preview("next.csv", body, target, status=409)
        # Capacity validation needs only the bounded IDs, not old record payloads.
        many = data.fixture_rows(20000)
        full = self.app.state.service.save_dataset(
            {"tenant": "A", "actor": "owner", "role": "admin"},
            self.project,
            many,
            {"fictional": True},
        )
        self.preview(
            "next.csv",
            body,
            {"dataset": full["id"], "expected_hash": full["payload"]["sha256"]},
            status=400,
        )

    def test_canonical_selected_worksheet_is_bound_and_preserved(self):
        rows = data.fixture_rows(1)
        values = [
            [json.dumps(v) if isinstance(v, dict) else v for v in rows[0].values()]
        ]
        content = workbook_bytes(list(rows[0]), values, sheet="Canonical")
        self.preview("book.xlsx", content, status=400)
        report = self.preview("book.xlsx", content, {"worksheet": "Canonical"})
        saved = self.save(
            "book.xlsx",
            content,
            {"worksheet": "Canonical", "preview_sha256": report["preview_sha256"]},
        )
        self.assertEqual(self.rows(saved)[0]["id"], rows[0]["id"])
        self.assertEqual(saved["payload"]["provenance"]["worksheet"], "Canonical")
        self.preview(
            "book.xlsx",
            content,
            {
                "worksheet": "Canonical",
                "mapping": json.dumps(
                    {"worksheet": "Cover", "state_columns": ["state"]}
                ),
            },
            status=400,
        )
        self.preview("text.csv", b"state\nx\n", {"worksheet": "Canonical"}, status=400)

    def test_untrusted_json_and_malformed_files_fail_without_mutation(self):
        bad = [
            ("x.json", b'[{"state":"first","state":"second"}]', {}),
            ("x.jsonl", b'{"state":NaN}\n', {}),
            ("x.json", b"[" * 2000 + b"0" + b"]" * 2000, {}),
            ("x.csv", b'state\n"unclosed\n', {}),
            ("x.csv", b"state,state\na,b\n", {}),
            ("x.csv", b"state\n\xff\n", {}),
            (
                "x.csv",
                b"state\nx\n",
                {"mapping": '{"state_columns":["state"],"state_columns":["other"]}'},
            ),
            ("x.csv", b"state\nx\n", {"mapping": "[" * 2000 + "0" + "]" * 2000}),
            ("x.csv", b"state\nx\n", {"mapping": "null"}),
            ("x.xlsx", b"not a zip", {}),
        ]
        for name, content, form in bad:
            with self.subTest(name=name, form=str(form)[:40]):
                self.preview(name, content, form, status=400)
                self.save(name, content, form, status=400)
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])

    def test_server_bounds_and_roles_apply_to_preview_and_save(self):
        for name, content in [
            ("huge.json", b" " * (10 * 1024 * 1024 + 1)),
            ("many.csv", b"state\n" + b"x\n" * 20001),
            ("long.csv", b"state\n" + b"x" * 64001 + b"\n"),
        ]:
            self.preview(name, content, status=400)
            self.save(name, content, status=400)
        for token, status in [
            ("b-secret", 404),
            ("review-secret", 403),
            ("infer-secret", 403),
        ]:
            self.preview(
                "ok.csv",
                b"state\nexample\n",
                headers={"Authorization": "Bearer " + token},
                status=status,
            )
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])


if __name__ == "__main__":
    unittest.main()
