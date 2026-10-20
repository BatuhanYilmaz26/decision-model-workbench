"""Bounded resident model broker; GPU packages stay outside the web API process."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

import atexit
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from decision_workbench.data import dumps
from decision_workbench.training.resources import ModelResourceLease, ResourceBusy
from decision_workbench.training.process_guard import leased_process_options


class RuntimeUnavailable(RuntimeError):
    pass


class RuntimeBroker:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.process = None
        self.model_id = None
        self.messages = None
        self.last_use = 0
        self.log = None
        self.lease = None
        self.stop = threading.Event()
        self.monitor = None
        self.reader = None
        self.capacity = threading.BoundedSemaphore(4)
        self.idle_seconds = float(os.environ.get("DMT_INFERENCE_IDLE_SECONDS", "30"))
        self.timeout = float(os.environ.get("DMT_INFERENCE_TIMEOUT_SECONDS", "120"))
        self.load_timeout = float(
            os.environ.get("DMT_MODEL_LOAD_TIMEOUT_SECONDS", "240")
        )
        if any(
            not math.isfinite(v) or v <= 0
            for v in (self.idle_seconds, self.timeout, self.load_timeout)
        ):
            raise ValueError("Runtime timeouts must be finite and positive")

    def _watch(self):
        while not self.stop.wait(1):
            try:
                pending = self.store.has_model_training()
                with self.lock:
                    if self.process is not None and (
                        pending or time.monotonic() - self.last_use > self.idle_seconds
                    ):
                        self._close()
            except Exception:
                with self.lock:
                    self._close()

    def _read(self, process, messages):
        try:
            for line in iter(lambda: process.stdout.readline(1024 * 1024 + 1), ""):
                if len(line) > 1024 * 1024:
                    messages.put_nowait({"transport_error": True})
                    return
                if line.startswith("@@DMT_RESULT "):
                    try:
                        messages.put_nowait(json.loads(line[len("@@DMT_RESULT ") :]))
                    except (ValueError, queue.Full):
                        messages.put_nowait({"transport_error": True})
                        return
        finally:
            try:
                messages.put_nowait({"transport_error": True})
            except queue.Full:
                pass

    def _reply(self, request_id, timeout):
        try:
            response = self.messages.get(timeout=timeout)
        except queue.Empty:
            self._close()
            raise RuntimeUnavailable("Model runtime timed out; it was stopped")
        if (
            not isinstance(response, dict)
            or response.get("transport_error")
            or response.get("id") != request_id
        ):
            self._close()
            raise RuntimeUnavailable(
                "Model runtime stopped or returned an invalid response"
            )
        if not response.get("ok"):
            if response.get("validation_error"):
                raise ValueError(response["validation_error"])
            self._close()
            raise RuntimeUnavailable(response.get("error", "Model runtime unavailable"))
        return response

    def _close(self):
        process = self.process
        self.process = None
        self.model_id = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if self.lease:
                # Windows descendants may retain pipes after the direct child
                # exits. End the owned job before waiting/closing those pipes.
                self.lease.stop_children()
            if self.reader and self.reader is not threading.current_thread():
                self.reader.join(timeout=2)
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
        if self.log:
            self.log.close()
            self.log = None
        if self.lease:
            self.lease.release()
            self.lease = None

    def close(self):
        self.stop.set()
        with self.lock:
            self._close()
        if self.monitor and self.monitor is not threading.current_thread():
            self.monitor.join(timeout=3)
        atexit.unregister(self.close)

    def _ensure(self, model_id, model, questions):
        if self.stop.is_set():
            raise RuntimeUnavailable("Model broker is shut down")
        if self.store.has_model_training():
            self._close()
            raise RuntimeUnavailable(
                "GPU training is queued or running; head inference resumes when it finishes"
            )
        if self.process and self.process.poll() is None and self.model_id == model_id:
            self.last_use = time.monotonic()
            return
        self._close()
        folder = (self.store.folder / "artifacts" / model["artifact_id"]).resolve()
        if folder.parent != (self.store.folder / "artifacts").resolve():
            raise ValueError("Invalid artifact reference")
        lease = ModelResourceLease(self.store.folder)
        try:
            lease.acquire(timeout=2)
        except ResourceBusy as exc:
            raise RuntimeUnavailable(str(exc)) from exc
        self.lease = lease
        try:
            logs = self.store.folder / "logs"
            logs.mkdir(exist_ok=True)
            schema = logs / ("schema-" + model_id + ".json")
            schema.write_text(dumps(questions), encoding="utf-8")
            self.log = (logs / "inference.log").open("a", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                PYTHONIOENCODING="utf-8",
                TOKENIZERS_PARALLELISM="false",
            )
            python = (
                environment.get("DMT_MODEL_PYTHON")
                or environment.get("DMT_WORKER_PYTHON")
                or sys.executable
            )
            runtime_cwd = None
            if model["backend"] in (
                "liquid-trl",
                "liquid-unsloth",
                "liquid-omni",
                "perplexity-native",
                *D3_BACKENDS,
            ):
                from .model_environment import native_environment, model_python

                environment, runtime_cwd, _ = native_environment(
                    self.store.folder, environment, backend=model["backend"]
                )
                python = model_python(model["backend"], "serving", environment)
            argv = [
                python,
                "-m",
                "decision_workbench.training.runtime",
                "--folder",
                str(folder),
                "--backend",
                model["backend"],
                "--manifest-sha",
                model["model_sha256"],
                "--schema-file",
                str(schema),
            ]
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                text=True,
                encoding="utf-8",
                bufsize=1,
                shell=False,
                **({"cwd": runtime_cwd} if runtime_cwd is not None else {}),
                **leased_process_options(self.lease, environment),
            )
            self.messages = queue.Queue(maxsize=4)
            self.reader = threading.Thread(
                target=self._read, args=(self.process, self.messages), daemon=True
            )
            self.reader.start()
            self._reply("ready", self.load_timeout)
            self.model_id = model_id
            self.last_use = time.monotonic()
            if self.monitor is None:
                self.monitor = threading.Thread(target=self._watch, daemon=True)
                self.monitor.start()
                atexit.register(self.close)
        except BaseException:
            self._close()
            raise

    def preload(self, model_id, model, questions):
        with self.lock:
            self._ensure(model_id, model, questions)

    def predict(self, model_id, model, questions, state, threshold):
        if not self.capacity.acquire(timeout=2):
            raise RuntimeUnavailable("Inference queue is full; retry later")
        try:
            with self.lock:
                self._ensure(model_id, model, questions)
                request_id = uuid.uuid4().hex
                try:
                    self.process.stdin.write(
                        dumps(
                            {"id": request_id, "state": state, "threshold": threshold}
                        )
                        + "\n"
                    )
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    self._close()
                    raise RuntimeUnavailable("Model runtime connection was lost")
                result = self._reply(request_id, self.timeout)
                self.last_use = time.monotonic()
                return result["answers"]
        finally:
            self.capacity.release()
