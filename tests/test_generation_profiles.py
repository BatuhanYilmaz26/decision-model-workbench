"""Synthetic teacher configuration and exact Decimal planning arithmetic."""

import copy
import os
import unittest
from unittest.mock import patch

from decision_workbench.platform.generation_profiles import (
    amount,
    estimate,
    selector,
    teacher_profile,
    token_cost,
    validate_profile,
)


class GenerationProfileTests(unittest.TestCase):
    def profile(self):
        with patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": "http://127.0.0.1:1234/v1",
                "DMT_TEACHER_MODEL": "fictional-teacher",
            },
            clear=True,
        ):
            return teacher_profile()

    def test_prices_are_unknown_until_both_rates_are_configured(self):
        profile = self.profile()
        self.assertIsNone(estimate(b"{}", profile)["usd"])
        self.assertEqual(profile["max_inflight"], 1)
        self.assertEqual(profile["timeout_seconds"], 120)
        bad = copy.deepcopy(profile)
        bad["input_usd_per_million"] = "1"
        with self.assertRaises(ValueError):
            validate_profile(bad)
        bad = copy.deepcopy(profile)
        bad["max_estimated_request_usd"] = "1"
        with self.assertRaises(ValueError):
            validate_profile(bad)

    def test_planning_allowance_and_reported_usage_arithmetic_are_distinct(self):
        profile = self.profile()
        profile.update(input_usd_per_million="2", output_usd_per_million="5")
        profile = validate_profile(profile)
        planned = estimate(b"{}", profile)
        self.assertEqual(planned["input_token_allowance"], 1026)
        self.assertEqual(planned["output_token_allowance"], 6000)
        self.assertEqual(planned["usd"], "0.03205200")
        self.assertEqual(token_cost(90, 20, profile), "0.00028000")
        self.assertEqual(profile["input_usd_per_million"], "2.00000000")

    def test_unsupported_control_types_and_nonfinite_money_refuse(self):
        for value in ("NaN", "Infinity", "-1", "0.000000001", "1000001", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amount(value)
        for field, value in (
            ("max_inflight", True),
            ("max_attempts", 6),
            ("timeout_seconds", 4),
            ("temperature", float("nan")),
            ("token_parameter", "max_output_tokens"),
        ):
            p = self.profile()
            p[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_profile(p)

    def test_optional_modern_token_parameter_and_omitted_temperature(self):
        with patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": "http://localhost:11434/v1",
                "DMT_TEACHER_MODEL": "fictional",
                "DMT_GENERATION_TEMPERATURE": "omit",
                "DMT_GENERATION_TOKEN_PARAMETER": "max_completion_tokens",
            },
            clear=True,
        ):
            p = teacher_profile()
        self.assertIsNone(p["temperature"])
        self.assertEqual(p["token_parameter"], "max_completion_tokens")

    def test_selection_normalizes_only_user_coverage_and_optional_retry(self):
        result = selector(
            {
                "snapshot": "a" * 32,
                "count": 2,
                "seed_count": 0,
                "coverage": "  Rare language cases  ",
            }
        )
        self.assertEqual(result["coverage"], "Rare language cases")
        self.assertIsNone(result["retry_of"])
        for field, value in (
            ("count", True),
            ("seed_count", 11),
            ("snapshot", None),
            ("coverage", 5),
            ("retry_of", []),
        ):
            bad = dict(result)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                selector(bad)
