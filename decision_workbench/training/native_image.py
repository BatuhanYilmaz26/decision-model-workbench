"""Check a native model image's runtime and cache its pinned public checkpoint."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS, get_profile

import argparse
import json
from pathlib import Path
from .native_remote import BACKENDS, profile
from .provenance import source_fingerprint


def prepare(backend, cache):
    local, _, _ = profile(backend)
    if backend == "perplexity-native":
        from .perplexity_runtime import runtime_identity
        from .perplexity_checkpoint import download_checkpoint

        packages = runtime_identity()
        checkpoint = download_checkpoint(cache)
    elif backend in D3_BACKENDS:
        from .d3_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity()
    elif backend == "liquid-omni":
        from .liquid_omni_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity()
    else:
        from .liquid_runtime import resolve_checkpoint, runtime_identity

        packages = runtime_identity(backend)
    if backend != "perplexity-native":
        checkpoint = resolve_checkpoint(
            cache,
            download=True,
            **({"backend": backend} if backend in D3_BACKENDS else {}),
        )
    selected = get_profile(backend).fields if backend in D3_BACKENDS else local.PROFILE
    return {
        "backend": backend,
        "model": selected["model"],
        "revision": selected["revision"],
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
