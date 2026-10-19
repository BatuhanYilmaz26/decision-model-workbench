"""Request and validate one synthetic batch from a selected chat teacher."""

import uuid
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from .synthetic_contracts import (
    MAX_RESPONSE,
    teacher_address,
    request_body,
    completion,
    candidate_rows,
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def generate(
    spec,
    examples,
    *,
    base_url,
    model,
    api_key="",
    count=10,
    request=None,
    output_format="json_object",
):
    base_url = teacher_address(base_url)
    if (
        not isinstance(api_key, str)
        or len(api_key) > 8192
        or any(ord(c) < 32 or ord(c) == 127 for c in api_key)
    ):
        raise ValueError("Teacher credentials must be text without control characters")
    body = request_body(spec, examples, model, count, output_format=output_format)
    headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    transport = request or build_opener(_NoRedirect).open
    req = Request(base_url + "/chat/completions", data=body, headers=headers)
    try:
        with transport(req, timeout=120) as response:
            if (
                getattr(response, "status", 200) != 200
                or getattr(response, "headers", {}).get("Content-Encoding", "identity")
                != "identity"
            ):
                raise ValueError("Teacher returned an unsupported HTTP response")
            payload = response.read(MAX_RESPONSE + 1)
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
    except HTTPError as exc:
        raise ValueError(
            f"Teacher HTTP {exc.code}; check model, quota and JSON-output support"
        ) from None
    return candidate_rows(
        spec,
        examples,
        completion(payload),
        count,
        batch_id=uuid.uuid4().hex,
        provenance={"teacher_model": model},
    )
