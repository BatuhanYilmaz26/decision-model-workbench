"""Liquid omni controls with its native memory and dependency profile."""

from decision_workbench.training.liquid_omni_local import PROFILE, DEFAULTS
from .native_head import training_options as native_options


def training_options(config, capabilities, *, cloud=False):
    return native_options(
        config,
        capabilities,
        profile=PROFILE,
        defaults=DEFAULTS,
        label="Omni",
        cpu_gib=6,
        max_tokens=16384,
        checkpoint_label="omni",
        checkpoint_gb="2.35",
        cloud=cloud,
    )
