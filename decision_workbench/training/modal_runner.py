"""Offline Linux Modal Sandbox runner; final Volume commit occurs on shutdown."""

import argparse
from pathlib import Path
from .runpod_runner import execute


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("archive-sha256", "request-sha256", "run-id", "source-sha256"):
        parser.add_argument("--" + key, required=True)
    for key in ("archive", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    for key in ("max-artifact-bytes", "max-runtime-seconds"):
        parser.add_argument("--" + key, type=int, required=True)
    return execute(
        **vars(parser.parse_args(argv)),
        root=Path("/tmp/domain-modal"),
        result_format="domain-modal-result-v1",
    )


if __name__ == "__main__":
    main()
