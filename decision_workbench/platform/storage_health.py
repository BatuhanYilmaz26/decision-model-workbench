"""Bounded workspace storage checks without downloading model payloads."""

import json
import time

from .artifact_storage import reference_key


def inspect(store):
    """Check binding and one registered manifest; this is not a full model audit."""
    shared = store.artifact_store is not None
    with store.transaction() as db:
        db.execute("SELECT 1").fetchone()
        if store.backend == "postgresql":
            store._artifact_binding(db)
        row = None
        if shared:
            row = db.execute(
                "SELECT e.payload FROM active_models a JOIN entities e ON e.id=a.model_id ORDER BY a.tenant,a.project LIMIT 1"
            ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT payload FROM entities WHERE kind='model' ORDER BY id LIMIT 1"
                ).fetchone()
    probe = "local-workspace"
    if shared:
        probe = "none-no-model"
        if row:
            model = json.loads(row["payload"])
            ref = model["artifact_reference"]
            key = reference_key(store.workspace_id, ref)
            store.artifact_store.objects.available(key, ref["bytes"])
            probe = "one-registered-manifest-head"
    return {
        "status": "healthy",
        "metadata": store.backend,
        "artifacts": "shared" if shared else "local",
        "probe": probe,
        "checked_at": time.time(),
    }
