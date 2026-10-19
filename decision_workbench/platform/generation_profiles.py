"""Operator-selected chat contracts and explicitly approximate token-cost budgets."""

import copy
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
import os

from decision_workbench.synthetic_contracts import teacher_address
from decision_workbench.teacher_formats import configured_format, format_mode

FIELDS = {
    "provider",
    "base_url",
    "model",
    "timeout_seconds",
    "max_calls_day",
    "max_inflight",
    "max_output_tokens",
    "token_parameter",
    "temperature",
    "input_usd_per_million",
    "output_usd_per_million",
    "max_estimated_request_usd",
    "max_estimated_day_usd",
    "max_attempts",
}


def amount(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("Enter a bounded decimal USD amount")
    try:
        number = Decimal(value)
        if (
            not number.is_finite()
            or not 0 <= number <= 1000000
            or number.as_tuple().exponent < -8
        ):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError(
            "USD amounts must be finite, nonnegative and have at most eight decimal places"
        ) from None
    if number == 0:
        number = Decimal(0)
    return format(number.quantize(Decimal("0.00000001")), "f")


def validate_profile(value):
    if (
        not isinstance(value, dict)
        or set(value) not in (FIELDS, FIELDS | {"response_format"})
        or value["provider"] != "generation-teacher"
    ):
        raise ValueError("Invalid synthetic teacher configuration")
    result = copy.deepcopy(value)
    if "response_format" in result:
        format_mode(result["response_format"])
    result["base_url"] = teacher_address(result["base_url"])
    model = result["model"]
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 200
        or model != model.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in model)
    ):
        raise ValueError("Configure a bounded teacher model identifier")
    for key, lower, upper in (
        ("timeout_seconds", 5, 180),
        ("max_calls_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_output_tokens", 256, 16384),
        ("max_attempts", 1, 5),
    ):
        if type(result[key]) is not int or not lower <= result[key] <= upper:
            raise ValueError(f"{key} must be an integer from {lower} to {upper}")
    if result["token_parameter"] not in ("max_tokens", "max_completion_tokens"):
        raise ValueError(
            "Choose max_tokens or max_completion_tokens for this compatible teacher"
        )
    temperature = result["temperature"]
    if temperature is not None and (
        type(temperature) not in (int, float)
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise ValueError(
            "Teacher temperature must be omitted or a finite number in [0,2]"
        )
    for key in (
        "input_usd_per_million",
        "output_usd_per_million",
        "max_estimated_request_usd",
        "max_estimated_day_usd",
    ):
        if result[key] is not None:
            result[key] = amount(result[key])
    if (result["input_usd_per_million"] is None) != (
        result["output_usd_per_million"] is None
    ):
        raise ValueError(
            "Configure both input and output token rates, or leave both unknown"
        )
    if result["input_usd_per_million"] is None and any(
        result[k] is not None
        for k in ("max_estimated_request_usd", "max_estimated_day_usd")
    ):
        raise ValueError("Estimated-cost limits require configured token prices")
    return result


def teacher_profile():
    base = os.environ.get("DMT_TEACHER_BASE_URL", "")
    model = os.environ.get("DMT_TEACHER_MODEL", "")
    if not base or not model:
        raise ValueError(
            "Configure a local or hosted generative teacher before previewing synthetic data"
        )

    def optional(name):
        return os.environ.get(name, "").strip() or None

    try:
        temperature = optional("DMT_GENERATION_TEMPERATURE")
        temperature = (
            0.7
            if temperature is None
            else (None if temperature.lower() == "omit" else float(temperature))
        )
        value = {
            "provider": "generation-teacher",
            "base_url": base,
            "model": model,
            "timeout_seconds": int(
                os.environ.get("DMT_GENERATION_TIMEOUT_SECONDS", "120")
            ),
            "max_calls_day": int(os.environ.get("DMT_GENERATION_CALLS_PER_DAY", "20")),
            "max_inflight": int(os.environ.get("DMT_GENERATION_MAX_INFLIGHT", "1")),
            "max_output_tokens": int(
                os.environ.get("DMT_GENERATION_OUTPUT_TOKENS", "6000")
            ),
            "max_attempts": int(os.environ.get("DMT_GENERATION_MAX_ATTEMPTS", "3")),
            "token_parameter": os.environ.get(
                "DMT_GENERATION_TOKEN_PARAMETER", "max_tokens"
            ),
            "temperature": temperature,
            "input_usd_per_million": optional("DMT_GENERATION_INPUT_USD_PER_MILLION"),
            "output_usd_per_million": optional("DMT_GENERATION_OUTPUT_USD_PER_MILLION"),
            "max_estimated_request_usd": optional(
                "DMT_GENERATION_MAX_ESTIMATED_REQUEST_USD"
            ),
            "max_estimated_day_usd": optional("DMT_GENERATION_MAX_ESTIMATED_DAY_USD"),
        }
    except (ValueError, OverflowError):
        raise ValueError(
            "Check the integer, temperature and price settings for the synthetic teacher"
        ) from None
    return validate_profile(configured_format(value))


def token_cost(prompt_tokens, completion_tokens, profile):
    if profile["input_usd_per_million"] is None:
        return None
    value = (
        Decimal(prompt_tokens) * Decimal(profile["input_usd_per_million"])
        + Decimal(completion_tokens) * Decimal(profile["output_usd_per_million"])
    ) / Decimal(1000000)
    return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING), "f")


def estimate(body, profile):
    # This planning allowance is deliberately not an exact tokenizer or bill cap.
    prompt = len(body) + 1024
    output = profile["max_output_tokens"]
    return {
        "method": "utf8-bytes-plus-1024-v1",
        "input_token_allowance": prompt,
        "output_token_allowance": output,
        "currency": "USD",
        "usd": token_cost(prompt, output, profile),
    }


def selector(config):
    result = {
        "snapshot": config.get("snapshot"),
        "count": config.get("count"),
        "seed_count": config.get("seed_count"),
        "coverage": config.get("coverage", ""),
        "retry_of": config.get("retry_of"),
    }
    if (
        not isinstance(result["snapshot"], str)
        or not 1 <= len(result["snapshot"]) <= 128
    ):
        raise ValueError("Choose a reserved data snapshot")
    if type(result["count"]) is not int or not 1 <= result["count"] <= 50:
        raise ValueError("Request 1–50 candidates")
    if type(result["seed_count"]) is not int or not 0 <= result["seed_count"] <= 10:
        raise ValueError("Select 0–10 approved training seeds")
    if not isinstance(result["coverage"], str) or len(result["coverage"]) > 4000:
        raise ValueError("Describe synthetic coverage in at most 4,000 characters")
    result["coverage"] = result["coverage"].strip()
    if result["retry_of"] is not None and (
        not isinstance(result["retry_of"], str)
        or not 1 <= len(result["retry_of"]) <= 128
    ):
        raise ValueError("Choose an existing generation request to retry")
    return result
