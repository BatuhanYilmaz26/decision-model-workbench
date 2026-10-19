"""Call an approved workbench release; return review on abstention or failure."""

import argparse
import getpass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys

import httpx

MAX_REQUEST_BYTES = 128000
MAX_RESPONSE_BYTES = 1024 * 1024


def strict_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON fields")
            result[key] = value
        return result

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number

    def constant(value):
        raise ValueError("Nonfinite JSON constant")

    return json.loads(
        body, object_pairs_hook=pairs, parse_float=finite, parse_constant=constant
    )


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def encoded_request(request):
    if (
        not isinstance(request, dict)
        or not {"state", "expected_release"} <= set(request)
        or set(request) - {"state", "expected_release", "threshold", "request_key"}
    ):
        raise ValueError("Provide state and a complete expected_release")
    pin = request["expected_release"]
    if not isinstance(pin, dict) or set(pin) != {
        "model_id",
        "release_revision",
        "schema_sha256",
    }:
        raise ValueError("Provide the complete release identity")
    for name, width in (("model_id", 32), ("schema_sha256", 64)):
        if not isinstance(pin[name], str) or not re.fullmatch(
            r"[a-f0-9]{" + str(width) + "}", pin[name]
        ):
            raise ValueError("Invalid release identity")
    if (
        type(pin["release_revision"]) is not int
        or not 1 <= pin["release_revision"] <= 9223372036854775807
    ):
        raise ValueError("Invalid release revision")
    threshold = request.get("threshold", 0.8)
    if not number(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid confidence threshold")
    if not isinstance(request["state"], (str, dict, list)) or not request["state"]:
        raise ValueError("Provide nonempty text or JSON evidence")
    if (
        len(json.dumps(request["state"], ensure_ascii=False, allow_nan=False).encode())
        > 64000
    ):
        raise ValueError("Evidence exceeds the API byte bound")
    if "request_key" in request and (
        not isinstance(request["request_key"], str)
        or not 8 <= len(request["request_key"]) <= 128
    ):
        raise ValueError("Use an 8-128 character logical request key")
    body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Request exceeds the client byte bound")
    return body


def endpoint(base_url, project):
    if not isinstance(project, str) or not re.fullmatch(r"[a-f0-9]{32}", project):
        raise ValueError("Invalid project ID")
    url = httpx.URL(base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Use a plain API base URL without credentials or query")
    if url.scheme == "http" and url.host != "localhost":
        try:
            loopback = ipaddress.ip_address(url.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("Use HTTPS for a remote API")
    return url.copy_with(
        path=url.path.rstrip("/") + f"/v1/projects/{project}/decisions"
    )


def checked_result(payload, request):
    pin = request["expected_release"]
    if (
        not isinstance(payload, dict)
        or type(payload.get("release_revision")) is not int
        or any(payload.get(k) != v for k, v in pin.items())
    ):
        raise ValueError("Response changed the approved release")
    answers = payload.get("answers")
    if (
        not isinstance(answers, dict)
        or not answers
        or type(payload.get("abstained")) is not bool
    ):
        raise ValueError("Incomplete decision response")
    review = payload["abstained"]
    for answer in answers.values():
        if not isinstance(answer, dict) or type(answer.get("abstained")) is not bool:
            raise ValueError("Invalid question response")
        probabilities = answer.get("probabilities")
        if (
            not isinstance(probabilities, dict)
            or not probabilities
            or any(not number(p) or not 0 <= p <= 1 for p in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-5)
        ):
            raise ValueError("Invalid probabilities")
        confidence = answer.get("confidence")
        if not number(confidence) or not math.isclose(
            confidence, max(probabilities.values()), abs_tol=1e-5
        ):
            raise ValueError("Invalid confidence")
        value = answer.get("answer")
        key = (
            ("true" if value else "false")
            if type(value) is bool
            else str(value) if type(value) in (str, int) else None
        )
        if key not in probabilities or not math.isclose(
            probabilities[key], max(probabilities.values()), abs_tol=1e-12
        ):
            raise ValueError("Invalid selected outcome")
        if "model_abstained" in answer and type(answer["model_abstained"]) is not bool:
            raise ValueError("Invalid model abstention")
        review |= (
            answer["abstained"]
            or answer.get("model_abstained", False)
            or confidence < request.get("threshold", 0.8)
        )
    if review:
        return {
            "status": "review_required",
            "reason": "abstained",
            "release": dict(pin),
        }
    return {"status": "accepted", "decision": payload}


def request_decision(base_url, project, key, request, *, timeout=420, client=None):
    """One attempt only. Callers route every review_required result to fallback."""
    try:
        body = encoded_request(request)
        url = endpoint(base_url, project)
        if (
            not isinstance(key, str)
            or not key
            or "\r" in key
            or "\n" in key
            or not number(timeout)
            or timeout <= 0
        ):
            raise ValueError("Invalid credential or timeout")
    except (ValueError, TypeError, RecursionError, httpx.InvalidURL):
        return {"status": "review_required", "reason": "invalid_request"}
    owned = client is None
    client = client or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        with client.stream(
            "POST",
            url,
            content=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(
                timeout, connect=min(5.0, timeout), pool=min(5.0, timeout)
            ),
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                return {
                    "status": "review_required",
                    "reason": (
                        "conflict" if response.status_code == 409 else "http_error"
                    ),
                    "http_status": response.status_code,
                }
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("Unexpected response encoding")
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("Response exceeds the client byte bound")
        return checked_result(strict_json(bytes(data)), request)
    except httpx.HTTPError:
        return {"status": "review_required", "reason": "unavailable"}
    except (ValueError, TypeError, RecursionError):
        return {"status": "review_required", "reason": "invalid_response"}
    finally:
        if owned:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=420)
    args = parser.parse_args(argv)
    try:
        if args.request.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("Request file exceeds the byte bound")
        request = strict_json(args.request.read_bytes())
        encoded_request(request)
        key = os.environ.get("DMT_INFERENCE_KEY")
        if not key:
            if not sys.stdin.isatty():
                raise ValueError("Set DMT_INFERENCE_KEY for noninteractive use")
            key = getpass.getpass("Workspace inference key: ")
        result = request_decision(
            args.base_url, args.project, key, request, timeout=args.timeout
        )
    except (OSError, ValueError, TypeError, RecursionError, EOFError):
        result = {"status": "review_required", "reason": "invalid_request"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
