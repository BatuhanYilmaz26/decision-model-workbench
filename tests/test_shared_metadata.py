"""Snapshot consistency, bounded archives and rollbackable metadata reads."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.platform import shared_metadata as archive
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
)
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, encode
import test_platform as fixtures
import test_postgres as pg


class MetadataFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "metadata.jsonl"

    def fixture(self):
        columns = {table: ["id"] for table in archive.TABLES}
        columns["schema_version"] = ["version"]
        columns["workspace_metadata_identity"] = ["slot", "id"]
        header = {
            "format": archive.FORMAT,
            "workspace": "a" * 32,
            "protocol": 14,
            "source_schema": "fictional",
            "tables": columns,
        }
        counts = {table: 0 for table in archive.TABLES}
        rows = [
            {"table": "schema_version", "values": [14]},
            {"table": "workspace_metadata_identity", "values": [1, "a" * 32]},
        ]
        for row in rows:
            counts[row["table"]] += 1
        body = b"".join(
            (encode(row) + "\n").encode()
            for row in [header, *rows, {"complete": archive.FORMAT, "counts": counts}]
        )
        self.path.write_bytes(body)
        return {
            "header": header,
            "counts": counts,
            "rows": 2,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "references": [],
        }

    def test_complete_inventory_is_validated_and_rows_are_streamed(self):
        metadata = self.fixture()
        rows = []
        result = archive.read_snapshot(
            self.path, metadata, lambda *row: rows.append(row)
        )
        self.assertEqual(result, metadata["header"])
        self.assertEqual(
            [row[0] for row in rows], ["schema_version", "workspace_metadata_identity"]
        )

    def test_corruption_extra_bytes_and_truncation_are_refused(self):
        metadata = self.fixture()
        original = self.path.read_bytes()
        for body in (
            original[:-1],
            original + b"{}\n",
            original.replace(b"fictional", b"changed"),
        ):
            self.path.write_bytes(body)
            with self.assertRaises(ValueError):
                archive.read_snapshot(self.path, metadata)

    def test_wrong_identity_and_protocol_are_refused_even_with_matching_file_hash(self):
        metadata = self.fixture()
        original = self.path.read_bytes()
        for body in (
            original.replace(
                b'"values": [1, "' + b"a" * 32 + b'"]',
                b'"values": [1, "' + b"b" * 32 + b'"]',
            ),
            original.replace(b'"values": [14]', b'"values": [15]'),
        ):
            self.assertNotEqual(body, original)
            self.path.write_bytes(body)
            altered = {
                **metadata,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            with self.assertRaises(ValueError):
                archive.read_snapshot(self.path, altered)

    def test_invalid_columns_and_oversized_lines_are_refused(self):
        metadata = self.fixture()
        with self.assertRaises(ValueError):
            archive.validate_columns({})
        malformed = {**metadata["header"]["tables"], "schema_version": [{}]}
        with self.assertRaises(ValueError):
            archive.validate_columns(malformed)
        with patch.object(archive, "MAX_ROW", 10), self.assertRaises(ValueError):
            archive.read_snapshot(self.path, metadata)


@unittest.skipUnless(
    pg.URL, "Set DMT_TEST_POSTGRES_URL for shared metadata snapshot checks"
)
class PostgreSQLMetadataTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = ArtifactRepository(DirectoryObjects(self.root / "objects"))
        self.store = Store(
            self.root / "source",
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        self.service = Service(self.store)
        self.addCleanup(self.service.runtime.close)
        self.owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        self.project = self.service.create_project(
            self.owner,
            "Fictional shared recovery",
            "Fictional policy",
            fixtures.QUESTIONS,
        )["id"]

    def test_snapshot_retains_one_view_when_source_changes_during_export(self):
        changed = []

        def change():
            if not changed:
                changed.append(
                    self.service.create_project(
                        self.owner,
                        "Created after snapshot",
                        "Later policy",
                        fixtures.QUESTIONS,
                    )["id"]
                )

        path = self.root / "metadata.jsonl"
        metadata = archive.write_snapshot(self.store, path, change)
        rows = []
        archive.read_snapshot(
            path,
            metadata,
            lambda table, names, values: rows.append((table, dict(zip(names, values)))),
        )
        captured = [
            row["id"]
            for table, row in rows
            if table == "entities" and row["kind"] == "project"
        ]
        self.assertEqual(captured, [self.project])
        self.assertEqual(
            self.store.get(changed[0], "A", changed[0], "project")["id"], changed[0]
        )
        self.assertEqual(metadata["header"]["workspace"], self.store.workspace_id)

    def test_callback_changes_roll_back_on_retained_checksum_mismatch(self):
        path = self.root / "metadata.jsonl"
        metadata = archive.write_snapshot(self.store, path)
        with self.assertRaises(ValueError):
            with self.store.transaction() as db:

                def callback(table, names, values):
                    if table == "entities":
                        db.execute(
                            "UPDATE entities SET payload=? WHERE id=?",
                            ("{}", self.project),
                        )

                archive.read_snapshot(path, {**metadata, "sha256": "0" * 64}, callback)
        self.assertEqual(
            self.store.get(self.project, "A", self.project, "project")["payload"][
                "name"
            ],
            "Fictional shared recovery",
        )

    def test_unsupported_schema_and_existing_output_are_refused(self):
        path = self.root / "metadata.jsonl"
        path.write_text("preserve")
        with self.assertRaises(ValueError):
            archive.write_snapshot(self.store, path)
        self.assertEqual(path.read_text(), "preserve")
        with self.store.transaction() as db:
            db.execute("CREATE TABLE unexpected_private_table (id TEXT)")
        with self.assertRaises(ValueError):
            archive.write_snapshot(self.store, self.root / "unknown.jsonl")


if __name__ == "__main__":
    unittest.main()
