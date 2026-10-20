"""Exercise the local Docker image end to end on explicitly fictional fixtures.

No provider profiles, enterprise records or paid GPU resources are used. Optional
Vega qualification uses a selected local GPU and approved offline public cache. Receipts
and private logs remain under --output. Cleanup verifies the unique Compose label.
Build the image first; see docs/container-guide.md for prerequisites and scope.
"""

import argparse
import hashlib
import io
import json
import re
import secrets
import socket
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from decision_workbench.data import hard_gold, jsonl
from decision_workbench.training.provenance import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]


QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Route invoice to billing and crashes to engineering",
        "criteria": {
            "billing": "invoice and payment",
            "engineering": "software crash and bug",
        },
    },
    "refund": {"type": "noul", "instructions": "Was a refund requested?"},
    "urgency": {
        "type": "score",
        "instructions": "none, normal or urgent",
        "criteria": ["routine", "normal", "urgent"],
    },
}


def fixture_rows(count=180):
    rows = []
    for i in range(count):
        labels = {
            "team": "billing" if i % 2 == 0 else "engineering",
            "refund": i % 2 == 0,
            "urgency": i % 3,
        }
        message = (
            (
                "invoice payment refund requested"
                if i % 2 == 0
                else "software crash bug no refund needed"
            )
            + " "
            + ["routine", "normal", "urgent"][i % 3]
            + f" sourcecase{i:05d}"
        )
        rows.append(
            {
                "id": f"case-{i}",
                "group_id": f"group-{i}",
                "state": message,
                "questions": QUESTIONS,
                "gold": hard_gold(QUESTIONS, labels),
                "metadata": {"origin": "real", "reviewed": True, "fixture": True},
            }
        )
    return rows


