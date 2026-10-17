"""D3 Edge controls for the native readout and calibrated serving device."""

from decision_workbench.training.d3_local import PROFILE, DEFAULTS
from .native_head import training_options as native_options


def training_options(config, capabilities):
    return native_options(
        config,
        capabilities,
        profile=PROFILE,
        defaults=DEFAULTS,
        label="D3",
        cpu_gib=3,
        max_tokens=32768,
        checkpoint_label="D3 Edge",
        checkpoint_gb="1.19",
    )
