"""Native Perplexity training controls with explicit setup and resource guidance."""

import streamlit as st
from decision_workbench.training.perplexity_local import PROFILE, DEFAULTS


def training_options(config, questions, *, remote=False):
    config.update(PROFILE)
    config["device"] = "cuda"
    st.info(
        "Perplexity open v1.1 uses full-weight training on the selected Linux CUDA host. It needs about 107 GiB free VRAM, 428 GiB available RAM and 150 GiB free output storage beyond the preinstalled 52.2 GB checkpoint. These are startup floors; longer inputs may need more. No model download or paid API call is started by this form."
    )
    st.caption(
        (
            "Cloud studies need a compatible image with the pinned checkpoint already installed; preparation needs no local GPU. Independent admission and local serving still require the native runtime. "
            if remote
            else "An owner configures DMT_PERPLEXITY_PYTHON and DMT_PERPLEXITY_CHECKPOINT on the API/worker host. "
        )
        + "This is separate from a Perplexity API key. The final-epoch checkpoint uses a global temperature fitted on reserved calibration data; optimizer-state resume is unavailable."
    )
    st.link_button(
        "Set up native Perplexity training",
        "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/perplexity-native.md#train-through-the-application",
    )
    if remote:
        st.link_button(
            "Set up native Perplexity cloud training",
            "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/remote-training/perplexity.md",
        )
    st.link_button(
        "Use hosted Perplexity without a local GPU",
        "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/perplexity.md#evaluate-and-release-in-the-localhost-application",
    )
    config["epochs"] = int(
        st.number_input("Perplexity training epochs", 1, 20, DEFAULTS["epochs"])
    )
    config["batch_size"] = int(
        st.number_input(
            "Perplexity decisions per micro-batch", 1, 4, DEFAULTS["batch_size"]
        )
    )
    config["grad_accum"] = int(
        st.number_input(
            "Perplexity gradient accumulation", 1, 128, DEFAULTS["grad_accum"]
        )
    )
    config["max_length"] = int(
        st.number_input(
            "Perplexity complete input token budget", 64, 8192, DEFAULTS["max_length"]
        )
    )
    config["learning_rate"] = st.number_input(
        "Perplexity learning rate",
        min_value=0.00000001,
        max_value=0.0001,
        value=DEFAULTS["learning_rate"],
        format="%.8f",
    )
    with st.expander("Perplexity advanced training settings"):
        config["cpu_threads"] = int(
            st.number_input("Perplexity CPU threads", 1, 128, DEFAULTS["cpu_threads"])
        )
        config["eval_every"] = int(
            st.number_input(
                "Perplexity evaluation interval (optimizer updates)",
                1,
                10000,
                DEFAULTS["eval_every"],
            )
        )
    if any(q["type"] not in ("choice", "noul") for q in questions.values()):
        st.warning(
            "This native trainer accepts Choice and Noul only. Select another backend for Score questions, or define and review a separate compatible policy."
        )
        return False
    return True
