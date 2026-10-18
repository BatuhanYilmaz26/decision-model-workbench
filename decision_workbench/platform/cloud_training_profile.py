"""Declared image capabilities checked before any owner-approved allocation."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

LEGACY = ("laya-native", "unsloth")
SUPPORTED = (
    *LEGACY,
    "liquid-trl",
    "liquid-unsloth",
    "liquid-omni",
    "perplexity-native",
    *D3_BACKENDS,
)


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


def require_perplexity_resources(profile):
    """Reject known undersized allocations before the owner starts paid compute."""
    provider = profile["provider"]
    adequate = {
        "aws": profile["gpu"] in ("ml.p5e.48xlarge", "ml.p5en.48xlarge"),
        "azure": profile["gpu"] == "Standard_ND96isr_H200_v5",
        "gcp": profile["gpu"] == "NVIDIA_H200_141GB"
        and profile.get("machine_type") == "a3-ultragpu-8g",
        "modal": profile["gpu"] == "H200" and profile.get("memory_mb", 0) >= 524288,
        "runpod": profile["gpu"] == "NVIDIA H200"
        and profile.get("min_memory_gb", 0) >= 512,
    }.get(provider, False)
    if not adequate:
        raise ValueError(
            "Native Perplexity needs the documented H200 profile and at least 512 GB declared host RAM; use its cloud setup guide"
        )
    if provider in ("aws", "gcp", "runpod") and profile.get("volume_gb", 0) < 300:
        raise ValueError(
            "Reserve at least 300 GB provider storage for native Perplexity training"
        )
    if provider == "runpod" and profile.get("disk_gb", 0) < 100:
        raise ValueError(
            "Reserve at least 100 GB RunPod container storage in addition to its study volume"
        )
