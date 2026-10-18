"""Declared image capabilities checked before any owner-approved allocation."""

LEGACY = ("laya-native", "unsloth")
SUPPORTED = (*LEGACY, "liquid-trl", "liquid-unsloth", "liquid-omni", "d3-edge")


def training_backends(profile):
    if not isinstance(profile, dict):
        raise ValueError("Cloud profile must be a JSON object")
    values = profile.get("training_backends", list(LEGACY))
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= len(SUPPORTED)
        or any(not isinstance(v, str) or v not in SUPPORTED for v in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("Declare unique compatible image training_backends")
    return tuple(values)


def provider_fields(profile):
    training_backends(profile)
    return {k: v for k, v in profile.items() if k != "training_backends"}
