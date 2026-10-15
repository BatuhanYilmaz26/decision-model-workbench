"""Repeatable checks for intake and non-GPU preparation surfaces."""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from decision_workbench.data import read_records

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'examples/support/spec.json').read_text(encoding='utf-8'))

class IntakeTests(unittest.TestCase):
    def test_json_and_tsv_import(self):
        rows = read_records((ROOT / 'examples/support/seeds.jsonl').read_bytes(), 'seeds.jsonl')
        self.assertEqual(read_records(json.dumps(rows).encode(), 'rows.json'), rows)
        csv_rows = list(csv.DictReader(io.StringIO((ROOT / 'examples/support/seeds.csv').read_text(encoding='utf-8'))))
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]), delimiter='\t')
        writer.writeheader(); writer.writerows(csv_rows)
        imported = read_records(stream.getvalue().encode(), 'rows.tsv', SPEC)
        self.assertEqual([r['gold'] for r in imported], [r['gold'] for r in rows])

    def test_bad_json_rows_and_empty_spreadsheet(self):
        with self.assertRaisesRegex(ValueError, 'must be an object'):
            read_records(b'[null]', 'rows.json')
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest('XLSX extra absent')
        workbook = Workbook(); stream = io.BytesIO(); workbook.save(stream); workbook.close()
        with self.assertRaisesRegex(ValueError, 'empty'):
            read_records(stream.getvalue(), 'empty.xlsx', SPEC)

    def test_cli_wizard_validates_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'nested/spec.json'
            stdin = 'Route enterprise support under a documented policy.\nexamples/support/spec.json\nexamples/support/seeds.csv\n'
            result = subprocess.run([sys.executable, '-m', 'decision_workbench.cli', 'wizard', '--out', str(output)], input=stdin, text=True, capture_output=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())['questions'], SPEC['questions'])
            bad_output = Path(directory) / 'bad/spec.json'
            result = subprocess.run([sys.executable, '-m', 'decision_workbench.cli', 'wizard', '--out', str(bad_output)], input=stdin.replace('examples/support/seeds.csv','nonexistent.csv'), text=True, capture_output=True, cwd=ROOT)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(bad_output.exists())

class AppTests(unittest.TestCase):
    def test_local_entry_and_shared_connection_are_available_without_a_key(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            self.skipTest('UI extra absent')
        app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(app.text_input[0].label, 'Workspace API URL')
        self.assertEqual(app.text_input[1].label, 'Workspace credential')
        self.assertEqual(app.button[0].label, 'Start local workspace')
        self.assertTrue(any(b.label == 'Connect' for b in app.button))
        self.assertTrue(any(w.label == 'Model-environment Python executable (optional)' for w in app.text_input))
        self.assertTrue(any(w.label == 'Teacher base URL (optional)' for w in app.text_input))
        self.assertEqual(next(w for w in app.radio if w.label == 'Where is your workspace?').value, 'On this computer')

if __name__ == '__main__':
    unittest.main()
