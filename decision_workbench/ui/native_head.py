"""Shared controls for native-head profiles with explicit per-model limits."""

import streamlit as st


def training_options(
    config,
    capabilities,
    *,
    profile,
    defaults,
    label,
    cpu_gib,
    max_tokens,
    checkpoint_label,
    checkpoint_gb,
    cloud=False,
):
    config.update(profile)
    st.caption(
        "Pinned base: " + profile["model"] + " · revision " + profile["revision"]
    )
    config["device"] = st.selectbox(
        f"{label} compute device",
        ["cuda"] if cloud else ["auto", "cuda", "cpu"],
        format_func=lambda value: {
            "auto": "Automatic: CUDA if available, otherwise CPU",
            "cuda": "NVIDIA CUDA GPU",
            "cpu": f"CPU (at least {cpu_gib} GiB available RAM)",
        }[value],
    )
    config["epochs"] = int(st.number_input(f"{label} training epochs", 1, 20, 1))
    config["batch_size"] = int(
        st.number_input(f"{label} decisions per micro-batch", 1, 16, 1)
    )
    config["grad_accum"] = int(
        st.number_input(f"{label} gradient accumulation", 1, 128, 4)
    )
    config["max_length"] = int(
        st.number_input(
            f"{label} complete input token budget",
            64,
            max_tokens,
            defaults["max_length"],
        )
    )
    config["learning_rate"] = st.number_input(
        f"{label} learning rate",
        min_value=0.00000001,
        max_value=0.001,
        value=defaults["learning_rate"],
        format="%.8f",
    )
    steps = int(
        st.number_input(
            f"{label} maximum optimizer updates (0 uses all epochs)", 0, 100000, 0
        )
    )
    if steps:
        config["max_steps"] = steps
    checkpoint_download = st.checkbox(
        f"Allow downloading the pinned public {checkpoint_label} checkpoint (about {checkpoint_gb} GB)",
        value=False,
        disabled=cloud,
    )
    config["download"] = checkpoint_download if not cloud else False
    st.caption(
        "Model download does not upload your records. Use reviewed examples and at least eight reserved real calibration records. Soft target distributions are preserved. Long inputs and large batches require additional RAM/VRAM; unsupported native prompt lengths are refused."
    )
    if cloud:
        st.caption(
            "The approved cloud image must include this exact pinned checkpoint and its compatible runtime. Training downloads are disabled, including on offline providers."
        )
        return True
    hardware = (
        (capabilities or {})
        .get("model_profiles", {})
        .get(profile["backend"], {})
        .get("training", {})
    )
    if (
        capabilities
        and config["device"] == "cpu"
        and (hardware.get("ram_available_gib") or 0) < cpu_gib
    ):
        st.warning(
            f"CPU {label} needs at least {cpu_gib} GiB available RAM. Choose another installed model or the CPU comparison baseline."
        )
        return False
    if capabilities and config["device"] == "cuda" and not hardware.get("cuda"):
        st.warning(f"CUDA is unavailable in the configured {label} model environment.")
        return False
    return True
