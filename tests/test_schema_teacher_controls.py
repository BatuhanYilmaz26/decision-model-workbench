"""Explicit schema-teacher controls and immutable approved request checks."""

import hashlib
import json
import os
import unittest
from unittest.mock import patch

from decision_workbench.platform import schema_intake as intake

DOMAIN = {
    "name": "Example",
    "description": "Review fictional evidence",
    "data_requirements": "Independent domain records",
}


class SchemaTeacherControlTests(unittest.TestCase):
    def profile(self, **settings):
        with patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": "http://localhost:1234/v1",
                "DMT_TEACHER_MODEL": "fixture",
                **settings,
            },
            clear=True,
        ):
            return intake.teacher_profile()

    def payload(self, profile):
        body = intake.request_body(DOMAIN, profile)
        return {
            "profile": profile,
            "brief": DOMAIN,
            "request_body": body.decode(),
            "request_sha256": hashlib.sha256(body).hexdigest(),
        }

    def test_defaults_preserve_legacy_profile_and_request_shape(self):
        profile = self.profile()
        self.assertNotIn("token_parameter", profile)
        self.assertNotIn("max_output_tokens", profile)
        self.assertNotIn("temperature", profile)
        request = json.loads(self.payload(profile)["request_body"])
        self.assertEqual(request["max_tokens"], 3000)
        self.assertEqual(request["temperature"], 0.2)
        self.assertNotIn("max_completion_tokens", request)

    def test_modern_parameter_omits_temperature_and_preserves_approval(self):
        profile = self.profile(
            DMT_SCHEMA_TOKEN_PARAMETER="max_completion_tokens",
            DMT_SCHEMA_OUTPUT_TOKENS="4096",
            DMT_SCHEMA_TEMPERATURE="omit",
            DMT_TEACHER_RESPONSE_FORMAT="json_schema",
        )
        payload = self.payload(profile)
        body = intake.frozen_request(payload)
        request = json.loads(body)
        self.assertEqual(request["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("temperature", request)
        self.assertEqual(request["response_format"]["type"], "json_schema")
        with patch.object(
            intake,
            "request_body",
            side_effect=AssertionError("Never rebuild an approval"),
        ):
            self.assertEqual(intake.frozen_request(payload), body)

    def test_explicit_numeric_temperature_and_output_limits(self):
        for temperature in ("0", "0.6", "2"):
            profile = self.profile(
                DMT_SCHEMA_TEMPERATURE=temperature, DMT_SCHEMA_OUTPUT_TOKENS="256"
            )
            request = json.loads(intake.frozen_request(self.payload(profile)))
            self.assertEqual(request["temperature"], float(temperature))
            self.assertEqual(request["max_tokens"], 256)

    def test_invalid_environment_controls_refuse(self):
        for variable, value in (
            ("DMT_SCHEMA_TOKEN_PARAMETER", "max_output_tokens"),
            ("DMT_SCHEMA_OUTPUT_TOKENS", "0"),
            ("DMT_SCHEMA_OUTPUT_TOKENS", "16385"),
            ("DMT_SCHEMA_OUTPUT_TOKENS", "1.5"),
            ("DMT_SCHEMA_TEMPERATURE", "NaN"),
            ("DMT_SCHEMA_TEMPERATURE", "Infinity"),
            ("DMT_SCHEMA_TEMPERATURE", "3"),
        ):
            with (
                self.subTest(variable=variable, value=value),
                self.assertRaises(ValueError),
            ):
                self.profile(**{variable: value})

    def test_invalid_saved_controls_and_rehashed_request_changes_refuse(self):
        for key, value in (
            ("token_parameter", []),
            ("max_output_tokens", True),
            ("max_output_tokens", 1.5),
            ("temperature", True),
            ("temperature", float("inf")),
            ("unknown", 1),
        ):
            profile = self.profile()
            profile[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                intake.validate_profile(profile)
        for key, value in (
            ("max_tokens", 3001),
            ("max_tokens", True),
            ("temperature", True),
            ("temperature", 1.0),
        ):
            payload = self.payload(self.profile())
            request = json.loads(payload["request_body"])
            request[key] = value
            body = json.dumps(request).encode()
            payload.update(
                request_body=body.decode(),
                request_sha256=hashlib.sha256(body).hexdigest(),
            )
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                intake.frozen_request(payload)


if __name__ == "__main__":
    unittest.main()
