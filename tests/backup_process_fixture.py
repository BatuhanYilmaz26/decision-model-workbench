"""Deterministic process barriers for backup OS-lock and crash contracts."""

from pathlib import Path
import sys
import time

from decision_workbench.platform import backup_schedule as schedule


def main():
    mode, config, key_file, ready = sys.argv[1:]
    ready = Path(ready)

    def pause():
        ready.write_text("Process reached the requested barrier", encoding="utf-8")
        while True:
            time.sleep(0.1)

    if mode == "hold":
        policy, _, _ = schedule.load(config, key_file)
        with schedule._lease(Path(policy["catalog"] + ".operation.lock")):
            pause()
    elif mode == "published":
        original = schedule._append

        def append(policy, key, state, operation, action, details=None):
            if action == "capture.succeeded":
                pause()
            return original(policy, key, state, operation, action, details)

        schedule._append = append
        schedule.run_due(config, key_file)
    elif mode == "unprepared":
        schedule.encrypted.backup_encrypted = lambda *args, **kwargs: pause()
        schedule.run_due(config, key_file)
    else:
        raise ValueError("Unknown process fixture mode")


if __name__ == "__main__":
    main()
