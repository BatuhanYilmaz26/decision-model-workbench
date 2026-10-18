"""D3 Edge controls for the native readout and calibrated serving device."""

from decision_workbench.training.d3_local import DEFAULTS
from decision_workbench.training.d3_profiles import get_profile
from .native_head import training_options as native_options


def training_options(config, capabilities, *, cloud=False):
    selected = get_profile(config["backend"])
    return native_options(
        config,
        capabilities,
        profile=selected.fields,
        defaults=DEFAULTS,
        label="D3",
        cpu_gib=selected.cpu_gib,
        max_tokens=32768,
        checkpoint_label=selected.label,
        checkpoint_gb=f"{(selected.weight_bytes + selected.readout_bytes) / 1e9:.2f}",
        cloud=cloud,
    )
