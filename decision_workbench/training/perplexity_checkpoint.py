"""Verify native checkpoint bytes and load only pinned publisher source."""

import ast
import argparse
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import math
from pathlib import Path
import shutil
import struct
import sys
from urllib.request import urlopen

from decision_workbench.json_tools import strict_json
from .perplexity_profile import ASSETS, WEIGHTS, WEIGHT_BYTES, MODEL, REVISION
from .perplexity_assets import BASE_MODEL, BASE_REVISION

MAX_HEADER = 16 * 1024**2


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset(folder, name):
    if name not in ASSETS:
        raise ValueError("Unrecognized native Perplexity asset")
    expected = ASSETS[name]
    path = Path(folder) / name
    if not path.is_file() or path.stat().st_size != expected["bytes"]:
        raise ValueError("Missing or resized publisher asset: " + name)
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected["sha256"]:
        raise ValueError("Publisher asset hash differs: " + name)
    return body


def prepare_assets(folder, *, download=False):
    """Fetch source/config/tokenizer assets only, never model weights."""
    folder = Path(folder)
    if download:
        folder.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        path = folder / name
        if not path.exists() and download:
            with urlopen(
                f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=60
            ) as response:
                body = response.read(expected["bytes"] + 1)
            if (
                len(body) != expected["bytes"]
                or hashlib.sha256(body).hexdigest() != expected["sha256"]
            ):
                raise ValueError("Downloaded publisher asset differs: " + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as stream:
                    stream.write(body)
            except FileExistsError:
                pass
        asset(folder, name)
    return folder


def base_checkpoint(folder):
    folder = prepare_assets(folder)
    for name, expected in WEIGHTS.items():
        path = folder / name
        if (
            not path.is_file()
            or path.stat().st_size != expected["bytes"]
            or file_sha(path) != expected["sha256"]
        ):
            raise ValueError("Base weight identity differs: " + name)
    return folder


def download_checkpoint(cache):
    """Explicit user action: download the complete roughly 52 GB checkpoint."""
    cache = Path(cache).absolute()
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache).free < WEIGHT_BYTES + 2 * 1024**3:
        raise ValueError(
            "Insufficient free cache storage for the complete Perplexity checkpoint"
        )
    from huggingface_hub import snapshot_download

    folder = snapshot_download(
        MODEL,
        revision=REVISION,
        cache_dir=str(cache),
        allow_patterns=[*ASSETS, *WEIGHTS],
    )
    return base_checkpoint(folder)


