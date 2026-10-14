"""All public offset routes reject overflow before storage work."""

import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from test_platform import keys


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name), keys())
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})

    def tearDown(self):
        self.app.state.service.runtime.close()
        self.client.close()
        self.temp.cleanup()

    def test_every_offset_endpoint_rejects_oversized_and_negative_pages(self):
        paths = [
            path
            for path, item in self.app.openapi()["paths"].items()
            if any(
                p["name"] == "offset" for p in item.get("get", {}).get("parameters", [])
            )
        ]
        self.assertGreaterEqual(len(paths), 10)
        for path in paths:
            url = re.sub(r"\{[^}]+\}", "missing", path)
            for offset in (str(2**31), str(10**80), "-1", "1.5", "true"):
                with self.subTest(path=path, offset=offset):
                    response = self.client.get(url, params={"offset": offset})
                    self.assertEqual(response.status_code, 422, response.text)
            for limit in ("0", "100000", "1.5"):
                with self.subTest(path=path, limit=limit):
                    self.assertEqual(
                        self.client.get(url, params={"limit": limit}).status_code, 422
                    )

    def test_largest_supported_offset_is_a_valid_empty_project_page(self):
        response = self.client.get("/v1/projects", params={"offset": 2**31 - 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_direct_storage_rejects_invalid_pages_before_database_binding(self):
        store = self.app.state.store
        for offset in (2**31, 10**80, -1, True, 0.5, None):
            for call in (
                lambda: store.list("A", offset=offset),
                lambda: store.resolve_rows(None, {}, limit=50, offset=offset),
                lambda: store.operations("A", "missing", offset=offset),
                lambda: store.incident_history(
                    "A", "missing", "missing", offset=offset
                ),
            ):
                with self.subTest(offset=offset):
                    with self.assertRaises(ValueError):
                        call()
        for limit in (0, -1, True, 0.5, 501):
            with self.assertRaises(ValueError):
                store.list("A", limit=limit)
