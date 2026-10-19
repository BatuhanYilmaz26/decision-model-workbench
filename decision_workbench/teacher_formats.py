"""Explicit Chat Completions output formats for reviewed teacher proposals."""

import os

from .data import options


def format_mode(value):
    if not isinstance(value, str) or value not in ("json_object", "json_schema"):
        raise ValueError("Choose teacher response format json_object or json_schema")
    return value


def configured_format(profile):
    """Keep legacy profile bytes unchanged when no format was configured."""
    value = os.environ.get("DMT_TEACHER_RESPONSE_FORMAT", "").strip()
    if value:
        profile["response_format"] = format_mode(value)
    return profile


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def candidates_schema(questions, count):
    gold = {
        name: object_schema(
            {
                "probabilities": object_schema(
                    {
                        key: {"type": "number", "minimum": 0, "maximum": 1}
                        for key in options(question)
                    }
                )
            }
        )
        for name, question in questions.items()
    }
    row = object_schema(
        {
            "state": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object"},
                    {"type": "array", "items": {}},
                ]
            },
            "gold": object_schema(gold),
        }
    )
    return object_schema(
        {"rows": {"type": "array", "items": row, "minItems": count, "maxItems": count}}
    )


def suggestions_schema():
    # Question identifiers and outcomes are proposed by the teacher, then checked
    # by schema_intake.questions and explicitly confirmed by the owner.
    return object_schema(
        {
            "questions": {"type": "object", "minProperties": 1, "maxProperties": 8},
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
        }
    )


def response_format(mode, name, schema):
    if format_mode(mode) == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": False, "schema": schema},
    }


def archived_format(value, mode, name):
    """Check saved format identity without rebuilding a historical schema."""
    if format_mode(mode) == "json_object":
        return value == {"type": "json_object"}
    if not isinstance(value, dict) or set(value) != {"type", "json_schema"}:
        return False
    descriptor = value["json_schema"]
    if (
        value["type"] != mode
        or not isinstance(descriptor, dict)
        or set(descriptor) != {"name", "strict", "schema"}
        or descriptor["name"] != name
        or descriptor["strict"] is not False
    ):
        return False
    schema = descriptor["schema"]
    return (
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and schema.get("additionalProperties") is False
    )
