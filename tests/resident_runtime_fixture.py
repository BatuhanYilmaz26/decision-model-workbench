"""Weight-free resident framing and broker-parent ownership fixture."""

from pathlib import Path
import json
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


if sys.argv[1] == "serve":
    from decision_workbench.training.process_guard import arm_parent_guard

    if not arm_parent_guard():
        raise RuntimeError("Expected supervised runtime ownership")
    print('@@DMT_RESULT {"id":"ready","ok":true}', flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        print(
            "@@DMT_RESULT "
            + json.dumps(
                {
                    "id": request["id"],
                    "ok": True,
                    "answers": {
                        "decision": {
                            "answer": "accept",
                            "confidence": 0.8,
                            "probabilities": {"accept": 0.8, "review": 0.2},
                            "abstained": False,
                        }
                    },
                }
            ),
            flush=True,
        )
else:
    from decision_workbench.platform.runtime_broker import RuntimeBroker
    from decision_workbench.platform.store import Store

    broker = RuntimeBroker(Store(sys.argv[2]))
    real_popen = subprocess.Popen
    errors = []

    def spawn(argv, **kwargs):
        return real_popen([sys.executable, __file__, "serve"], **kwargs)

    def request():
        try:
            broker.preload(
                "a" * 32,
                {
                    "backend": "laya-native",
                    "artifact_id": "b" * 32,
                    "model_sha256": "c" * 64,
                },
                {},
            )
        except BaseException as exc:
            errors.append(str(exc))

    try:
        with patch(
            "decision_workbench.platform.runtime_broker.subprocess.Popen",
            side_effect=spawn,
        ):
            caller = threading.Thread(target=request)
            caller.start()
            caller.join(timeout=10)
            time.sleep(0.2)
            if caller.is_alive() or errors or broker.process.poll() is not None:
                raise RuntimeError(
                    "Resident runtime did not survive its request thread"
                )
            Path(sys.argv[3]).write_text(str(broker.process.pid), encoding="utf-8")
            while True:
                time.sleep(0.1)
    finally:
        broker.close()
