"""Keep local instructions and artifacts outside public Git snapshots."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publication_policy import private_path_reason, publication_violations

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def test_private_paths_at_any_depth(self):
        for name in (
            "TRACKER.md",
            "docs/tracker.md",
            "PLATFORM_TRACKER.md",
            "docs/Tracker_release.MD",
            "AGENTS.md",
            "docs/Skill.md",
            ".agents/skills/domain/SKILL.md",
            "docs/.Agents/instructions.txt",
            "docs/.research/receipt.json",
            ".codex/config.toml",
            "nested/.internal/session.md",
            "nested/.env.production",
            "nested/credentials.json",
            ".streamlit/secrets.toml",
            "nested/Secrets.Toml",
            "nested/recovery.dmtkey",
            "nested/policy.dmtplan",
            "nested/monitor.dmtpub",
            "nested/audit.dmtlog",
            "nested/deletion.dmtdelete",
            "nested/DELETION.DMTDELETE",
            "nested/.dmt-vault.json",
            "nested/payload.dmtenc",
            "nested/encrypted-backup.json",
            "nested/backup-manifest.json",
            "nested/backups/private-data.bin",
            "nested/.Dmt-restore-interrupted/weights.bin",
            "nested/operator-keys.json",
            "nested/evidence/log.txt",
            "data/model-cache/weights.bin",
            "nested/.venv/lib/module.py",
            "tests/__pycache__/test.pyc",
            "unsloth_compiled_cache/UnslothRLOOTrainer.py",
            "nested/Unsloth_Compiled_Cache/generated.py",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(private_path_reason(name))

    def test_public_source_and_examples_remain_eligible(self):
        for name in (
            "README.md",
            "docs/data-guide.md",
            "docs/model-container-guide.md",
            "docker/models/Dockerfile.vega",
            "examples/cloud/aws-profiles.json",
            ".env.example",
            "examples/platform/streamlit-secrets.toml.example",
            "research/provenance/source-snapshot.json",
            "tests/fixtures/provider-contracts/README.md",
            "decision_workbench/platform/credentials.py",
            ".github/workflows/checks.yml",
        ):
            with self.subTest(name=name):
                self.assertIsNone(private_path_reason(name))

    def test_custom_backup_and_workspace_names_are_private_by_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=home, check=True, capture_output=True
            )
            for folder, marker in (
                ("custom-capture", "backup-manifest.json"),
                ("custom-encrypted", "encrypted-backup.json"),
                ("custom-home", "workspace.sqlite3"),
                ("custom-vault", ".dmt-vault.json"),
            ):
                nested = home / folder / "artifacts" / "model"
                nested.mkdir(parents=True)
                (home / folder / marker).write_text("Fictional private marker")
                (nested / "weights.bin").write_bytes(
                    b"Fictional private model contents"
                )
                name = folder + "/artifacts/model/weights.bin"
                errors = publication_violations(home, [name])
                self.assertTrue(
                    any("Private runtime or backup directory" in e for e in errors),
                    errors,
                )
            renamed = home / "renamed-export.jsonl"
            renamed.write_text(
                '{"manifest":{"format":"domain-audit-export-v1"}}\n', encoding="utf-8"
            )
            self.assertTrue(
                any(
                    "Private project audit export" in e
                    for e in publication_violations(home, [renamed.name])
                )
            )

    def test_renamed_deletion_plan_and_receipt_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=home, check=True, capture_output=True
            )
            for body in (
                '{"format":"domain-project-deletion-v1"}',
                '{"plan":{"format":"domain-project-deletion-v1"}}',
            ):
                file = home / "renamed.json"
                file.write_text(body, encoding="utf-8")
                self.assertTrue(
                    any(
                        "Private project deletion record" in error
                        for error in publication_violations(home, [file.name])
                    )
                )

    def test_renamed_cache_plans_and_receipts_are_private_in_both_json_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=home, check=True, capture_output=True
            )
            for body in (
                '{"format":"domain-cache-retention-v1"}',
                '{\n  "format": "domain-cache-retention-v1"\n}',
                '{"format":"domain-registered-cache-v1"}',
            ):
                file = home / "renamed-cache.json"
                file.write_text(body, encoding="utf-8")
                self.assertTrue(
                    any(
                        "Private cache maintenance record" in error
                        for error in publication_violations(home, [file.name])
                    )
                )

    def test_git_add_excludes_internal_files_and_flags_force_added_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=home, check=True, capture_output=True
                ).stdout

            git("init", "--quiet")
            git("config", "core.ignorecase", "false")
            (home / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            private = (
                "nested/recovery.dmtkey",
                "nested/POLICY.DMTPLAN",
                "nested/MONITOR.DMTPUB",
                "nested/AUDIT.DMTLOG",
                "nested/DELETION.DMTDELETE",
                "nested/.dmt-vault.json",
                "nested/RECOVERY.DMTKEY",
                "nested/payload.dmtenc",
                "nested/encrypted-backup.json",
                "nested/backup-manifest.json",
                "nested/Backups/private-data.bin",
                "nested/.Dmt-restore-interrupted/weights.bin",
                "TRACKER.md",
                "docs/tracker.md",
                "PLATFORM_TRACKER.md",
                "docs/Tracker_release.MD",
                "nested/AGENTS.md",
                "docs/Skill.md",
                ".agents/skills/domain/SKILL.md",
                "docs/.Agents/instructions.txt",
                "docs/.Research/receipt.json",
                ".codex/config.toml",
                "nested/.internal/session.md",
                "nested/.env.production",
                "nested/credentials.json",
                "nested/operator-keys.json",
                "nested/evidence/log.txt",
                "data/model-cache/weights.bin",
                "nested/.venv/lib/module.py",
                "tests/__pycache__/test.pyc",
            )
            public = (
                "README.md",
                ".env.example",
                "docker/models/Dockerfile.vega",
                "examples/cloud/aws-profiles.json",
                "docs/data-guide.md",
            )
            for name in (*private, *public):
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fictional publication fixture\n", encoding="utf-8")
            git("add", "--all")
            names = [n.decode() for n in git("ls-files", "-z").split(b"\0") if n]
            self.assertEqual(set(names), {".gitignore", *public})
            self.assertEqual(publication_violations(home, names), [])
            git("add", "--force", "docs/Skill.md", "data/model-cache/weights.bin")
            names = [n.decode() for n in git("ls-files", "-z").split(b"\0") if n]
            violations = publication_violations(home, names)
            self.assertTrue(any("docs/Skill.md" in item for item in violations))
            self.assertTrue(
                any("data/model-cache/weights.bin" in item for item in violations)
            )
            self.assertTrue(
                any("Ignored file already added to Git" in item for item in violations)
            )


if __name__ == "__main__":
    unittest.main()
