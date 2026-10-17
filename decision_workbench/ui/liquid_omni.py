"""Local native omni training controls, separate from causal SFT/LoRA profiles."""

import streamlit as st
from decision_workbench.training.liquid_omni_local import PROFILE


def training_options(config, capabilities):
    config.update(PROFILE)
    st.caption(
        "Pinned base: " + PROFILE["model"] + " · revision " + PROFILE["revision"]
    )
    config["device"] = st.selectbox(
        "Omni compute device",
        ["auto", "cuda", "cpu"],
        format_func=lambda value: {
            "auto": "Automatic: CUDA if available, otherwise CPU",
            "cuda": "NVIDIA CUDA GPU",
            "cpu": "CPU (at least 6 GiB available RAM)",
        }[value],
    )
    config["epochs"] = int(st.number_input("Omni training epochs", 1, 20, 1))
    config["batch_size"] = int(
        st.number_input("Omni decisions per micro-batch", 1, 16, 1)
    )
    config["grad_accum"] = int(st.number_input("Omni gradient accumulation", 1, 128, 4))
    config["max_length"] = int(
        st.number_input("Omni complete input token budget", 64, 16384, 2048)
    )
    config["learning_rate"] = st.number_input(
        "Omni learning rate",
        min_value=0.00000001,
        max_value=0.001,
        value=0.00001,
        format="%.8f",
    )
    steps = int(
        st.number_input(
            "Omni maximum optimizer updates (0 uses all epochs)", 0, 100000, 0
        )
    )
    if steps:
        config["max_steps"] = steps
    config["download"] = st.checkbox(
        "Allow downloading the pinned public omni checkpoint (about 2.35 GB)",
        value=False,
    )
    st.caption(
        "Model download does not upload your records. Use reviewed examples and at least eight reserved real calibration records. Soft target distributions are preserved. Long inputs and large batches require additional RAM/VRAM; unsupported native prompt lengths are refused."
    )
    hardware = (
        (capabilities or {})
        .get("model_profiles", {})
        .get("liquid-omni", {})
        .get("training", {})
    )
    if (
        capabilities
        and config["device"] == "cpu"
        and (hardware.get("ram_available_gib") or 0) < 6
    ):
        st.warning(
            "CPU omni needs at least 6 GiB available RAM. Choose another installed model or the CPU comparison baseline."
        )
        return False
    if capabilities and config["device"] == "cuda" and not hardware.get("cuda"):
        st.warning("CUDA is unavailable in the configured omni model environment.")
        return False
    return True
