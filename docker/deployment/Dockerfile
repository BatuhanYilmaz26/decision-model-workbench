# Build the repository's pinned CPU image first, then extend that exact image.
ARG BASE_IMAGE=domain-decision-platform:local
FROM ${BASE_IMAGE}
USER root
COPY requirements/managed-application-linux-amd64-py312.txt ./requirements/managed-application-linux-amd64-py312.txt
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps \
    -r requirements/managed-application-linux-amd64-py312.txt \
    && python -m pip check
# Include the current source even when the selected base is an older local build.
COPY decision_workbench ./decision_workbench
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY app.py ./app.py
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .
# This public trust bundle contains certificates, not application credentials.
RUN python - <<'PY'
from pathlib import Path
import urllib.request
from cryptography import x509

url = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
with urllib.request.urlopen(url, timeout=30) as response:
    body = response.read(1024 * 1024 + 1)
if len(body) > 1024 * 1024 or not x509.load_pem_x509_certificates(body):
    raise ValueError("Invalid or oversized RDS CA bundle")
folder = Path("/app/certificates")
folder.mkdir(exist_ok=True)
(folder / "rds-global.pem").write_bytes(body)
PY
USER platform
CMD ["python", "-m", "decision_workbench.platform.deployment", "api"]
