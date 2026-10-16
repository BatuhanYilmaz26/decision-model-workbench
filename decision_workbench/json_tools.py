"""Strict JSON parsing for persisted requests and untrusted model output."""

import json


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("JSON contains duplicate keys")
            result[key] = item
        return result

    def nonfinite(_):
        raise ValueError("Nonfinite JSON value")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=nonfinite)