def native_files(folder, publisher):
    """Validate native structure without importing Torch or executing model code."""
    folder = Path(folder).resolve()
    reference = strict_json(asset(publisher, "decision_config.json"))
    path = folder / "decision_config.json"
    if path.stat().st_size > 1024**2:
        raise ValueError("Native decision configuration exceeds 1 MiB")
    config = strict_json(path.read_bytes())
    if not isinstance(config, dict) or type(config.get("format_version")) is not int:
        raise ValueError("Native decision configuration must be a versioned object")
    for key in (
        "format_version",
        "base_model",
        "revision",
        "codes",
        "token_ids",
        "attention_mode",
        "pooling",
    ):
        if config.get(key) != reference[key]:
            raise ValueError("Native decision configuration differs: " + key)
    scale = config.get("temperature")
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Native temperature must be finite and positive")
    if config["base_model"] != BASE_MODEL or config["revision"] != BASE_REVISION:
        raise ValueError("Native backbone identity differs")
    index = folder / "model.safetensors.index.json"
    if index.stat().st_size > 2 * 1024**2:
        raise ValueError("Native weight index exceeds 2 MiB")
    index_body = strict_json(index.read_bytes())
    if not isinstance(index_body, dict):
        raise ValueError("Native weight index must be an object")
    mapping = index_body.get("weight_map")
    original = strict_json(asset(publisher, "model.safetensors.index.json"))[
        "weight_map"
    ]
    if not isinstance(mapping, dict) or set(mapping) != set(original):
        raise ValueError("Native backbone tensor inventory differs")
    if any(not isinstance(n, str) for n in mapping.values()):
        raise ValueError("Native weight shard names must be strings")
    names = set(mapping.values())
    if not 1 <= len(names) <= 16 or any(
        not isinstance(n, str)
        or Path(n).name != n
        or not n.startswith("model-")
        or not n.endswith(".safetensors")
        for n in names
    ):
        raise ValueError("Native weight shards must be local safetensors filenames")
    if sum((folder / n).stat().st_size for n in names) > WEIGHT_BYTES + 16 * MAX_HEADER:
        raise ValueError("Native backbone exceeds its pinned profile size")
    files = [
        *sorted(names),
        "readout.safetensors",
        "model.safetensors.index.json",
        "decision_config.json",
        "config.json",
        "processor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    ]
    if any(not (folder / n).is_file() or (folder / n).is_symlink() for n in files):
        raise ValueError("Native checkpoint has missing or linked files")
    return config, mapping, files


def tensor_digest(folder, mapping, *, readout=False):
    """Hash logical tensor payloads independent of shard ordering/header metadata."""
    folder = Path(folder)
    mapping = {"weight": "readout.safetensors"} if readout else mapping
    descriptors = {}
    for name in sorted(set(mapping.values())):
        path = folder / name
        with path.open("rb") as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise ValueError("Incomplete safetensors header")
            length = struct.unpack("<Q", prefix)[0]
            if not 2 <= length <= MAX_HEADER:
                raise ValueError("Safetensors header exceeds the profile")
            header = strict_json(stream.read(length))
            if not isinstance(header, dict):
                raise ValueError("Safetensors header must be an object")
        size = path.stat().st_size - 8 - length
        regions = []
        for key, entry in header.items():
            if key == "__metadata__":
                continue
            if mapping.get(key) != name or not isinstance(entry, dict):
                raise ValueError("Safetensors tensors differ from their index")
            shape, offsets = entry.get("shape"), entry.get("data_offsets")
            if (
                entry.get("dtype") != "BF16"
                or not isinstance(shape, list)
                or not shape
                or any(type(x) is not int or x < 1 for x in shape)
                or not isinstance(offsets, list)
                or len(offsets) != 2
                or any(type(x) is not int for x in offsets)
            ):
                raise ValueError(
                    "Native tensors must have valid BF16 shapes and offsets"
                )
            start, end = offsets
            if start < 0 or end > size or end - start != math.prod(shape) * 2:
                raise ValueError("Safetensors payload shape/length differs")
            descriptors[key] = (path, 8 + length + start, end - start, shape)
            regions.append((start, end))
        regions.sort()
        if (
            not regions
            or regions[0][0] != 0
            or regions[-1][1] != size
            or any(a[1] != b[0] for a, b in zip(regions, regions[1:]))
        ):
            raise ValueError("Safetensors payload has gaps or overlaps")
    if set(descriptors) != set(mapping):
        raise ValueError("Native tensor index has missing payloads")
    if readout and descriptors["weight"][3] != [255, 5120]:
        raise ValueError("Native readout must have shape [255, 5120]")
    digest = hashlib.sha256()
    for key in sorted(descriptors):
        path, offset, length, shape = descriptors[key]
        digest.update(json.dumps([key, shape, "BF16"], separators=(",", ":")).encode())
        with path.open("rb") as stream:
            stream.seek(offset)
            remaining = length
            while remaining:
                body = stream.read(min(1024**2, remaining))
                if not body:
                    raise ValueError("Native tensor payload changed during hashing")
                digest.update(body)
                remaining -= len(body)
    return digest.hexdigest()


class _PublisherFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, folder):
        self.folder = Path(folder)
        self.prefix = (
            "_dmt_perplexity_"
            + hashlib.sha256(str(self.folder.resolve()).encode()).hexdigest()[:24]
        )

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.prefix:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        if fullname.startswith(self.prefix + "."):
            name = fullname[len(self.prefix) + 1 :]
            if "." in name or "source/src/autojev/" + name + ".py" not in ASSETS:
                raise ImportError(
                    "Publisher module is outside the pinned source allowlist"
                )
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = (
            "__init__"
            if module.__name__ == self.prefix
            else module.__name__.rsplit(".", 1)[1]
        )
        filename = "source/src/autojev/" + name + ".py"
        module.__file__ = str(self.folder / filename)
        tree = ast.parse(asset(self.folder, filename), filename=module.__file__)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "autojev" or node.module.startswith("autojev."))
            ):
                node.module = self.prefix + node.module[len("autojev") :]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "autojev" or alias.name.startswith("autojev."):
                        raise ImportError("Unexpected direct publisher package import")
        exec(compile(tree, module.__file__, "exec"), module.__dict__)


def publisher_module(folder, name):
    """Isolated verified source imports, ignoring all existing Python bytecode."""
    prepare_assets(folder)
    finder = _PublisherFinder(folder)
    fullname = finder.prefix + "." + name
    if not any(
        isinstance(item, _PublisherFinder) and item.prefix == finder.prefix
        for item in sys.meta_path
    ):
        sys.meta_path.insert(0, finder)
    try:
        return importlib.import_module(fullname)
    except BaseException:
        for key in list(sys.modules):
            if key == finder.prefix or key.startswith(finder.prefix + "."):
                del sys.modules[key]
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assets",
        help="Verify or populate only the pinned source/config/tokenizer directory",
    )
    parser.add_argument("--download-assets", action="store_true")
    parser.add_argument(
        "--cache", help="Hub cache for the complete model, roughly 52 GB"
    )
    parser.add_argument(
        "--download-weights",
        action="store_true",
        help="Explicitly download the complete large checkpoint",
    )
    args = parser.parse_args()
    try:
        if args.assets and not args.cache and not args.download_weights:
            folder = prepare_assets(args.assets, download=args.download_assets)
            result = {
                "assets": str(folder.absolute()),
                "model": MODEL,
                "revision": REVISION,
                "weight_download": False,
            }
        elif (
            args.cache
            and args.download_weights
            and not args.assets
            and not args.download_assets
        ):
            folder = download_checkpoint(args.cache)
            result = {
                "checkpoint": str(folder),
                "model": MODEL,
                "revision": REVISION,
                "weight_bytes": WEIGHT_BYTES,
            }
        else:
            raise ValueError(
                "Choose --assets [--download-assets], or --cache with explicit --download-weights"
            )
        print(json.dumps(result))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.exit(1, f"Perplexity checkpoint preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
