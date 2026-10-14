"""Exercise the real HTTP transport without an external or paid teacher."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from decision_workbench.data import read_records

class HttpGenerationTest(unittest.TestCase):
    def test_cli_teacher_request_and_unreviewed_export(self):
        spec = json.loads(Path("examples/support/spec.json").read_text(encoding="utf-8-sig"))
        seed = read_records(Path("examples/support/seeds.csv").read_bytes(), "seeds.csv", spec)[0]
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, body))
                text = json.dumps({"rows": [{"state": "Please refund the extra payment.", "gold": seed["gold"]}]})
                response = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "candidates.jsonl"
                result = subprocess.run([
                    sys.executable, "-m", "decision_workbench.cli", "generate",
                    "--input", "examples/support/seeds.csv", "--spec", "examples/support/spec.json",
                    "--base-url", f"http://127.0.0.1:{server.server_port}/v1",
                    "--model", "fixture-teacher", "--count", "1", "--out", str(output),
                ], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                candidates = read_records(output.read_bytes(), output.name)
                self.assertFalse(candidates[0]["metadata"]["reviewed"])
                self.assertEqual(seen[0][0], "/v1/chat/completions")
                self.assertEqual(seen[0][1]["model"], "fixture-teacher")
                self.assertEqual(candidates[0]["metadata"]["seed_ids"][0], "t01")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

if __name__ == "__main__":
    unittest.main()
