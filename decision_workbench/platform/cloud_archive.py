"""Bounded regular-file USTAR/GNU-short-name extraction for managed training outputs."""

import gzip
import hashlib
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from decision_workbench.training.cloud_runner import ordinary


def exact(stream, count):
    value = stream.read(count)
    if len(value) != count:
        raise ValueError("Truncated cloud model archive")
    return value


def octal(field):
    value = field.rstrip(b"\0 ").lstrip(b" ")
    if not value or any(c not in b"01234567" for c in value):
        raise ValueError("Unsupported cloud archive numeric field")
    return int(value, 8)


def extract_model(archive, output, max_bytes, *, backend=None):
    """Extract regular files; native assets require a selected pinned profile."""
    output = ordinary(output)
    from decision_workbench.training.native_remote import (
        BACKENDS,
        export_files,
        export_assets,
    )

    native_files, native_hashes = set(), {}
    if backend in BACKENDS:
        checkpoint = "merged" if backend == "liquid-trl" else "checkpoint"
        native_files = export_files(backend, checkpoint)
        native_hashes = export_assets(backend, checkpoint)
    output.mkdir(parents=True, exist_ok=False)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *["COM" + str(n) for n in range(1, 10)],
        *["LPT" + str(n) for n in range(1, 10)],
    }
    count, total, seen = 0, 0, set()
    deadline = time.monotonic() + 600
    try:
        with gzip.open(ordinary(archive), "rb") as stream:
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("Cloud archive extraction exceeded its time bound")
                header = exact(stream, 512)
                if header == bytes(512):
                    if exact(stream, 512) != bytes(512):
                        raise ValueError("Invalid cloud archive terminator")
                    padding = 0
                    while extra := stream.read(65536):
                        padding += len(extra)
                        if any(extra) or padding > 1024**2:
                            raise ValueError(
                                "Invalid/excessive trailing cloud archive data"
                            )
                    break
                count += 1
                if count > 2048:
                    raise ValueError("Cloud archive exceeds its entry bound")
                if octal(header[148:156]) != sum(header[:148]) + 8 * 32 + sum(
                    header[156:]
                ):
                    raise ValueError("Cloud archive header checksum differs")
                if header[257:263] not in (b"ustar\0", b"ustar ", bytes(6)):
                    raise ValueError("Unsupported cloud archive format")
                # GNU headers use the prefix area for extended fields; require no such data.
                prefix = (
                    header[345:500].split(b"\0", 1)[0]
                    if header[257:263] == b"ustar\0"
                    else b""
                )
                raw = header[:100].split(b"\0", 1)[0]
                if prefix:
                    raw = prefix + b"/" + raw
                name = raw.decode("utf-8")
                while name.startswith("./"):
                    name = name[2:]
                kind, size = header[156:157], octal(header[124:136])
                if kind not in (b"0", b"\0", b"5") or header[157:257].strip(b"\0"):
                    raise ValueError(
                        "Cloud archive contains unsupported linked/extended content"
                    )
                if kind == b"5":
                    name = name.rstrip("/")
                    if size:
                        raise ValueError("Cloud archive directory has content")
                    if name in ("", "."):
                        continue
                parts = PurePosixPath(name).parts
                if (
                    not parts
                    or len(parts) > 16
                    or len(name) > 1024
                    or PurePosixPath(name).is_absolute()
                    or PurePosixPath(name).as_posix() != name
                    or any(
                        p in (".", "..")
                        or not re.fullmatch(r"[A-Za-z0-9_.-]+", p)
                        or p.endswith((".", " "))
                        or p.split(".")[0].upper() in reserved
                        for p in parts
                    )
                ):
                    raise ValueError("Unsafe cloud archive path")
                folded = name.casefold()
                if folded in seen:
                    raise ValueError("Duplicate cloud archive path")
                seen.add(folded)
                target = output / name
                if kind == b"5":
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if name not in native_files and target.suffix.lower() not in (
                    ".json",
                    ".safetensors",
                    ".txt",
                    ".model",
                    ".tiktoken",
                ):
                    raise ValueError(
                        "Cloud archive has an unsupported/executable export file"
                    )
                total += size
                if total > max_bytes:
                    raise ValueError("Cloud archive exceeds its expanded byte bound")
                target.parent.mkdir(parents=True, exist_ok=True)
                asset_hash = hashlib.sha256() if name in native_hashes else None
                with target.open("xb") as writer:
                    remaining = size
                    while remaining:
                        if time.monotonic() > deadline:
                            raise ValueError(
                                "Cloud archive extraction exceeded its time bound"
                            )
                        chunk = exact(stream, min(1024**2, remaining))
                        writer.write(chunk)
                        if asset_hash is not None:
                            asset_hash.update(chunk)
                        remaining -= len(chunk)
                if (
                    asset_hash is not None
                    and asset_hash.hexdigest() != native_hashes[name]
                ):
                    raise ValueError("Cloud archive changes a reviewed native asset")
                padding = (-size) % 512
                if any(exact(stream, padding)):
                    raise ValueError("Cloud archive file padding differs")
        if (
            not (output / "manifest.json").is_file()
            or not (output / "report.json").is_file()
        ):
            raise ValueError("Cloud archive lacks the export manifest/report")
        return {"bytes": total, "entries": count}
    except BaseException:
        shutil.rmtree(output)
        raise
