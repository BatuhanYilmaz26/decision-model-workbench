"""Owned local service entrypoint with desktop-process supervision before imports."""

import os
import sys
import threading
import time


def main():
    parent = int(os.environ.pop("DMT_LOCAL_PARENT_PID"))
    if sys.platform == "win32":
        from decision_workbench.training.process_guard import arm_parent_guard

        if not arm_parent_guard():
            raise RuntimeError("Personal service requires Windows process ownership")
    else:
        # The Streamlit script thread ends on each rerun. Track the server process,
        # not that thread, so a rerun cannot kill durable background work.

        def watch():
            while True:
                if os.getppid() != parent:
                    os._exit(1)
                time.sleep(0.25)

        threading.Thread(target=watch, daemon=True).start()
    kind = sys.argv.pop(1)
    if kind == "api":
        from .cli import main as serve

        sys.argv.insert(1, "serve")
        serve()
    elif kind == "worker":
        from .worker import main as worker

        worker()
    elif kind == "web":
        import runpy

        sys.argv[0] = "streamlit"
        runpy.run_module("streamlit", run_name="__main__")
    else:
        raise ValueError("Unknown local service")


if __name__ == "__main__":
    main()
