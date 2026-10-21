"""Start a PostgreSQL metadata workspace; artifacts remain in shared --home.

Set DMT_DATABASE_URL and DMT_KEYS_FILE in the operator environment. Keep API and
worker on the same host until shared artifact storage is configured.
"""

import argparse
import os
import time

from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker"))
    parser.add_argument("--home", default="data/postgres-workspace")
    parser.add_argument("--schema", default="decision_workbench")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    url = os.environ.get("DMT_DATABASE_URL")
    if not url:
        parser.error("Set DMT_DATABASE_URL in the operator environment")
    if args.role == "api":
        import uvicorn

        app = create_app(args.home, database_url=url, database_schema=args.schema)
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return
    worker = Worker(Store(args.home, database_url=url, database_schema=args.schema))
    try:
        with worker.presence():
            while True:
                if not worker.once():
                    time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        worker.service.runtime.close()


if __name__ == "__main__":
    main()
