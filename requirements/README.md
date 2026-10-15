# Dependency profiles

[All guides](../docs/README.md) · [Project overview](../README.md)

Use separate environments for the application and model training. Select a profile matching your interpreter, operating system and accelerator; a Windows snapshot is not a Linux lock.

For the standard local CPU application, follow the [root quick start](../README.md#run-on-your-computer) and run `python -m pip install -r requirements.txt` from the repository root. The [root requirements file](../requirements.txt) installs the editable project with its `ui` and `platform` extras from [pyproject.toml](../pyproject.toml). It uses compatible dependency ranges; the exact platform locks below serve repeatable installs. Model runtimes and provider clients remain optional, separately selected profiles.

| Profile | Target | Purpose |
|---|---|---|
| [Linux CPU lock](platform-linux-amd64-py312.txt) | Linux/amd64, CPython 3.12.15 | 87 hash-locked external dependencies for API, UI, CPU training and five provider control clients |
| [Linux Vega/semantic lock](vega-linux-amd64-py312-cu126.txt) | Linux/amd64, CPython 3.12.15, CUDA 12.6 | 124 hash-locked dependencies for the application, Vega and semantic encoder; use the separate [official Torch/Triton stage](torch-linux-amd64-py312-cu126.txt) |
| [Windows CPU lock](platform-windows-py313.txt) | Windows/amd64, CPython 3.13.5 | 88 hash-locked external dependencies for API, UI, CPU training and five provider control clients |
| [Windows model snapshot](models-windows-py312-cu126.txt) | CPython 3.12.13, CUDA 12.6 | Pinned native Laya and Unsloth encoder training/reload dependencies |
| [Vega runtime pins](vega-runtime.txt) | Matching model environment | vegaml 0.8.0 and direct runtime pins; not a complete transitive lock |
| [AWS client pins](aws-sdk.txt) | API/worker environment | Boto3/Botocore 1.43.113 control clients |
| [Modal client pins](modal-transport.txt) | API/worker environment | SDK 1.6.1 Sandbox transport |
| [Azure transport pins](azure-transport.txt) | API/worker environment | HTTP transport for the managed Azure controller |
| [GCP authentication pins](gcp-auth.txt) | API/worker environment | Authentication dependencies for the managed GCP controller |
| [RunPod transport pins](runpod-transport.txt) | API/worker environment | REST and signed storage transport dependencies |
| [Semantic encoder pins](semantic-models.txt) | Separate model environment | Direct pins for the local semantic-audit encoder |

The [CPU Linux input constraints](platform-linux-amd64-py312.in) and [Vega Linux input constraints](vega-linux-amd64-py312-cu126.in) are maintenance inputs for their corresponding locks. Use the `.txt` lock for installation; consult [runtime compatibility](../docs/compatibility-and-upgrades.md) before regenerating a profile. The [Torch/Triton Linux stage](torch-linux-amd64-py312-cu126.txt) supplies accelerator packages from their intended index.

Linux recipes pin the Python base digest, require hashed wheels and install project source without dependency resolution or build isolation. Build the selected image and run its checks before use. Model snapshots and direct runtime pins are platform-specific profiles rather than complete cross-platform locks. Review the [current versions and GPU dependency advisories](../docs/compatibility-and-upgrades.md) before selecting a training environment. Follow [testing](../docs/verification.md), [CPU containers](../docs/container-guide.md) or [model containers](../docs/model-container-guide.md).

## Windows CPU application

Use CPython 3.13.5 and install the hash-locked application dependencies before the project:

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements/platform-windows-py313.txt
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m pip check
```

## Windows model environment

This historical GPU profile has [known advisory and dependency conflicts](../docs/compatibility-and-upgrades.md#gpu-dependency-advisories) and needs remediation before production use. For isolated compatibility research, install CUDA wheels from the official index first, then the selected model dependencies and project source:

```powershell
py -3.12 -m venv .venv-models
.venv-models/Scripts/python.exe -m pip install torch==2.10.0+cu126 torchvision==0.25.0+cu126 --index-url https://download.pytorch.org/whl/cu126
.venv-models/Scripts/python.exe -m pip install -r requirements/models-windows-py312-cu126.txt --extra-index-url https://download.pytorch.org/whl/cu126
.venv-models/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv-models/Scripts/python.exe -m pip check
```

Pin Laya source and checkpoints separately. The Unsloth probability adapter requires Unsloth and Zoo 2026.10.3 and refuses other versions. Optional TorchAO C++ extensions are outside this encoder profile and require a newer Torch version. Validate quantization, xformers, replacement backbones and CUDA architectures before adding them. Use the [Unsloth installation guide](https://unsloth.ai/docs/get-started/install/pip-install) when preparing a new environment.

## Regenerate a Linux lock

Use uv 0.11.23 with the retained [CPU input pins](platform-linux-amd64-py312.in):

```bash
uv --no-config pip compile pyproject.toml requirements/platform-linux-amd64-py312.in --extra ui --extra platform --extra aws --extra azure --extra gcp --extra runpod --extra modal --python-version 3.12.15 --python-platform x86_64-unknown-linux-gnu --only-binary :all: --generate-hashes --default-index https://pypi.org/simple --output-file requirements/platform-linux-amd64-py312.txt
```

Vega regeneration and Torch/Triton index routing are described in the [model image guide](../docs/model-container-guide.md). Review changes, rebuild, reconcile installed packages and run the appropriate lifecycle/model checks before accepting an upgrade. Hashes bind distributions; they do not establish package safety, byte-identical images or domain quality. [uv locking](https://docs.astral.sh/uv/pip/compile/), [pip hash checking](https://pip.pypa.io/en/stable/topics/secure-installs/)
