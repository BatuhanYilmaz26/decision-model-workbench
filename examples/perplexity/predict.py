"""Make one text/JSON Perplexity decision using your server-side API key."""

import argparse
import json
import sys
from pathlib import Path

from decision_workbench.json_tools import strict_json
from decision_workbench.platform.hosted import (
    HostedUnavailable,
    credential,
    decode_response,
    public_profile,
    request_body,
    validate_profile,
)
from decision_workbench.platform.hosted_transport import TransportFailure, run_process
from decision_workbench.platform.perplexity import MODELS


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        type=Path,
        required=True,
        help="JSON containing state and questions",
    )
    parser.add_argument("--model", choices=tuple(MODELS), default="pplx-decider-v1.2")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Total local request budget, 3–30 seconds",
    )
    args = parser.parse_args(argv)
    profile = {
        "id": "perplexity-example",
        "provider": "perplexity",
        "model": args.model,
        "response_model": args.model,
        "key_env": "DMT_PROVIDER_PERPLEXITY_API_KEY",
        "tenants": ["local"],
        "max_calls_day": 1,
        "max_inflight": 1,
        "timeout_seconds": args.timeout,
        "max_request_bytes": 64000,
    }
    try:
        validate_profile(profile)
        with args.request.open("rb") as stream:
            data = stream.read(64001)
        if len(data) > 64000:
            raise ValueError("Request file exceeds 64,000 bytes")
        value = strict_json(data)
        if not isinstance(value, dict) or set(value) != {"state", "questions"}:
            raise ValueError("Request needs exactly state and questions")
        body = request_body(profile, value["state"], value["questions"])
        raw = run_process(public_profile(profile), body, credential(profile))
        result = decode_response(strict_json(raw), profile, value["questions"])
    except (
        OSError,
        ValueError,
        TypeError,
        RecursionError,
        HostedUnavailable,
        TransportFailure,
    ):
        print(
            "Decision unavailable. Check your request, provider key and timeout; use your application's fallback. No automatic retry was made.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
