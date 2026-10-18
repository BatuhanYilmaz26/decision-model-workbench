"""Check a native model image's runtime and cache its pinned public checkpoint."""

import argparse
import json
from pathlib import Path
from .native_remote import BACKENDS, profile
from .provenance import source_fingerprint


def prepare(backend, cache):
    local, _, _ = profile(backend)
    if backend == "d3-edge":
        from .d3_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity()
    elif backend == "liquid-omni":
        from .liquid_omni_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity()
    else:
        from .liquid_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity(backend)
    checkpoint = resolve_checkpoint(cache, download=True)
    return {
        "backend": backend,
        "model": local.PROFILE["model"],
        "revision": local.PROFILE["revision"],
        "source_sha256": source_fingerprint(),
        "packages": packages,
        "cached_checkpoint": str(checkpoint),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    value = prepare(args.backend, args.cache)
    with Path(args.metadata).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
    print(json.dumps(value, allow_nan=False))


if __name__ == "__main__":
    main()
