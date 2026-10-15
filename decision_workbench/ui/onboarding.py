"""Personal-local entry and explicit shared-workspace connection."""

import os
from pathlib import Path

import httpx
import streamlit as st

REPO = "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/"
PROVIDERS = {
    "AWS": "docs/remote-training/aws.md",
    "Azure": "docs/remote-training/azure.md",
    "GCP": "docs/remote-training/gcp.md",
    "Modal": "docs/remote-training/modal.md",
    "RunPod": "docs/remote-training/runpod.md",
}


def loopback_server():
    return st.get_option("server.address") in ("127.0.0.1", "localhost", "::1")


def connect(url, token, *, personal=False):
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
        raise ValueError("Use an HTTP(S) API address without embedded credentials")
    if personal and (not loopback_server() or parsed.host != "127.0.0.1"):
        raise ValueError(
            "Personal mode requires loopback-bound browser and API services"
        )
    if (
        parsed.scheme != "https"
        and parsed.host not in ("localhost", "127.0.0.1", "::1")
        and url.rstrip("/") != os.environ.get("DMT_API_URL", "").rstrip("/")
    ):
        raise ValueError("Remote workspaces require HTTPS")
    with httpx.Client(timeout=10, trust_env=not personal) as client:
        response = client.get(
            url.rstrip("/") + "/v1/me", headers={"Authorization": "Bearer " + token}
        )
    if response.status_code != 200:
        raise ValueError("Workspace did not accept this credential")
    principal = response.json()
    if personal and principal != {
        "tenant": "local",
        "actor": "local-owner",
        "role": "admin",
    }:
        raise ValueError("Personal workspace identity did not match")
    st.session_state.update(
        credential=token,
        api_url=url.rstrip("/"),
        principal=principal,
        connection_mode="personal" if personal else "shared",
    )


@st.cache_resource(show_spinner=False)
def personal_workspace(home, root, _settings=None):
    from decision_workbench.platform.local import LocalWorkspace

    environment = os.environ.copy()
    environment.update(_settings or {})
    return LocalWorkspace(home, root, environment=environment).start()


def provider_help():
    with st.expander("Cloud GPU and hosted API setup"):
        st.write(
            "Keep your workspace on this computer while training runs on a cloud GPU. Configure credentials in the API/worker environment; approve data transfers and compute submissions inside your project."
        )
        provider = st.selectbox(
            "Cloud provider guide", list(PROVIDERS), key="onboarding-provider"
        )
        st.link_button("Set up " + provider + " training", REPO + PROVIDERS[provider])
        st.link_button(
            "Use a hosted decision API", REPO + "docs/hosted-decisions-guide.md"
        )
        st.link_button(
            "Configure synthetic generation",
            REPO + "docs/setup.md#configure-synthetic-generation",
        )
        st.caption(
            "Selecting a guide creates no resources. Managed cloud adapters require account, image and billing qualification."
        )


