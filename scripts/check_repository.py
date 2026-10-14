"""Validate the public checkout without depending on local planning or receipts."""

import ast
import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from publication_policy import (
    private_path_reason,
    publication_violations,
    reader_facing_violations,
)

root = Path(__file__).resolve().parents[1]
errors = []


def is_private(path):
    return private_path_reason(path.relative_to(root).as_posix()) is not None


if (root / ".git").exists():
    inventory = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    names = [name.decode("utf-8") for name in inventory.stdout.split(b"\0") if name]
    errors.extend(publication_violations(root, names))
    if errors:
        raise SystemExit("\n".join(errors))
    files = {
        (root / name.decode("utf-8")).resolve()
        for name in inventory.stdout.split(b"\0")
        if name
    }
else:
    files = {
        p.resolve()
        for p in root.iterdir()
        if p.is_file() and p.name != "TRACKER.md" and not p.name.startswith(".env")
    }
    files.add(root / ".env.example")
    for folder in (
        "decision_workbench",
        "docs",
        "examples",
        "requirements",
        "research",
        "scripts",
        "tests",
        "docker",
        ".github",
    ):
        files.update(
            p.resolve()
            for p in (root / folder).rglob("*")
            if p.is_file() and not is_private(p)
        )
files = {p for p in files if p.is_file()}
documents = sorted(p for p in files if p.suffix == ".md")
links = 0
blocks = 0
for path in documents:
    text = path.read_text(encoding="utf-8-sig")
    errors.extend(reader_facing_violations(path.relative_to(root).as_posix(), text))
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link) or link.startswith("#"):
            continue
        target = (path.parent / unquote(link.strip("<>").split("#")[0])).resolve()
        links += 1
        if not target.is_relative_to(root):
            errors.append(f"{path.relative_to(root)}: link outside repository: {link}")
        elif target.is_dir():
            if not any(p.is_relative_to(target) for p in files):
                errors.append(
                    f"{path.relative_to(root)}: directory absent from public checkout: {link}"
                )
        elif target not in files:
            errors.append(
                f"{path.relative_to(root)}: link absent from public checkout: {link}"
            )
    for block in re.findall(r"```python\s*\n(.*?)\n```", text, flags=re.S):
        try:
            ast.parse(block)
            blocks += 1
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(root)}: invalid Python example: {exc}")
    if "TRACKER.md" in text or ".research/" in text or ".agents/" in text:
        errors.append(
            f"{path.relative_to(root)}: public documentation references local internal material"
        )
for path in sorted(files):
    if path.suffix == ".py":
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    if is_private(path):
        errors.append(
            f"Private file included in public checkout: {path.relative_to(root)}"
        )
from decision_workbench.data import read_records

spec = json.loads((root / "examples/support/spec.json").read_text(encoding="utf-8-sig"))
rows = read_records(
    (root / "examples/support/seeds.csv").read_bytes(), "seeds.csv", spec
)
assert len(rows) == 12
snapshot = json.loads(
    (root / "research/benchmark-method.json").read_text(encoding="utf-8-sig")
)
extract = root / "research/benchmark-extract.csv"
assert hashlib.sha256(extract.read_bytes()).hexdigest() == snapshot["extract_sha256"]
with extract.open(encoding="utf-8-sig", newline="") as stream:
    results = list(csv.DictReader(stream))
assert any("Jev 1.13.0" in row["name"] for row in results)
index_snapshot = json.loads(
    (root / "research/decision-index-snapshot.json").read_text(encoding="utf-8-sig")
)
source_registry = json.loads(
    (root / "research/source-snapshot.json").read_text(encoding="utf-8-sig")
)
for field, source_id in (
    ("methodology_sha256", "decision-index-methodology-32425c2"),
    ("results_sha256", "decision-index-index-32425c2"),
):
    assert (
        next(item["sha256"] for item in source_registry if item["id"] == source_id)
        == index_snapshot[field]
    )
assert len(index_snapshot["selected_rows"]) == 7
if errors:
    raise SystemExit("\n".join(errors))
print(
    f"Checked {len(documents)} public Markdown files, {links} local links, {blocks} Python examples, source syntax, 12 data examples and {len(results)} benchmark rows"
)
