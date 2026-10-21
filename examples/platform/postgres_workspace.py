"""Start a PostgreSQL workspace with optional S3 model artifact storage.

Set DMT_DATABASE_URL and DMT_KEYS_FILE in the operator environment. Keep API and
worker on the same host when shared artifact storage is not configured.
"""

import argparse
import os
import time

from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker", "warm-cache"))
    parser.add_argument("--home", default="data/postgres-workspace")
    parser.add_argument("--schema", default="decision_workbench")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--tenant", help="Registry tenant for warm-cache")
    parser.add_argument("--project", help="Registry project for warm-cache")
    parser.add_argument("--model", help="Registered model ID for warm-cache")
    args = parser.parse_args()
    url = os.environ.get("DMT_DATABASE_URL")
    if not url:
        parser.error("Set DMT_DATABASE_URL in the operator environment")
    from decision_workbench.platform.artifact_s3 import repository_from_environment

    artifacts = repository_from_environment()
    if args.role == "api":
        import uvicorn

        app = create_app(
            args.home,
            database_url=url,
            database_schema=args.schema,
            artifact_store=artifacts,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return
    store = Store(
        args.home,
        database_url=url,
        database_schema=args.schema,
        artifact_store=artifacts,
    )
    if args.role == "warm-cache":
        if not all((args.tenant, args.project, args.model)):
            parser.error("warm-cache requires --tenant, --project and --model")
        model = store.get(args.model, args.tenant, args.project, "model")["payload"]
        print(store.artifact_path(model, warm=True))
        return
    worker = Worker(store)
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
