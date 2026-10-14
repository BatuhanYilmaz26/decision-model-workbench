"""One-shot HTTP transport isolated from the API/worker and killed on deadline."""

import base64
import json
import os
import subprocess
import sys
import time
import uuid
import httpx


class TransportFailure(RuntimeError):
    def __init__(self, terminated=True):
        super().__init__("Decision transport failed; reconcile the unknown outcome")
        self.terminated = terminated


def endpoint(profile):
    return {
        "typesafe": "https://api.typesafe.ai/v1/systemone",
        "typellm": "https://api.typellm.ai/v1/generate",
        "openrouter": "https://openrouter.ai/api/alpha/decisions",
        "perplexity": "https://api.perplexity.ai/v1/decisions",
        "cloudflare": "https://api.cloudflare.com/client/v4/accounts/"
        + profile.get("account_id", "")
        + "/ai/run/@cf/cloudflare/"
        + profile["model"],
    }[profile["provider"]]


def receive(profile, body, key, transport=None):
    started = time.monotonic()
    with httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(2.0, connect=3.0, pool=2.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with client.stream(
            "POST",
            endpoint(profile),
            content=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
        ) as response:
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise TransportFailure()
            data = bytearray()
            for chunk in response.iter_raw(chunk_size=65536):
                data.extend(chunk)
                if (
                    len(data) > 1048576
                    or time.monotonic() - started > profile["timeout_seconds"]
                ):
                    raise TransportFailure()
    if time.monotonic() - started > profile["timeout_seconds"]:
        raise TransportFailure()
    return bytes(data)


def child_main(transport=None):
    # Only the selected credential/evidence crosses a private pipe. Nothing is logged.
    try:
        data = sys.stdin.buffer.read(524289)
        if len(data) > 524288:
            raise ValueError()
        request = json.loads(data)
        body = base64.b64decode(request["body"], validate=True)
        if len(body) > 64000:
            raise ValueError()
        result = receive(request["profile"], body, request["key"], transport)
        message = {"data": base64.b64encode(result).decode("ascii")}
    except Exception:
        message = {"failed": True}
    sys.stdout.buffer.write(json.dumps(message).encode("ascii"))
    sys.stdout.buffer.flush()


def run_process(profile, body, key, command=None, observe=None, record_started=None):
    """Deadline covers process startup/communication; creation itself is an OS limit.

    command/observe are internal test seams. There is no environment/API URL override.
    """
    started = time.monotonic()
    deadline = started + profile["timeout_seconds"]
    process = None
    token = uuid.uuid4().hex
    terminated = True
    try:
        private_env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}
        }
        process = subprocess.Popen(
            (
                command
                or [
                    sys.executable,
                    "-m",
                    "decision_workbench.platform.hosted_transport",
                ]
            )
            + [token],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=private_env,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        terminated = False
        if record_started:
            record_started(process, token)
        if observe:
            observe(process)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired([], 0)
        payload = json.dumps(
            {
                "profile": profile,
                "body": base64.b64encode(body).decode("ascii"),
                "key": key,
            }
        ).encode("utf-8")
        output, _ = process.communicate(payload, timeout=remaining)
        terminated = process.poll() is not None
        if (
            process.returncode != 0
            or len(output) > 1400000
            or time.monotonic() > deadline
        ):
            raise TransportFailure(terminated)
        envelope = json.loads(output)
        if not isinstance(envelope, dict) or set(envelope) != {"data"}:
            raise TransportFailure(terminated)
        received = base64.b64decode(envelope["data"], validate=True)
        if len(received) > 1048576:
            raise TransportFailure(terminated)
        return received
    except Exception:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        terminated = process is None or process.poll() is not None
        raise TransportFailure(terminated) from None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


if __name__ == "__main__":
    child_main()
