"""Teacher formats preserve legacy requests and constrain reviewed proposals."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from decision_workbench.synthetic_contracts import request_body
from decision_workbench.teacher_formats import archived_format, format_mode
from decision_workbench.platform import generation_profiles, schema_intake


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Choose a route",
        "criteria": {"a": "A", "b": "B"},
    },
    "review": {"type": "noul", "instructions": "Needs review?"},
    "level": {
        "type": "score",
        "instructions": "Choose a level",
        "criteria": ["Low", "High"],
    },
}
SPEC = {"description": "Fictional bounded policy", "questions": QUESTIONS}


class TeacherFormatTests(unittest.TestCase):
    def environment(self, mode=None):
        result = {
            "DMT_TEACHER_BASE_URL": "http://localhost:1234/v1",
            "DMT_TEACHER_MODEL": "fixture",
        }
        if mode is not None:
            result["DMT_TEACHER_RESPONSE_FORMAT"] = mode
        return patch.dict(os.environ, result, clear=True)

    def test_default_request_and_profile_remain_legacy_compatible(self):
        self.assertEqual(
            request_body(SPEC, [], "fixture", 1),
            request_body(SPEC, [], "fixture", 1, output_format="json_object"),
        )
        with self.environment():
            for module in (generation_profiles, schema_intake):
                profile = module.teacher_profile()
                self.assertNotIn("response_format", profile)
                self.assertEqual(module.validate_profile(profile), profile)

    def test_format_configuration_is_explicit_and_shared(self):
        for mode in ("json_object", "json_schema"):
            with self.environment(mode):
                for module in (generation_profiles, schema_intake):
                    self.assertEqual(module.teacher_profile()["response_format"], mode)
        for mode in (None, True, {}, "text", "JSON_SCHEMA"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                format_mode(mode)
        with self.environment("text"):
            for module in (generation_profiles, schema_intake):
                with self.assertRaises(ValueError):
                    module.teacher_profile()

    def test_schema_binds_count_and_all_three_target_shapes(self):
        body = json.loads(
            request_body(SPEC, [], "fixture", 3, output_format="json_schema")
        )
        fmt = body["response_format"]
        self.assertTrue(archived_format(fmt, "json_schema", "decision_candidates"))
        self.assertIs(fmt["json_schema"]["strict"], False)
        rows = fmt["json_schema"]["schema"]["properties"]["rows"]
        self.assertEqual((rows["minItems"], rows["maxItems"]), (3, 3))
        gold = rows["items"]["properties"]["gold"]
        self.assertCountEqual(gold["required"], QUESTIONS)
        for question, labels in (
            ("route", ["a", "b"]),
            ("review", ["false", "true"]),
            ("level", ["0", "1"]),
        ):
            probabilities = gold["properties"][question]["properties"]["probabilities"]
            self.assertCountEqual(probabilities["required"], labels)
            self.assertFalse(probabilities["additionalProperties"])

    def test_archived_schema_does_not_depend_on_current_builder(self):
        fmt = json.loads(
            request_body(SPEC, [], "fixture", 1, output_format="json_schema")
        )["response_format"]
        changed = copy.deepcopy(fmt)
        changed["json_schema"]["schema"][
            "description"
        ] = "Historical schema, retained exactly"
        self.assertTrue(archived_format(changed, "json_schema", "decision_candidates"))
        self.assertFalse(archived_format(fmt, "json_object", "decision_candidates"))
        for value in ({}, [], {"type": "json_schema", "json_schema": None}):
            self.assertFalse(
                archived_format(value, "json_schema", "decision_candidates")
            )
        for field, value in (
            ("name", "other"),
            ("strict", True),
            ("schema", {"type": "array"}),
        ):
            bad = copy.deepcopy(fmt)
            bad["json_schema"][field] = value
            self.assertFalse(archived_format(bad, "json_schema", "decision_candidates"))

    def test_schema_suggestions_use_the_selected_format(self):
        with self.environment("json_schema"):
            profile = schema_intake.teacher_profile()
        body = json.loads(
            schema_intake.request_body(
                {
                    "name": "Example",
                    "description": SPEC["description"],
                    "data_requirements": "Fictional evidence",
                },
                profile,
            )
        )
        self.assertTrue(
            archived_format(body["response_format"], "json_schema", "decision_schema")
        )
        self.assertCountEqual(
            body["response_format"]["json_schema"]["schema"]["required"],
            ["questions", "assumptions"],
        )

    def test_schema_format_still_enforces_complete_request_byte_limit(self):
        large = copy.deepcopy(SPEC)
        large["description"] = "x" * 64000
        for mode in ("json_object", "json_schema"):
            with self.assertRaisesRegex(ValueError, "Nothing was truncated"):
                request_body(large, [], "fixture", 1, output_format=mode)


if __name__ == "__main__":
    unittest.main()
