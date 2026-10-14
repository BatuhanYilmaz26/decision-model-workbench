"""Bounded mounted-volume result protocol; validation does not authenticate its writer."""

import re
from .cloud_aws import CloudUnavailable


def validate_result(run, value, result_format):
    required = {
        "format",
        "run_id",
        "archive_sha256",
        "request_sha256",
        "source_sha256",
        "status",
    }
    if not isinstance(value, dict) or not required <= set(value):
        raise CloudUnavailable("Mounted-volume result marker is invalid")
    if (
        value["format"] != result_format
        or value["run_id"] != run["id"]
        or value["archive_sha256"] != run["archive_sha256"]
        or value["request_sha256"] != run["request_sha256"]
        or value["source_sha256"] != run["profile"]["source_sha256"]
        or value["status"] not in ("succeeded", "failed")
    ):
        raise CloudUnavailable("Mounted-volume result marker binding differs")
    if value["status"] == "failed":
        if set(value) != required:
            raise CloudUnavailable("Failed result has unexpected fields")
    elif (
        set(value)
        != required | {"artifact_sha256", "artifact_bytes", "manifest_sha256"}
        or any(
            not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
            for k in ("artifact_sha256", "manifest_sha256")
        )
        or type(value["artifact_bytes"]) is not int
        or not 1 <= value["artifact_bytes"] <= run["profile"]["max_artifact_bytes"]
    ):
        raise CloudUnavailable("Successful artifact marker differs")
    return value