def gate_rows(count=40):
    return [
        {
            "id": f"weather-{index}",
            "group_id": f"weather-source-{index}",
            "state": f"Fictional weather bulletin {index}: clear sky.",
            "negative_for": list(QUESTIONS),
            "reason": "No support, refund or service evidence",
            "metadata": {
                "origin": "real",
                "reviewed": True,
                "review_status": "approved",
                "reviewer": "forged",
                "review_note": "forged",
                "fictional_fixture": True,
            },
        }
        for index in range(count)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default="domain-decision-platform:local",
        help="Already built local image containing this exact worktree source",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/container-checks",
        help="Private receipt/log parent; a fresh directory is created for each run",
    )
    parser.add_argument(
        "--backend", choices=["cpu-baseline", "vega"], default="cpu-baseline"
    )
    parser.add_argument(
        "--model-image",
        help="Retained Vega image; API/worker use this while browser uses --image",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        help="Approved offline public Hub cache; mounted read-only",
    )
    parser.add_argument(
        "--gpu-device",
        help="One local NVIDIA device ID or UUID, shared by API and worker",
    )
    parser.add_argument(
        "--docker-context",
        help="Explicit Docker context; otherwise use operator's configured context",
    )
    parser.add_argument(
        "--vega-resource-checks",
        action="store_true",
        help="Also exercise queued eviction and cancellation of a real local Vega training child",
    )
    parser.add_argument(
        "--vega-crash-checks",
        action="store_true",
        help="Also kill an owned secondary worker parent during actual Vega feature extraction; includes resource checks",
    )
    args = parser.parse_args()
    if args.vega_crash_checks:
        args.vega_resource_checks = True
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", args.image):
        parser.error(
            "Use an ordinary Docker image reference without spaces or interpolation"
        )
    gpu = args.backend == "vega"
    if args.vega_resource_checks and not gpu:
        parser.error("Resource checks require --backend vega")
    if gpu:
        if not args.model_image or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", args.model_image
        ):
            parser.error("Vega requires an ordinary retained --model-image reference")
        if (
            args.model_cache is None
            or not args.model_cache.expanduser().resolve().is_dir()
        ):
            parser.error("Vega requires an existing approved --model-cache directory")
        if not args.gpu_device or not re.fullmatch(r"[A-Za-z0-9_-]+", args.gpu_device):
            parser.error("Vega requires one --gpu-device ID or UUID")
    elif any(
        value is not None
        for value in (args.model_image, args.model_cache, args.gpu_device)
    ):
        parser.error("Model image/cache/device arguments require --backend vega")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output / ("accept-" + uuid.uuid4().hex)
    work.mkdir(exist_ok=False)
    config = work / "config"
    config.mkdir()
    project = "dmt-gate-accept-" + uuid.uuid4().hex[:10]
    tokens = {
        role: secrets.token_urlsafe(32)
        for role in ("owner", "reviewer", "inference", "other")
    }
    keys = [
        {
            "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "tenant": "B" if role == "other" else "A",
            "actor": role,
            "role": {
                "owner": "admin",
                "reviewer": "reviewer",
                "inference": "inference",
                "other": "admin",
            }[role],
        }
        for role, token in tokens.items()
    ]
    (config / "operator-keys.json").write_text(json.dumps(keys), encoding="utf-8")
    image = args.image
    common = {
        "image": image,
        "volumes": [
            "workspace:/app/data",
            str(config).replace("\\", "/") + ":/run/config:ro",
        ],
        "environment": {
            "DMT_HOME": "/app/data/workspace",
            "DMT_KEYS_FILE": "/run/config/operator-keys.json",
        },
        "networks": ["isolated"],
    }
    with socket.socket() as api_socket, socket.socket() as web_socket:
        api_socket.bind(("127.0.0.1", 0))
        web_socket.bind(("127.0.0.1", 0))
        api_port, web_port = api_socket.getsockname()[1], web_socket.getsockname()[1]
    definition = {
        "services": {
            "api": {
                **common,
                "networks": ["isolated", "entrypoint"],
                "command": [
                    "domain-platform",
                    "serve",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                ],
                "ports": [f"127.0.0.1:{api_port}:8080"],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=2)",
                    ],
                    "interval": "3s",
                    "timeout": "3s",
                    "retries": 40,
                },
            },
            "worker": {**common, "command": ["domain-worker"]},
            "web": {
                **common,
                "networks": ["isolated", "entrypoint"],
                "command": [
                    "python",
                    "-m",
                    "streamlit",
                    "run",
                    "app.py",
                    "--server.address",
                    "0.0.0.0",
                    "--server.headless",
                    "true",
                ],
                "environment": {
                    **common["environment"],
                    "DMT_API_URL": "http://api:8080",
                },
                "ports": [f"127.0.0.1:{web_port}:8501"],
            },
        },
        "volumes": {"workspace": {}},
        "networks": {"isolated": {"internal": True}, "entrypoint": {}},
    }
    if gpu:
        cache = args.model_cache.expanduser().resolve()
        for name in ("api", "worker"):
            service = definition["services"][name]
            service["image"] = args.model_image
            service["environment"] = {
                **common["environment"],
                "DMT_MODEL_PYTHON": "/usr/local/bin/python",
                "DMT_MODEL_CACHE": "/app/model-cache/hub",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "DMT_INFERENCE_IDLE_SECONDS": "240",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
            }
            service["volumes"] = [
                *common["volumes"],
                {
                    "type": "bind",
                    "source": str(cache),
                    "target": "/app/model-cache/hub",
                    "read_only": True,
                },
            ]
            service["deploy"] = {
                "resources": {
                    "reservations": {
                        "devices": [
                            {
                                "driver": "nvidia",
                                "device_ids": [args.gpu_device],
                                "capabilities": ["gpu"],
                            }
                        ]
                    }
                }
            }
    if args.vega_crash_checks:
        # A real PID-1 reaper keeps the container alive while ordinary worker
        # processes (PID > 1) can be stopped and independent orphans reaped.
        supervisor = (
            "import os,signal,subprocess,sys;"
            "child=subprocess.Popen(['domain-worker']);\n"
            "def stop(sig,frame): child.terminate()\n"
            "signal.signal(signal.SIGTERM,stop)\n"
            "while True:\n"
            " try: pid,status=os.waitpid(-1,0)\n"
            " except InterruptedError: continue\n"
            " if pid==child.pid: sys.exit(os.waitstatus_to_exitcode(status))\n"
        )
        definition["services"]["worker"]["command"] = ["python", "-c", supervisor]
    compose = work / "compose.json"
    compose.write_text(json.dumps(definition, indent=2), encoding="utf-8")
    context = ["--context", args.docker_context] if args.docker_context else []
    base = ["compose", "--project-name", project, "--file", str(compose)]
    checks, commands = [], []
    start = time.monotonic()

    def docker(args, timeout=180):
        done = subprocess.run(
            ["docker", *context, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        commands.append({"arguments": args, "exit_code": done.returncode})
        with (work / "docker.log").open("a", encoding="utf-8") as log:
            log.write(done.stdout + done.stderr + "\n")
        if done.returncode:
            raise AssertionError(
                "Owned Docker operation failed; private log records details: "
                + " ".join(args[:4])
            )
        return done.stdout.strip()

    def command(args, timeout=180):
        return docker(base + args, timeout)

    def ready(client, url):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        raise AssertionError("Owned fixture service did not become ready")

    record = {
        "project": project,
        "image": image,
        "scope": "Actual Linux containers with explicitly fictional positive/negative fixtures; "
        + args.backend
        + " mechanics, no domain efficacy/enterprise/provider acceptance",
        "backend": args.backend,
        "docker_context": args.docker_context,
        "model_image": args.model_image,
        "vega_resource_checks": args.vega_resource_checks,
        "vega_crash_checks": args.vega_crash_checks,
        "source_sha256": source_fingerprint(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "paid_compute": False,
        "external_data_upload": False,
        "checks": checks,
    }
    try:
        command(["config", "--quiet"])
        command(["up", "--no-build", "--detach", "--wait", "--wait-timeout", "120"])
        checks.append("three_isolated_services_started")
        api_url = "http://" + command(["port", "api", "8080"])
        web_url = "http://" + command(["port", "web", "8501"])
        assert (
            api_url == f"http://127.0.0.1:{api_port}"
            and web_url == f"http://127.0.0.1:{web_port}"
        )
        checks.append("fixed_loopback_ports_bound")
        with httpx.Client(
            base_url=api_url,
            headers={"Authorization": "Bearer " + tokens["owner"]},
            timeout=420 if gpu else 30,
        ) as client:
            ready(client, "/ready")
            ready(client, web_url + "/_stcore/health")
            checks.extend(["api_worker_dependency_readiness", "streamlit_http_health"])
            uid = command(["exec", "-T", "api", "id", "-u"])
            assert uid == "10001", uid
            checks.append("non_root_uid_10001")
            runtime = json.loads(
                command(
                    [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "-c",
                        "import json,sys,platform,importlib.metadata;print(json.dumps({'python':sys.version,'system':platform.platform(),'packages':{name:importlib.metadata.version(name) for name in ('domain-decisions','fastapi','uvicorn','streamlit','scikit-learn','numpy','httpx','openpyxl','PyJWT')}}))",
                    ]
                )
            )
            installed_source = command(
                [
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "from decision_workbench.training.provenance import source_fingerprint;print(source_fingerprint())",
                ]
            )
            assert installed_source == record["source_sha256"], (
                installed_source,
                record["source_sha256"],
            )
            checks.append("linux_package_source_equals_worktree")
            command(["exec", "-T", "api", "python", "-m", "pip", "check"])
            checks.append("linux_installed_dependencies_consistent")
            profile_name = (
                "requirements/vega-linux-amd64-py312-cu126.txt"
                if gpu
                else "requirements/platform-linux-amd64-py312.txt"
            )
            profile = ROOT / profile_name
            installed_profile = json.loads(
                command(
                    [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "-c",
                        "import hashlib,json,re,importlib.metadata as m;"
                        "from pathlib import Path;"
                        "p=Path('/app/" + profile_name + "');"
                        "normalize=lambda n:re.sub(r'[-_.]+','-',n).lower();"
                        "print(json.dumps({'lock_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),"
                        "'packages':{normalize(d.metadata['Name']):d.version for d in m.distributions()}}))",
                    ]
                )
            )
            normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
            pins = {
                normalize(name): version
                for name, version in re.findall(
                    r"^([A-Za-z0-9_.-]+)==([^\s]+)",
                    profile.read_text(encoding="utf-8"),
                    re.M,
                )
            }
            assert (
                pins
                and installed_profile["lock_sha256"]
                == hashlib.sha256(profile.read_bytes()).hexdigest()
            ), "Container dependency lock differs from the worktree"
            assert pins == {
                name: version
                for name, version in installed_profile["packages"].items()
                if name != "domain-decisions"
            }, "Container dependency versions or package inventory differ from the lock"
            record["dependency_profile"] = {
                "file": profile_name,
                "sha256": installed_profile["lock_sha256"],
                "pin_count": len(pins),
                "packages": installed_profile["packages"],
            }
            checks.append("linux_hash_lock_and_all_installed_versions_match_worktree")
            app_hash = command(
                [
                    "exec",
                    "-T",
                    "web",
                    "python",
                    "-c",
                    "import hashlib;from pathlib import Path;print(hashlib.sha256(Path('/app/app.py').read_bytes()).hexdigest())",
                ]
            )
            assert (
                app_hash == hashlib.sha256((ROOT / "app.py").read_bytes()).hexdigest()
            )
            record["app_sha256"] = app_hash
            browser_source = command(
                [
                    "exec",
                    "-T",
                    "web",
                    "python",
                    "-c",
                    "from decision_workbench.training.provenance import source_fingerprint;print(source_fingerprint())",
                ]
            )
            assert browser_source == record["source_sha256"]
            record["browser_source_sha256"] = browser_source
            checks.append("container_browser_source_equals_worktree")
            record["runtime"] = runtime
            record["image_id"] = json.loads(
                docker(["image", "inspect", args.model_image if gpu else image])
            )[0]["Id"]
            record["browser_image_id"] = json.loads(
                docker(["image", "inspect", image])
            )[0]["Id"]
            if gpu:
                actual_cuda = json.loads(
                    command(
                        [
                            "exec",
                            "-T",
                            "worker",
                            "python",
                            "-c",
                            "import json,torch;assert torch.cuda.is_available();print(json.dumps({'device':torch.cuda.get_device_name(0),'cuda':torch.version.cuda,'device_count':torch.cuda.device_count()}))",
                        ]
                    )
                )
                assert actual_cuda["device_count"] == 1
                record["cuda"] = actual_cuda
                checks.append("actual_worker_container_selected_cuda_device")

            def request(method, path, role="owner", expected=200, **kwargs):
                response = client.request(
                    method,
                    path,
                    headers={"Authorization": "Bearer " + tokens[role]},
                    **kwargs,
                )
                assert response.status_code == expected, (
                    path,
                    response.status_code,
                    response.text[:1000],
                )
                return response

            created = request(
                "POST",
                "/v1/projects",
                expected=201,
                json={
                    "name": "Container mechanical fixture",
                    "description": "Fictional support policy used to verify Linux workflow, not actual enterprise gold.",
                    "questions": QUESTIONS,
                },
            ).json()
            prefix = "/v1/projects/" + created["id"]
            positive = fixture_rows(800 if gpu else 180)
            dataset = request(
                "POST",
                prefix + "/datasets",
                expected=201,
                files={"file": ("positive-fixture.jsonl", jsonl(positive).encode())},
            ).json()
            for row in positive:
                dataset = request(
                    "POST",
                    prefix + "/datasets/" + dataset["id"] + "/reviews",
                    role="reviewer",
                    expected=201,
                    json={
                        "record_id": row["id"],
                        "gold": row["gold"],
                        "approved": True,
                        "expected_hash": dataset["payload"]["sha256"],
                        "note": "Fictional label checked for container mechanics only",
                    },
                ).json()
            checks.append(f"{len(positive)}_authenticated_immutable_positive_reviews")

            def job(kind, configuration):
                queued = request(
                    "POST",
                    prefix + "/jobs",
                    expected=202,
                    json={
                        "kind": kind,
                        "config": configuration,
                        "request_key": uuid.uuid4().hex,
                    },
                ).json()
                deadline = time.monotonic() + (900 if gpu and kind == "train" else 90)
                while time.monotonic() < deadline:
                    value = next(
                        item
                        for item in request("GET", prefix + "/jobs").json()
                        if item["id"] == queued["id"]
                    )
                    if value["status"] in (
                        "succeeded",
                        "failed",
                        "cancelled",
                        "interrupted",
                    ):
                        assert value["status"] == "succeeded", value
                        return value["result"]
                    time.sleep(0.25)
                raise AssertionError("Owned container worker job did not finish")

            prepared = job("prepare", {"datasets": [dataset["id"]]})
            request(
                "PUT",
                prefix + "/release-policy",
                json={
                    "min_accuracy": 0.0,
                    "min_macro_f1": 0.0,
                    "max_ece": 1.0,
                    "min_test_rows": 10,
                    **(
                        {
                            "min_training_examples_per_class": 0,
                            "min_calibration_examples_per_class": 0,
                            "vega": {
                                "min_positive_accepted_fraction": 0,
                                "max_negative_false_open_rate": 1,
                                "min_negative_test_rows": 10,
                            },
                        }
                        if gpu
                        else {}
                    ),
                },
            )
            request("GET", prefix, role="other", expected=404)
            request(
                "GET",
                prefix + "/datasets/" + dataset["id"] + "/rows",
                role="inference",
                expected=403,
            )
            checks.append("tenant_and_inference_role_data_refusal")

            negatives = gate_rows(240 if gpu else 40)
            gate_dataset = request(
                "POST",
                prefix + "/gate-datasets",
                expected=201,
                files={"file": ("negative-fixture.jsonl", jsonl(negatives).encode())},
            ).json()
            initial = request(
                "GET", prefix + "/gate-datasets/" + gate_dataset["id"] + "/rows"
            ).json()
            assert all(
                not row["metadata"]["reviewed"] and "reviewer" not in row["metadata"]
                for row in initial["rows"]
            )
            for row in negatives:
                gate_dataset = request(
                    "POST",
                    prefix + "/gate-datasets/" + gate_dataset["id"] + "/reviews",
                    role="reviewer",
                    expected=201,
                    json={
                        "record_id": row["id"],
                        "negative_for": row["negative_for"],
                        "reason": row["reason"],
                        "status": "approved",
                        "expected_hash": gate_dataset["payload"]["sha256"],
                        "note": "Fictional applicability fixture reviewed, not domain gold",
                    },
                ).json()
            gate = job(
                "gate-prepare",
                {"snapshot": prepared["snapshot"], "datasets": [gate_dataset["id"]]},
            )
            inputs = request(
                "GET", prefix + "/gate-snapshots/" + gate["gate_snapshot"] + "/download"
            )
            assert (
                inputs.headers["X-DMT-Archive-SHA256"]
                == hashlib.sha256(inputs.content).hexdigest()
            )
            with zipfile.ZipFile(io.BytesIO(inputs.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert len(manifest["files"]) == 8
                for name, meta in manifest["files"].items():
                    assert (
                        hashlib.sha256(archive.read(name)).hexdigest() == meta["sha256"]
                    )
            checks.extend(
                [
                    "forged_negative_approval_reset",
                    f"{len(negatives)}_authenticated_applicability_reviews",
                    "coupled_negative_worker_reservation_eight_file_export",
                ]
            )
            record["input_archive_sha256"] = hashlib.sha256(inputs.content).hexdigest()

            trained = job(
                "train",
                {
                    "snapshot": prepared["snapshot"],
                    "backend": args.backend,
                    **(
                        {
                            "gate_snapshot": gate["gate_snapshot"],
                            "offline": True,
                            "epochs": 1,
                            "batch_size": 16,
                            "rank": 4,
                            "max_steps": 6,
                        }
                        if gpu
                        else {}
                    ),
                },
            )
            request(
                "POST",
                prefix + "/releases",
                json={
                    "model_id": trained["model"],
                    "expected_revision": 0,
                    "note": "Fixture-only Linux mechanics release; no domain efficacy acceptance",
                },
            )
            state = positive[0]["state"]
            before = request(
                "POST",
                prefix + "/decisions",
                role="inference",
                json={"state": state, "threshold": 0.8},
            ).json()
            assert set(before["answers"]) == set(QUESTIONS)
            checks.extend(
                [
                    "separate_worker_four_role_preparation",
                    (
                        "actual_linux_vega_gpu_gated_training"
                        if gpu
                        else "actual_linux_cpu_specialist_training"
                    ),
                    "owner_release_authenticated_three_type_inference",
                ]
            )
            if gpu:
                models = request("GET", prefix + "/entities/model").json()
                record["trained_model_entity"] = next(
                    value for value in models if value["id"] == trained["model"]
                )
                report = record["trained_model_entity"]["payload"]["report"]
                assert report["backend"] == "vega" and report["hardware"][
                    "device"
                ].startswith("cuda")
                assert (
                    report["training"]["optimizer_updates"] == 6
                    and report["local_study_sha256"]
                )
                assert (
                    report["export_qualification"]["passed"]
                    and report["export_qualification"]["same_uncertainty"]
                )
                assert (
                    len(report["data_sha256"]) == len(report["gate_data_sha256"]) == 4
                )
                lease_code = (
                    "import json;from decision_workbench.training.resources import ModelResourceLease,ResourceBusy;"
                    "lease=ModelResourceLease('/app/data/workspace');\n"
                    "try:\n lease.acquire(timeout=0)\n"
                    "except ResourceBusy:\n print(json.dumps({'blocked':True}))\n"
                    "else:\n lease.release();print(json.dumps({'blocked':False}))"
                )
                contention = json.loads(
                    command(["exec", "-T", "worker", "python", "-c", lease_code])
                )
                assert contention == {
                    "blocked": True
                }, "Resident API must hold the shared worker GPU lease"
                checks.append("resident_api_blocks_worker_container_shared_gpu_lease")
                command(["stop", "api"])
                freed = json.loads(
                    command(["exec", "-T", "worker", "python", "-c", lease_code])
                )
                assert freed == {
                    "blocked": False
                }, "API shutdown must release its cross-container lease"
                checks.append("api_container_shutdown_releases_shared_gpu_lease")
                command(
                    [
                        "up",
                        "--no-build",
                        "--detach",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        "api",
                    ]
                )
                ready(client, "/ready")
                reopened = request(
                    "POST",
                    prefix + "/decisions",
                    role="inference",
                    json={"state": state, "threshold": 0.8},
                ).json()
                assert reopened == before
                checks.append("api_restart_reloads_identical_vega_predictions")
                if args.vega_resource_checks:

                    def processes(service, job_id=""):
                        code = (
                            "import json,sys,re;from pathlib import Path;"
                            "found={'runtime':[],'trainer':[],'secondary_worker':[]};"
                            "targets={b'decision_workbench.training.runtime':'runtime',b'decision_workbench.training.vega_runner':'trainer',b'decision_workbench.platform.worker':'secondary_worker'};\n"
                            "for node in Path('/proc').iterdir():\n"
                            " if not node.name.isdigit(): continue\n"
                            " try: args=(node/'cmdline').read_bytes().split(b'\\0')\n"
                            " except OSError: continue\n"
                            " for target,kind in targets.items():\n"
                            "  if target in args: found[kind].append(int(node.name))\n"
                            "job=sys.argv[1];assert not job or re.fullmatch('[a-f0-9]{32}',job);"
                            "log=Path('/app/data/workspace/jobs')/job/'training.log';"
                            "body=log.read_text(encoding='utf-8',errors='replace')[-1048576:] if job and log.is_file() else '';"
                            "found['feature_extraction_observed']=bool(re.search(r'features positive [1-9][0-9]*/',body));"
                            "found['optimizer_epoch_completed']='epoch 1 updates=' in body;print(json.dumps(found))"
                        )
                        return json.loads(
                            command(
                                ["exec", "-T", service, "python", "-c", code, job_id]
                            )
                        )

                    def queued_training():
                        return request(
                            "POST",
                            prefix + "/jobs",
                            expected=202,
                            json={
                                "kind": "train",
                                "request_key": uuid.uuid4().hex,
                                "config": {
                                    "backend": "vega",
                                    "snapshot": prepared["snapshot"],
                                    "gate_snapshot": gate["gate_snapshot"],
                                    "offline": True,
                                    "epochs": 1,
                                    "batch_size": 16,
                                    "rank": 4,
                                    "max_steps": 6,
                                },
                            },
                        ).json()

                    def state_of(job_id):
                        return next(
                            value
                            for value in request("GET", prefix + "/jobs").json()
                            if value["id"] == job_id
                        )

                    resource = {}
                    command(["stop", "worker"])
                    resident = processes("api")
                    assert len(resident["runtime"]) == 1 and not resident["trainer"]
                    queued = queued_training()
                    begin = time.monotonic()
                    deadline = begin + 30
                    while True:
                        evicted = processes("api")
                        if not evicted["runtime"]:
                            break
                        assert (
                            time.monotonic() < deadline
                        ), "Pending training did not evict resident inference"
                        time.sleep(0.25)
                    assert state_of(queued["id"])["status"] == "queued"
                    assert json.loads(
                        command(["exec", "-T", "api", "python", "-c", lease_code])
                    ) == {"blocked": False}
                    request(
                        "POST",
                        prefix + "/decisions",
                        role="inference",
                        expected=503,
                        json={"state": state, "threshold": 0.8},
                    )
                    resource["queued_eviction"] = {
                        "seconds": time.monotonic() - begin,
                        "resident_before": resident["runtime"],
                        "resident_after": evicted["runtime"],
                        "lease_free": True,
                        "inference_status": 503,
                    }
                    checks.append(
                        "queued_vega_training_evicts_resident_and_refuses_inference"
                    )
                    cancelled = request(
                        "POST", prefix + "/jobs/" + queued["id"] + "/cancel"
                    ).json()
                    assert (
                        cancelled["status"] == "cancelled"
                        and cancelled["result"] is None
                    )
                    recovered = request(
                        "POST",
                        prefix + "/decisions",
                        role="inference",
                        json={"state": state, "threshold": 0.8},
                    ).json()
                    assert recovered == before
                    resource["queued_cancel"] = {
                        "status": cancelled["status"],
                        "same_released_prediction": True,
                    }
                    checks.append("queued_cancel_reopens_original_released_gpu_model")

                    running = queued_training()
                    command(["start", "worker"])
                    deadline = time.monotonic() + 180
                    while True:
                        phase = processes("worker", running["id"])
                        status = state_of(running["id"])
                        if phase["trainer"] and phase["feature_extraction_observed"]:
                            break
                        assert status["status"] in ("queued", "running"), status
                        assert (
                            time.monotonic() < deadline
                        ), "Actual Vega child did not reach feature extraction"
                        time.sleep(0.25)
                    assert (
                        status["status"] == "running"
                        and not phase["optimizer_epoch_completed"]
                    )
                    request(
                        "POST",
                        prefix + "/jobs/" + running["id"] + "/cancel",
                        role="inference",
                        expected=403,
                    )
                    request(
                        "POST",
                        prefix + "/decisions",
                        role="inference",
                        expected=503,
                        json={"state": state, "threshold": 0.8},
                    )
                    cancelling = request(
                        "POST", prefix + "/jobs/" + running["id"] + "/cancel"
                    ).json()
                    assert cancelling["status"] == "cancel_requested"
                    begin = time.monotonic()
                    deadline = begin + 60
                    while True:
                        terminal = state_of(running["id"])
                        ended = processes("worker", running["id"])
                        if terminal["status"] == "cancelled" and not ended["trainer"]:
                            break
                        assert terminal["status"] in (
                            "running",
                            "cancel_requested",
                            "cancelled",
                        ), terminal
                        assert (
                            time.monotonic() < deadline
                        ), "Cancelled training child or lease did not end"
                        time.sleep(0.25)
                    assert (
                        terminal["result"] is None
                        and terminal["token"] is None
                        and terminal["lease"] is None
                    )
                    assert not processes("api")["runtime"]
                    assert json.loads(
                        command(["exec", "-T", "worker", "python", "-c", lease_code])
                    ) == {"blocked": False}
                    models_after = request("GET", prefix + "/entities/model").json()
                    assert [model["id"] for model in models_after] == [trained["model"]]
                    resource["running_cancel"] = {
                        "seconds": time.monotonic() - begin,
                        "trainer_before": phase["trainer"],
                        "trainer_after": ended["trainer"],
                        "feature_extraction_observed": True,
                        "status": terminal["status"],
                        "lease_free": True,
                        "registered_model_count": 1,
                        "inference_role_cancel_status": 403,
                    }
                    checks.append(
                        "actual_vega_child_cancel_terminates_process_releases_lease_without_registration"
                    )
                    resumed = request(
                        "POST",
                        prefix + "/decisions",
                        role="inference",
                        json={"state": state, "threshold": 0.8},
                    ).json()
                    assert resumed == before
                    resource["after_cancel"] = {"same_released_prediction": True}
                    record["resource_transitions"] = resource
                    checks.append(
                        "post_cancel_original_resident_model_reloads_identically"
                    )
                    if args.vega_crash_checks:
                        # Keep PID 1/container alive: killing its entrypoint
                        # would let Docker clean up the child and mask a bug.
                        pause = (
                            "import os,signal,json,time;from pathlib import Path;found=[];\n"
                            "for node in Path('/proc').iterdir():\n"
                            " if not node.name.isdigit() or int(node.name)<=1: continue\n"
                            " try: args=(node/'cmdline').read_bytes().split(b'\\0')\n"
                            " except OSError: continue\n"
                            " if any(a.endswith(b'/domain-worker') for a in args): found.append(int(node.name))\n"
                            "assert len(found)==1;pid=found[0];os.kill(pid,signal.SIGSTOP);\n"
                            "for _ in range(100):\n"
                            " state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]\n"
                            " if state=='T': break\n"
                            " time.sleep(.01)\n"
                            "assert state=='T';print(json.dumps({'main_worker_pid':pid}))"
                        )
                        paused = json.loads(
                            command(["exec", "-T", "worker", "python", "-c", pause])
                        )
                        try:
                            crashed = queued_training()
                            command(
                                [
                                    "exec",
                                    "-d",
                                    "worker",
                                    "python",
                                    "-m",
                                    "decision_workbench.platform.worker",
                                    "--once",
                                    "--home",
                                    "/app/data/workspace",
                                ]
                            )
                            deadline = time.monotonic() + 180
                            while True:
                                phase = processes("worker", crashed["id"])
                                current_job = state_of(crashed["id"])
                                if (
                                    phase["trainer"]
                                    and phase["feature_extraction_observed"]
                                ):
                                    break
                                assert current_job["status"] in (
                                    "queued",
                                    "running",
                                ), current_job
                                assert (
                                    time.monotonic() < deadline
                                ), "Secondary real worker did not reach feature extraction"
                                time.sleep(0.25)
                            assert (
                                len(phase["secondary_worker"])
                                == len(phase["trainer"])
                                == 1
                            )
                            assert (
                                current_job["status"] == "running"
                                and not phase["optimizer_epoch_completed"]
                            )
                            parent_pid, trainer_pid = (
                                phase["secondary_worker"][0],
                                phase["trainer"][0],
                            )
                            kill_parent = (
                                "import os,signal,sys,json;from pathlib import Path;"
                                "from decision_workbench.training.resources import model_resource_path;"
                                "lease_path=str(model_resource_path());"
                                "parent,child=map(int,sys.argv[1:]);assert parent>1 and child>1;"
                                "args=Path(f'/proc/{parent}/cmdline').read_bytes().split(b'\\0');"
                                "assert b'decision_workbench.platform.worker' in args and b'--once' in args;"
                                "status=Path(f'/proc/{child}/status').read_text();"
                                "assert f'PPid:\\t{parent}\\n' in status;"
                                "fds=list(Path(f'/proc/{child}/fd').iterdir());"
                                "held=0;\n"
                                "for f in fds:\n"
                                " try: held+=os.readlink(f)==lease_path\n"
                                " except FileNotFoundError: pass\n"
                                "assert held>=1;os.kill(parent,signal.SIGKILL);"
                                "print(json.dumps({'trainer_inherited_lease_fds':held}))"
                            )
                            begin = time.monotonic()
                            inherited = json.loads(
                                command(
                                    [
                                        "exec",
                                        "-T",
                                        "worker",
                                        "python",
                                        "-c",
                                        kill_parent,
                                        str(parent_pid),
                                        str(trainer_pid),
                                    ]
                                )
                            )
                            deadline = time.monotonic() + 15
                            while True:
                                ended = processes("worker", crashed["id"])
                                if (
                                    not ended["trainer"]
                                    and not ended["secondary_worker"]
                                ):
                                    break
                                assert (
                                    time.monotonic() < deadline
                                ), "Model child survived parent SIGKILL"
                                time.sleep(0.1)
                            assert json.loads(
                                command(
                                    ["exec", "-T", "worker", "python", "-c", lease_code]
                                )
                            ) == {"blocked": False}
                            assert state_of(crashed["id"])["status"] == "running"
                            request(
                                "POST",
                                prefix + "/decisions",
                                role="inference",
                                expected=503,
                                json={"state": state, "threshold": 0.8},
                            )
                            crash_result = {
                                "seconds_to_observed_child_exit": time.monotonic()
                                - begin,
                                "parent_pid": parent_pid,
                                "trainer_pid": trainer_pid,
                                "feature_extraction_observed": True,
                                "trainer_after": ended["trainer"],
                                "secondary_worker_after": ended["secondary_worker"],
                                "lease_free": True,
                                **inherited,
                                "container_entrypoint_alive": True,
                            }
                            checks.append(
                                "actual_secondary_worker_sigkill_ends_vega_child_with_container_alive"
                            )
                        finally:
                            command(
                                [
                                    "exec",
                                    "-T",
                                    "worker",
                                    "python",
                                    "-c",
                                    "import os,signal,sys;from pathlib import Path;pid=int(sys.argv[1]);assert pid>1;args=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0');assert any(a.endswith(b'/domain-worker') for a in args);os.kill(pid,signal.SIGCONT)",
                                    str(paused["main_worker_pid"]),
                                ]
                            )
                        deadline = time.monotonic() + 60
                        while True:
                            interrupted = state_of(crashed["id"])
                            if interrupted["status"] == "interrupted":
                                break
                            assert (
                                interrupted["status"] == "running"
                                and time.monotonic() < deadline
                            ), interrupted
                            time.sleep(0.25)
                        assert (
                            interrupted["result"] is None
                            and interrupted["token"] is None
                            and interrupted["lease"] is None
                        )
                        models_after = request("GET", prefix + "/entities/model").json()
                        assert [model["id"] for model in models_after] == [
                            trained["model"]
                        ]
                        resumed = request(
                            "POST",
                            prefix + "/decisions",
                            role="inference",
                            json={"state": state, "threshold": 0.8},
                        ).json()
                        assert resumed == before
                        crash_result.update(
                            job_status="interrupted",
                            registered_model_count=1,
                            same_released_prediction=True,
                        )
                        record["worker_parent_crash"] = crash_result
                        checks.append(
                            "crashed_worker_lease_expiry_interrupts_without_retry_and_reloads_original_model"
                        )

            prior_ids = set(command(["ps", "--all", "--quiet"]).splitlines())
            command(["stop"])
            command(
                [
                    "up",
                    "--no-build",
                    "--detach",
                    "--force-recreate",
                    "--wait",
                    "--wait-timeout",
                    "120",
                ]
            )
            next_ids = set(command(["ps", "--all", "--quiet"]).splitlines())
            assert len(prior_ids) == len(next_ids) == 3 and not prior_ids.intersection(
                next_ids
            )
            ready(client, "/ready")
            after = request(
                "POST",
                prefix + "/decisions",
                role="inference",
                json={"state": state, "threshold": 0.8},
            ).json()
            assert before == after, (before, after)
            persisted = request("GET", prefix + "/entities/gate-snapshot").json()
            assert persisted[0]["id"] == gate["gate_snapshot"]
            again = request(
                "GET", prefix + "/gate-snapshots/" + gate["gate_snapshot"] + "/download"
            )
            with zipfile.ZipFile(io.BytesIO(again.content)) as archive:
                assert json.loads(archive.read("manifest.json")) == manifest
            checks.extend(
                [
                    "all_three_containers_replaced_named_volume_preserved",
                    "released_predictions_identical_after_replacement",
                    "applicability_archive_manifest_preserved_after_replacement",
                ]
            )
            backup = json.loads(
                command(
                    [
                        "exec",
                        "-T",
                        "api",
                        "domain-platform",
                        "backup",
                        "--home",
                        "/app/data/workspace",
                        "--output",
                        "/app/data/owned-backup",
                    ]
                )
            )
            restored = json.loads(
                command(
                    [
                        "exec",
                        "-T",
                        "api",
                        "domain-platform",
                        "restore",
                        "--backup",
                        "/app/data/owned-backup",
                        "--home",
                        "/app/data/owned-restored",
                        "--manifest-sha256",
                        backup["manifest_sha256"],
                    ]
                )
            )
            checks.append("linux_cli_consistent_backup_and_new_home_restore")
            if gpu:
                # Stop both original-home GPU services before moving to a new lease namespace.
                command(["stop", "api", "worker"])
                definition["services"]["api"]["environment"] = {
                    **definition["services"]["api"]["environment"],
                    "DMT_HOME": "/app/data/owned-restored",
                }
                compose.write_text(json.dumps(definition, indent=2), encoding="utf-8")
                command(
                    [
                        "up",
                        "--no-build",
                        "--detach",
                        "--no-deps",
                        "--force-recreate",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        "api",
                    ]
                )
                ready(client, "/ready")
                cold = request(
                    "POST",
                    prefix + "/decisions",
                    role="inference",
                    json={"state": state, "threshold": 0.8},
                ).json()
                checks.append("authenticated_http_new_home_cold_vega_restore")
            else:
                cold_probe = "import json,sys;from decision_workbench.platform.store import Store;from decision_workbench.platform.service import Service;s=Service(Store('/app/data/owned-restored'));print(json.dumps(s.predict({'tenant':'A','actor':'inference','role':'inference'},sys.argv[1],json.loads(sys.argv[2]),.8)))"
                cold = json.loads(
                    command(
                        [
                            "exec",
                            "-T",
                            "api",
                            "python",
                            "-c",
                            cold_probe,
                            created["id"],
                            json.dumps(state),
                        ]
                    )
                )
            assert cold == before, (cold, before)
            checks.append("cold_restored_workspace_prediction_equivalence")
            record["backup_manifest_sha256"] = backup["manifest_sha256"]
            record["counts"] = {
                "positive_fixture_rows": len(positive),
                "negative_fixture_rows": len(negatives),
                "answer_types": 3,
            }
            record["passed"] = True
    except BaseException as exc:
        record.update(
            passed=False, error_type=type(exc).__name__, error=str(exc)[:2000]
        )
        raise
    finally:
        try:
            identifiers = command(["ps", "--all", "--quiet"]).splitlines()
            for identifier in identifiers:
                label = docker(
                    [
                        "inspect",
                        "--format",
                        '{{ index .Config.Labels "com.docker.compose.project" }}',
                        identifier,
                    ]
                )
                assert (
                    label == project
                ), "Refuse cleanup of a container outside the owned Compose project"
            command(["down", "--volumes", "--remove-orphans"])
            record["owned_resources_removed"] = True
        except Exception as cleanup:
            record["owned_resources_removed"] = False
            record["passed"] = False
            record["cleanup_error_type"] = type(cleanup).__name__
        record["seconds"] = time.monotonic() - start
        record["commands"] = commands
        (work / "receipt.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    key: record.get(key)
                    for key in (
                        "passed",
                        "seconds",
                        "owned_resources_removed",
                        "checks",
                        "error_type",
                    )
                }
            )
        )

        if not record["owned_resources_removed"]:
            raise RuntimeError(
                "Owned-resource cleanup failed; inspect the retained receipt and private log"
            )


if __name__ == "__main__":
    main()
