"""Path policy for files included in a public source checkout."""

from pathlib import PurePosixPath
import json
import re
import subprocess


def private_path_reason(name):
    """Return an exclusion reason, or None for an eligible relative path."""
    path = PurePosixPath(str(name).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return "path outside checkout"
    parts = tuple(part.lower() for part in path.parts)
    if not parts:
        return "empty path"
    private_dirs = {
        ".research",
        ".agents",
        ".codex",
        ".internal",
        ".git",
        "evidence",
        ".vscode",
        ".idea",
        ".aws",
        ".azure",
        ".gcloud",
        ".terraform",
        ".ssh",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "htmlcov",
        "unsloth_compiled_cache",
        "backups",
    }
    if any(
        part in private_dirs
        or part.startswith((".venv", ".dmt-"))
        or part.endswith(".egg-info")
        for part in parts[:-1]
    ):
        return "local instructions, evidence, credentials or generated directory"
    if parts[0] in {"data", "runs", "models", "build", "dist"}:
        return "local data or build output"
    filename = parts[-1]
    if filename in {"agents.md", "skill.md", "tracker.md"} or (
        filename.endswith("_tracker.md")
        or filename.startswith("tracker_")
        and filename.endswith(".md")
    ):
        return "agent instructions or planning ledger"
    if (
        filename == ".env"
        or filename.startswith(".env.")
        and filename != ".env.example"
    ):
        return "local environment configuration"
    if filename in {"operator-keys.json", "credentials.json", "secrets.toml"}:
        return "local credential configuration"
    if (
        filename.startswith(".dmt-")
        or filename.endswith(
            (".dmtkey", ".dmtenc", ".dmtplan", ".dmtpub", ".dmtlog", ".dmtdelete")
        )
        or filename
        in {
            "encrypted-backup.json",
            "backup-manifest.json",
            "shared-backup.json",
            "shared-metadata.jsonl",
            "shared-restore.json",
        }
    ):
        return "private recovery keys, workspace backup data or metadata"
    if (
        filename.endswith((".tfstate", ".tfplan", ".tfvars", ".tfvars.json"))
        or ".tfstate." in filename
    ):
        return "private Terraform state, plan or operator variable file"
    if filename.endswith((".pyc", ".log")) or ".sqlite3" in filename:
        return "generated runtime output"
    if filename == ".coverage" or filename.startswith(".coverage."):
        return "generated coverage output"
    return None


def publication_violations(root, names):
    """Detect private paths and ignored files that were already added to Git."""
    errors = [
        f"Private file included in public checkout: {name} ({reason})"
        for name in names
        if (reason := private_path_reason(name)) is not None
    ]
    # Custom backup names remain private even outside the ordinary backup vault.

    from pathlib import Path

    root = Path(root).absolute()
    for name in names:
        if private_path_reason(name) is not None:
            continue
        candidate = root / name
        if candidate.is_file() and not candidate.is_symlink():
            try:
                with candidate.open("rb") as source:
                    prefix = source.read(1024 * 1024 + 1)
                    first = prefix.split(b"\n", 1)[0]
                if first.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"{"):
                    if re.search(
                        rb'"format"\s*:\s*"domain-(?:cache-retention|registered-cache)-v1"',
                        prefix,
                    ):
                        errors.append(
                            f"Private cache maintenance record included in public checkout: {name}"
                        )
                    if re.search(
                        rb'"format"\s*:\s*"domain-shared-(?:backup|metadata|restore)-v1"',
                        prefix,
                    ):
                        errors.append(
                            f"Private shared recovery record included in public checkout: {name}"
                        )
                    header = json.loads(first)
                    if isinstance(header, dict) and (
                        header.get("format") == "domain-project-deletion-v1"
                        or isinstance(header.get("plan"), dict)
                        and header["plan"].get("format") == "domain-project-deletion-v1"
                    ):
                        errors.append(
                            f"Private project deletion record included in public checkout: {name}"
                        )
                    if (
                        isinstance(header, dict)
                        and isinstance(header.get("manifest"), dict)
                        and header["manifest"].get("format") == "domain-audit-export-v1"
                    ):
                        errors.append(
                            f"Private project audit export included in public checkout: {name}"
                        )
            except (OSError, ValueError, RecursionError):
                pass
        for ancestor in (root / name).parents:
            if not ancestor.is_relative_to(root):
                break
            if any(
                (ancestor / marker).exists()
                for marker in (
                    "backup-manifest.json",
                    "shared-backup.json",
                    "shared-restore.json",
                    "encrypted-backup.json",
                    "workspace.sqlite3",
                    ".dmt-vault.json",
                )
            ):
                errors.append(
                    f"Private runtime or backup directory included in public checkout: {name}"
                )
                break
            if ancestor == root:
                break
    if not names:
        return errors
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=root,
        input=b"\0".join(name.encode("utf-8") for name in names) + b"\0",
        capture_output=True,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError("Could not check Git publication exclusions")
    errors.extend(
        f"Ignored file already added to Git: {name.decode('utf-8')}"
        for name in ignored.stdout.split(b"\0")
        if name
    )
    return errors


def reader_facing_violations(name, text):
    """Catch development-report phrasing in public Markdown prose."""
    prose = re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.M | re.S)
    patterns = (
        r"^Reviewed\s+(?:\*\*)?\d",
        r"\bsource-backed recipe\b",
        r"\b(?:executed|created|accepted|qualified) here\b",
        r"\bfor this research\b",
        r"\bour (?:qualified|proposed)\b",
        r"\bthis (?:pass|session)\b",
        r"\b(?:remains? on the tracker|remain tracked work|remain tracked separately)\b",
        r"\bfuture implementation work\b",
    )
    return [
        f"{name}: replace development-report prose with reader instructions"
        for pattern in patterns
        if re.search(pattern, prose, flags=re.I | re.M)
    ]