def render_connection(root):
    if "credential" in st.session_state:
        return
    local_url = os.environ.get("DMT_LOCAL_API_URL")
    local_token = os.environ.get("DMT_LOCAL_TOKEN")
    if local_url and local_token and not st.session_state.get("manual_connection"):
        try:
            connect(local_url, local_token, personal=True)
            return
        except (httpx.HTTPError, ValueError):
            st.error(
                "The personal service is unavailable. Restart domain-platform run; projects are retained."
            )
            st.stop()

    st.subheader("Train or use a decision model")
    st.write(
        "Create a domain project, add examples, generate and review synthetic data, then train and test a model. Start on your computer or connect to a workspace your team operates."
    )
    mode = st.radio(
        "Where is your workspace?",
        ["On this computer", "Shared workspace"],
        horizontal=True,
    )
    if mode == "On this computer":
        st.markdown("**Personal workspace · no account or workspace key required**")
        st.write(
            "CPU training is available without a GPU. Compatible model environments can use a local accelerator; cloud GPU and hosted API options are available in the same workspace. Local model runs keep data on this computer; provider use sends approved data to that provider."
        )
        if not loopback_server():
            st.info(
                "Personal mode needs a loopback-bound server. Start with domain-platform run, or streamlit run app.py --server.address 127.0.0.1. Shared deployments use the connection form below."
            )
        if st.button(
            "Start local workspace", type="primary", disabled=not loopback_server()
        ):
            try:
                if local_url and local_token:
                    connect(local_url, local_token, personal=True)
                    st.rerun()
                settings = {}
                model_python = st.session_state.get("local-model-python", "").strip()
                if model_python:
                    if not Path(model_python).is_file():
                        raise ValueError(
                            "Choose an existing model-environment Python executable"
                        )
                    settings.update(
                        DMT_WORKER_PYTHON=model_python, DMT_MODEL_PYTHON=model_python
                    )
                teacher_url = st.session_state.get("local-teacher-url", "").strip()
                teacher_model = st.session_state.get("local-teacher-model", "").strip()
                if teacher_url or teacher_model:
                    parsed = httpx.URL(teacher_url)
                    if (
                        not teacher_model
                        or not parsed.host
                        or parsed.userinfo
                        or parsed.scheme not in ("http", "https")
                        or (
                            parsed.scheme == "http"
                            and parsed.host not in ("localhost", "127.0.0.1", "::1")
                        )
                    ):
                        raise ValueError(
                            "Supply a teacher model and local HTTP or remote HTTPS base URL without embedded credentials"
                        )
                    settings.update(
                        DMT_TEACHER_BASE_URL=teacher_url,
                        DMT_TEACHER_MODEL=teacher_model,
                        DMT_TEACHER_API_KEY=st.session_state.get(
                            "local-teacher-key", ""
                        ),
                    )
                with st.spinner("Starting your local API and worker…"):
                    workspace = personal_workspace(
                        str(
                            Path(
                                os.environ.get("DMT_LOCAL_HOME", "data/local-workspace")
                            ).resolve()
                        ),
                        str(root),
                        settings,
                    )
                    workspace.check()
                    connect(workspace.url, workspace.token, personal=True)
                st.rerun()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                st.error(f"Could not start the local workspace: {exc}")
                st.caption(
                    "Install the platform and UI extras and inspect terminal output. Restart the app after correcting its environment."
                )
    with st.expander(
        "Connect to an existing workspace", expanded=mode == "Shared workspace"
    ):
        st.write(
            "For an existing operator workspace or shared deployment, your administrator supplies the API address and a workspace key or API access token."
        )
        with st.form("connect"):
            url = st.text_input(
                "Workspace API URL",
                os.environ.get("DMT_API_URL", "http://127.0.0.1:8080"),
            )
            token = st.text_input(
                "Workspace credential",
                type="password",
                help="Workspace key or enterprise API access token. ID tokens are not accepted.",
            )
            submitted = st.form_submit_button("Connect")
        if submitted:
            try:
                connect(url, token)
                st.rerun()
            except (httpx.HTTPError, ValueError):
                st.error(
                    "Could not connect. Check the API address, credential and whether the service is running."
                )
    provider_help()
    if mode == "On this computer" and loopback_server():
        with st.expander("Optional local model and synthetic generation settings"):
            st.caption(
                "Set these before starting. Settings apply to the local worker for this app lifetime; restart the app to change them. No models are installed automatically."
            )
            st.text_input(
                "Model-environment Python executable (optional)",
                key="local-model-python",
                help="Leave empty for CPU baseline. A compatible separate environment is required for Laya, Unsloth or Vega.",
            )
            st.write(
                "Synthetic generation uses a generative teacher; the trained decision model scores allowed answers. Use a local OpenAI-compatible server or an approved hosted endpoint supporting JSON object output."
            )
            st.text_input(
                "Teacher base URL (optional)",
                key="local-teacher-url",
                placeholder="http://127.0.0.1:8000/v1",
            )
            st.text_input(
                "Teacher model identifier (optional)", key="local-teacher-model"
            )
            st.text_input(
                "Teacher API key (optional)", key="local-teacher-key", type="password"
            )
            st.caption(
                "Credentials stay in the local server/worker memory. Training seeds leave this computer only if you approve generation using a remote teacher inside the project."
            )
    st.link_button("Installation and local model setup", REPO + "docs/setup.md")
    st.stop()


def render_runtime(api, principal):
    if principal["role"] != "admin":
        return None
    with st.expander("Computer, model environment and provider setup"):
        st.write(
            "CPU baseline training uses the workspace worker. Laya, Unsloth and Vega need their documented model dependencies. CUDA, Apple MPS and CPU support differ by backend; detected hardware does not prove training quality or sufficient memory."
        )
        if st.button("Check available hardware and model packages"):
            with st.spinner(
                "Inspecting the configured model interpreters; no model download…"
            ):
                st.session_state["runtime-capabilities"] = api(
                    "GET", "/v1/runtime-capabilities"
                )
        capability = st.session_state.get("runtime-capabilities")
        if capability:
            for kind, runtime in capability["runtimes"].items():
                st.markdown("**" + kind.capitalize() + " environment**")
                if runtime.get("error"):
                    st.warning(runtime["error"])
                else:
                    st.write(
                        f"{runtime['system']} · Python {runtime['python']} · CUDA {'available' if runtime['cuda'] else 'unavailable'} · Apple MPS {'available' if runtime['mps'] else 'unavailable'}"
                    )
                    if runtime["gpus"]:
                        st.dataframe(runtime["gpus"], hide_index=True)
                    st.dataframe(
                        [
                            {"Package": k, "Installed version": v or "Not installed"}
                            for k, v in runtime["packages"].items()
                        ],
                        hide_index=True,
                    )
            if not capability["teacher_configured"]:
                st.info(
                    "Synthetic generation needs a teacher endpoint and model. Use an approved local endpoint or hosted teacher; examples can be entered, uploaded and reviewed without one."
                )
        st.link_button(
            "Install a compatible model environment", REPO + "docs/training-guide.md"
        )
    provider_help()
    return st.session_state.get("runtime-capabilities")


def local_backend_available(backend, capabilities):
    """Availability hints; source/runtime qualification remains a separate gate."""
    if backend == "cpu-baseline":
        return capabilities[
            "cpu_baseline"
        ], "Install the platform dependencies in the worker environment."
    runtime = capabilities["runtimes"]["training"]
    package = {"laya-native": "laya", "unsloth": "unsloth", "vega": "vegaml"}[backend]
    if (
        runtime.get("error")
        or not runtime["packages"].get(package)
        or not runtime["packages"].get("torch")
    ):
        return (
            False,
            "Install the selected backend in a compatible model environment; see the model setup guide above.",
        )
    if backend == "unsloth" and not runtime["cuda"]:
        return (
            False,
            "This application's selected profile requires CUDA. Choose cloud GPU training or the CPU baseline.",
        )
    return True, ""
