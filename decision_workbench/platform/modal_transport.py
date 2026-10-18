"""Private-pipe Modal SDK operations with empty config and an OS process deadline."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from .cloud_aws import CloudUnavailable
from .cloud_modal import (
    SDK_VERSION,
    object_key,
    name,
    plan,
    tags,
    validate_profile,
    validate_result,
    valid_job_id,
)
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.transfer_limits import (
    MAX_ARTIFACT_BYTES,
    transfer_seconds,
)


def resources(modal, profile, client):
    workspace = modal.Workspace.from_context(client=client)
    workspace.hydrate()
    app = modal.App.lookup(
        profile["app_name"],
        environment_name=profile["environment"],
        create_if_missing=False,
        client=client,
    )
    volume = modal.Volume.from_name(
        profile["volume_name"],
        environment_name=profile["environment"],
        create_if_missing=False,
    )
    volume.hydrate(client=client)
    if (
        workspace.name != profile["account_id"]
        or app.app_id != profile["app_id"]
        or volume.object_id != profile["volume_id"]
    ):
        raise CloudUnavailable("Modal approved resources differ")
    return app, volume


def entry(modal, volume, path, bound):
    try:
        entries = []
        for item in volume.iterdir(path, recursive=False):
            entries.append(item)
            if len(entries) > 1:
                raise CloudUnavailable(
                    "Modal exact file lookup returned multiple entries"
                )
    except (modal.exception.NotFoundError, FileNotFoundError):
        return None
    if not entries:
        return None
    item = entries[0]
    if (
        item.path.lstrip("/") != path.lstrip("/")
        or item.type != modal.types.FileEntryType.FILE
        or type(item.size) is not int
        or not 1 <= item.size <= bound
    ):
        raise CloudUnavailable("Modal file metadata differs or exceeds its bound")
    return {"path": path, "size": item.size, "mtime": item.mtime}


def copy_file(modal, volume, path, bound, stream):
    before = entry(modal, volume, path, bound)
    if before is None:
        raise CloudUnavailable("Modal expected file is missing")
    total, hasher = 0, hashlib.sha256()
    for chunk in volume.read_file(path):
        if not isinstance(chunk, bytes):
            raise CloudUnavailable("Modal file stream is invalid")
        total += len(chunk)
        if total > before["size"] or total > bound:
            raise CloudUnavailable("Modal stream exceeds its file bound")
        hasher.update(chunk)
        stream.write(chunk)
    if total != before["size"] or entry(modal, volume, path, bound) != before:
        raise CloudUnavailable("Modal file changed during transfer")
    return {"sha256": hasher.hexdigest(), "bytes": total}


def marker(modal, volume, run):
    import io

    path = object_key(run, "result")
    if entry(modal, volume, path, 16384) is None:
        return None
    data = io.BytesIO()
    copy_file(modal, volume, path, 16384, data)
    return validate_result(run, json.loads(data.getvalue()))


def sandbox(modal, app, run, client):
    identifier = run.get("provider_job_id")
    if identifier is None:
        # This only resolves running Sandboxes. Missing after an uncertain create is
        # UNKNOWN, never permission to create another job or release capacity.
        sb = modal.Sandbox.from_name(
            run["profile"]["app_name"],
            name(run),
            environment_name=run["profile"]["environment"],
            client=client,
        )
    else:
        if not valid_job_id(run, identifier):
            raise CloudUnavailable("Invalid Modal Sandbox identity")
        sb = modal.Sandbox.from_id(identifier, client=client)
    observed = sb.get_tags()
    if (
        observed != tags(run)
        or not valid_job_id(run, sb.object_id)
        or identifier is not None
        and identifier != sb.object_id
    ):
        raise CloudUnavailable("Modal Sandbox writer binding differs")
    return sb, {"provider_job_id": sb.object_id, "tags": observed}


def execute(modal, profile, credentials, op, body):
    validate_profile(profile)
    client = modal.Client.from_credentials(*credentials)
    app, volume = resources(modal, profile, client)
    if op == "identity":
        return {k: profile[k] for k in ("account_id", "app_id", "volume_id")}
    run = body["run"]
    if run["profile"] != {
        k: v for k, v in profile.items() if k not in ("credential_prefix", "tenants")
    }:
        raise CloudUnavailable("Modal frozen profile differs")
    if op == "upload":
        archive = Path(body["archive"])
        if (
            not archive.is_file()
            or archive.is_symlink()
            or not 1 <= archive.stat().st_size <= 256 * 1024**2
            or file_sha(archive) != run["archive_sha256"]
        ):
            raise CloudUnavailable("Modal input archive differs")
        for kind in ("input", "output", "result"):
            if (
                entry(modal, volume, object_key(run, kind), MAX_ARTIFACT_BYTES)
                is not None
            ):
                raise CloudUnavailable("Modal run path already contains input/output")
        with volume.batch_upload(force=False) as batch:
            batch.put_file(str(archive), object_key(run, "input"))
        with open(os.devnull, "wb") as stream:
            result = copy_file(
                modal, volume, object_key(run, "input"), 256 * 1024**2, stream
            )
        if result != {"sha256": run["archive_sha256"], "bytes": archive.stat().st_size}:
            raise CloudUnavailable("Modal uploaded input changed")
        return {**result, "volume_id": profile["volume_id"]}
    if op == "create":
        p = plan(run)
        try:
            modal.Sandbox.from_name(
                profile["app_name"],
                p["name"],
                environment_name=profile["environment"],
                client=client,
            )
        except modal.exception.NotFoundError:
            pass
        else:
            raise CloudUnavailable("Modal run name already exists")
        image = modal.Image.from_id(profile["modal_image_id"], client=client)
        sb = modal.Sandbox.create(
            *p["command"],
            app=app,
            client=client,
            name=p["name"],
            tags=tags(run),
            image=image,
            env=p["env"],
            gpu=p["gpu"],
            cpu=tuple(p["cpu"]),
            memory=tuple(p["memory"]),
            timeout=p["timeout"],
            region=p["region"],
            runtime=p["runtime"],
            block_network=True,
            volumes={p["mount"]: volume},
            secrets=[],
            encrypted_ports=[],
            h2_ports=[],
            unencrypted_ports=[],
            include_oidc_identity_token=False,
        )
        if sb.get_tags() != tags(run):
            raise CloudUnavailable("Modal creation binding differs")
        return {"provider_job_id": sb.object_id, "tags": tags(run)}
    sb, result = sandbox(modal, app, run, client)
    if op == "observe":
        return {
            **result,
            "exit_code": sb.poll(),
            "training_result": marker(modal, volume, run),
        }
    if op == "stop":
        sb.terminate(wait=False)
        return {**result, "termination_acknowledged": True}
    if op == "download":
        approved = body["marker"]
        validate_result(run, approved)
        if sb.poll() != 0 or marker(modal, volume, run) != approved:
            raise CloudUnavailable("Modal download requires a matching exited export")
        target = Path(body["target"])
        created = False
        try:
            with target.open("xb") as stream:
                created = True
                result = copy_file(
                    modal,
                    volume,
                    object_key(run, "output"),
                    approved["artifact_bytes"],
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            if (
                result
                != {
                    "sha256": approved["artifact_sha256"],
                    "bytes": approved["artifact_bytes"],
                }
                or marker(modal, volume, run) != approved
            ):
                raise CloudUnavailable("Modal export changed during retrieval")
            return result
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    raise ValueError("Unknown Modal transport operation")


def operation_seconds(profile, op, body):
    if op == "download" and isinstance(body, dict):
        marker = body.get("marker")
        size = marker.get("artifact_bytes") if isinstance(marker, dict) else None
        if type(size) is int and 1 <= size <= profile["max_artifact_bytes"]:
            return transfer_seconds(size)
    return 300 if op in ("upload", "download") else 90


def run_process(
    profile, credentials, op, body, command=None, timeout=None, observe=None
):
    if op not in ("identity", "upload", "create", "observe", "stop", "download"):
        raise ValueError("Invalid Modal operation")
    seconds = timeout if timeout is not None else operation_seconds(profile, op, body)
    start, process = time.monotonic(), None
    try:
        with tempfile.TemporaryDirectory(prefix="dmt-modal-config-") as tmp:
            config = Path(tmp) / "empty.toml"
            config.write_text("", encoding="utf-8")
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}
            }
            env.update(
                MODAL_CONFIG_PATH=str(config),
                MODAL_SERVER_URL="https://api.modal.com",
                MODAL_SANDBOX_V2="false",
                MODAL_ENVIRONMENT=profile["environment"],
            )
            process = subprocess.Popen(
                command
                or [
                    sys.executable,
                    "-m",
                    "decision_workbench.platform.modal_transport",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                shell=False,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if observe:
                observe(process)
            payload = json.dumps(
                {"profile": profile, "credentials": credentials, "op": op, "body": body}
            ).encode("utf-8")
            if len(payload) > 262144:
                raise ValueError("Modal IPC input exceeds its bound")
            output, _ = process.communicate(
                payload, timeout=max(0.001, seconds - (time.monotonic() - start))
            )
            if (
                process.returncode
                or time.monotonic() - start > seconds
                or len(output) > 65536
            ):
                raise CloudUnavailable("Modal operation has an unknown outcome")
            value = json.loads(output)
            if set(value) != {"result"}:
                raise CloudUnavailable("Modal operation has an unknown outcome")
            return value["result"]
    except Exception:
        raise CloudUnavailable(
            "Modal operation failed or timed out; observe the original run"
        ) from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def child_main():
    envelope = {"failed": True}
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            raise ValueError()
        data = json.loads(raw)
        # Import only after the parent has selected an empty config/fixed endpoint.
        from importlib.metadata import version

        if version("modal") != SDK_VERSION:
            raise ValueError("Unqualified Modal SDK version")
        with (
            open(os.devnull, "w") as quiet,
            contextlib.redirect_stdout(quiet),
            contextlib.redirect_stderr(quiet),
        ):
            import modal
            from modal import types  # Public FileEntryType namespace.

            modal.types = types
            result = execute(
                modal, data["profile"], data["credentials"], data["op"], data["body"]
            )
        envelope = {"result": result}
    except Exception:
        pass  # Never echo credentials, SDK traces, data or transient signed URLs.
    sys.stdout.buffer.write(json.dumps(envelope).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    child_main()
