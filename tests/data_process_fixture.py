"""Test-only data worker/transport processes for parent-crash ownership checks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
mode = sys.argv[1]
if mode == "taxonomy-child":
    import httpx
    from decision_workbench.platform.hosted_transport import child_main

    def respond(request):
        assert str(request.url) == "https://api.typellm.ai/v1/generate"
        Path(sys.argv[2]).write_text(json.dumps({"pid": os.getpid()}))
        time.sleep(120)
        return httpx.Response(200, json={})

    child_main(httpx.MockTransport(respond))
else:
    from decision_workbench.platform.worker import Worker
    from decision_workbench.platform.store import Store

    if mode == "semantic-parent":
        original = subprocess.Popen

        def launch(argv, **kwargs):
            return original(
                [
                    sys.executable,
                    str(ROOT / "tests/semantic_fixture_runner.py"),
                    *argv[3:],
                ],
                **kwargs,
            )

        with patch("decision_workbench.platform.semantic.subprocess.Popen", launch):
            Worker(Store(sys.argv[2])).once()
    elif mode == "taxonomy-parent":
        from decision_workbench.platform.hosted_transport import run_process

        def transport(profile, body, key, **kwargs):
            return run_process(
                profile,
                body,
                key,
                command=[sys.executable, __file__, "taxonomy-child", sys.argv[3]],
                **kwargs,
            )

        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process", transport
        ):
            Worker(Store(sys.argv[2])).once()
    else:
        raise ValueError("Unknown test process mode")
