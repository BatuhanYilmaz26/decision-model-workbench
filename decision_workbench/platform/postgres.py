"""Optional PostgreSQL metadata storage for the existing workspace contracts.

Install the ``postgres`` extra. This adapter preserves the workspace's short,
serialized transactions across processes. It does not move model artifacts or
turn a local filesystem into shared storage; see docs/shared-artifacts-guide.md#postgresql-metadata.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid


_JSON = re.compile(
    r"json_extract\(\s*((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*,\s*"
    r"'\$\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'\s*\)",
    re.IGNORECASE,
)
_NUMERIC_PATHS = {
    "revision": "BIGINT",
    "selector.count": "BIGINT",
    "reported_cost_usd": "DOUBLE PRECISION",
}
_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def postgres_sql(statement):
    """Adapt the workspace's bounded SQL dialect, keeping values parameterized.

    Only metadata scalar paths are used by workspace queries. Revisions/counts
    are integers and reported cost is a float; other queried paths are text.
    This is not a general SQLite migration or arbitrary JSON query translator.
    Quoted text, identifiers and comments are preserved, including question marks.
    """
    if not isinstance(statement, str):
        raise TypeError("Workspace SQL must be text")
    table = re.fullmatch(r"\s*PRAGMA table_info\((\w+)\)\s*;?\s*", statement, re.I)
    if table:
        statement = (
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='"
            + table[1]
            + "' ORDER BY ordinal_position"
        )
    elif re.match(r"\s*PRAGMA\b", statement, re.I):
        raise ValueError("SQLite maintenance SQL cannot run on PostgreSQL")
    ignore = re.match(r"\s*INSERT OR IGNORE INTO (\w+)\b", statement, re.I)
    if ignore:
        if ignore[1].lower() not in {"record_contents", "schema_version"}:
            raise ValueError("Unsupported conflict-ignore statement")
        statement = re.sub(
            r"INSERT OR IGNORE", "INSERT", statement, count=1, flags=re.I
        )
        statement = statement.rstrip().removesuffix(";") + " ON CONFLICT DO NOTHING"
    if re.match(r"\s*CREATE TABLE\b", statement, re.I):
        statement = re.sub(r"\bREAL\b", "DOUBLE PRECISION", statement, flags=re.I)
        if re.match(
            r"\s*CREATE TABLE IF NOT EXISTS (events|incident_history)\b",
            statement,
            re.I,
        ):
            statement = statement.replace(
                "id INTEGER PRIMARY KEY",
                "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
            )
    result, position = [], 0
    while position < len(statement):
        char = statement[position]
        if statement.startswith("--", position):
            end = statement.find("\n", position)
            end = len(statement) if end < 0 else end
        elif statement.startswith("/*", position):
            end, depth = position + 2, 1
            while depth and end < len(statement):
                if statement.startswith("/*", end):
                    depth += 1
                    end += 2
                elif statement.startswith("*/", end):
                    depth -= 1
                    end += 2
                else:
                    end += 1
            if depth:
                raise ValueError("Unclosed SQL comment")
        elif char in ("'", '"'):
            end = position + 1
            while end < len(statement):
                if statement[end] == char:
                    end += 1
                    if end < len(statement) and statement[end] == char:
                        end += 1
                    else:
                        break
                else:
                    end += 1
            else:
                raise ValueError("Unclosed SQL quote")
        elif char == "$" and (
            tag := re.match(r"\$(?:[A-Za-z_]\w*)?\$", statement[position:])
        ):
            closing = statement.find(tag[0], position + len(tag[0]))
            if closing < 0:
                raise ValueError("Unclosed SQL dollar quote")
            end = closing + len(tag[0])
        else:
            match = _JSON.match(statement, position)
            if match:
                column, path = match.groups()
                arguments = ",".join("'" + part + "'" for part in path.split("."))
                expression = (
                    f"pg_catalog.jsonb_extract_path_text({column}::jsonb,{arguments})"
                )
                if path in _NUMERIC_PATHS:
                    expression = f"CAST({expression} AS {_NUMERIC_PATHS[path]})"
                result.append("(" + expression + ")")
                position = match.end()
                continue
            result.append("%s" if char == "?" else "%%" if char == "%" else char)
            position += 1
            continue
        result.append(statement[position:end].replace("%", "%%"))
        position = end
    return "".join(result)


class MetadataRow(dict):
    """Named metadata columns with positional access for aggregate results."""

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: MetadataRow(zip(names, values))


class _Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, statement, parameters=()):
        return self.raw.execute(postgres_sql(statement), parameters)

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        cursor.executemany(postgres_sql(statement), parameters)
        return cursor


class PostgresDatabase:
    """Connection-per-transaction metadata backend with a shared write fence.

    Use a dedicated pre-provisioned schema owned by the application role. The
    transaction advisory lock is database/schema scoped and released by commit,
    rollback or process death. No network/model work belongs inside this scope.
    """

    def __init__(self, dsn, schema="decision_workbench", timeout=10):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("Supply a PostgreSQL connection string")
        if (
            not isinstance(schema, str)
            or not _SCHEMA.fullmatch(schema)
            or schema.startswith("pg_")
            or schema in {"public", "information_schema"}
        ):
            raise ValueError("Use a dedicated lowercase PostgreSQL schema")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 120
        ):
            raise ValueError("Database timeout must be between 1 and 120 seconds")
        try:
            import psycopg
        except ImportError:
            raise RuntimeError(
                'Install the PostgreSQL extra: pip install ".[platform,postgres]"'
            ) from None
        self._driver = psycopg
        self._dsn, self.schema, self.timeout = dsn, schema, timeout
        self._lock = int.from_bytes(
            hashlib.sha256(("dmt-metadata-v1:" + schema).encode()).digest()[:8],
            "big",
            signed=True,
        )

    @contextmanager
    def artifact_guard(self, exclusive=False):
        """Coordinate object publication and maintenance outside SQL transactions."""
        from .artifact_storage import ArtifactUnavailable

        key = int.from_bytes(
            hashlib.sha256(("dmt-artifacts-v1:" + self.schema).encode()).digest()[:8],
            "big",
            signed=True,
        )
        function = (
            "pg_try_advisory_lock" if exclusive else "pg_try_advisory_lock_shared"
        )
        try:
            with self._driver.connect(
                self._dsn, autocommit=True, connect_timeout=self.timeout
            ) as connection:
                connection.execute(
                    "SELECT pg_catalog.set_config('statement_timeout', %s, false)",
                    (str(self.timeout * 1000),),
                )
                deadline = time.monotonic() + self.timeout
                while not connection.execute(
                    "SELECT pg_catalog." + function + "(%s)", (key,)
                ).fetchone()[0]:
                    if time.monotonic() >= deadline:
                        raise ArtifactUnavailable(
                            "Shared artifact publication or maintenance is busy; retry after it finishes"
                        )
                    time.sleep(0.05)

                def live():
                    try:
                        connection.execute("SELECT 1").fetchone()
                    except self._driver.Error:
                        raise ArtifactUnavailable(
                            "Shared artifact ownership was lost; publication was refused"
                        ) from None

                yield live
        except self._driver.Error:
            raise ArtifactUnavailable(
                "Shared artifact coordination was interrupted; inspect the operator maintenance state"
            ) from None

    def bind_folder(self, folder):
        """Bind a local artifact cache to one database identity, without secrets."""
        from decision_workbench.training.resources import ResourceLease
        from .credentials import publish

        folder = Path(folder)
        marker = folder / ".dmt-postgres.json"
        with ResourceLease(folder / ".dmt-postgres.lock").hold(timeout=10):
            if marker.is_symlink():
                raise ValueError(
                    "PostgreSQL workspace binding must be an ordinary file"
                )
            with self.transaction() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_metadata_identity (slot INTEGER PRIMARY KEY CHECK(slot=1), id TEXT NOT NULL)"
                )
                row = db.execute(
                    "SELECT id FROM workspace_metadata_identity WHERE slot=1"
                ).fetchone()
                identity = row["id"] if row else uuid.uuid4().hex
                if not re.fullmatch(r"[0-9a-f]{32}", identity):
                    raise ValueError("Invalid PostgreSQL workspace identity")
                if not row:
                    db.execute(
                        "INSERT INTO workspace_metadata_identity VALUES (1,?)",
                        (identity,),
                    )
            expected = {
                "backend": "postgresql",
                "schema": self.schema,
                "workspace": identity,
            }
            self.workspace_id = identity
            if marker.exists():
                if (
                    marker.stat().st_size > 4096
                    or json.loads(marker.read_text(encoding="utf-8")) != expected
                ):
                    raise ValueError(
                        "Workspace artifact directory belongs to another PostgreSQL database/schema"
                    )
                return
            handle, temporary = tempfile.mkstemp(prefix=".dmt-postgres-", dir=folder)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(json.dumps(expected, sort_keys=True))
                    stream.flush()
                    os.fsync(stream.fileno())
                publish(temporary, marker)
            finally:
                Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def transaction(self):
        psycopg = self._driver
        try:
            with psycopg.connect(
                self._dsn,
                autocommit=True,
                connect_timeout=self.timeout,
                row_factory=_row_factory,
            ) as raw:
                with raw.transaction():
                    raw.execute(
                        "SELECT pg_catalog.set_config('lock_timeout', %s, true)",
                        (str(self.timeout * 1000),),
                    )
                    raw.execute(
                        "SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                        (str(self.timeout * 1000),),
                    )
                    raw.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock(%s)", (self._lock,)
                    )
                    exists = raw.execute(
                        "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s",
                        (self.schema,),
                    ).fetchone()
                    if not exists:
                        raise ValueError(
                            "Create the dedicated PostgreSQL schema before starting the workspace"
                        )
                    raw.execute(
                        psycopg.sql.SQL(
                            "SET LOCAL search_path TO {}, pg_catalog"
                        ).format(psycopg.sql.Identifier(self.schema))
                    )
                    yield _Connection(raw)
        except psycopg.IntegrityError:
            raise sqlite3.IntegrityError(
                "Metadata constraint refused the transaction"
            ) from None
        except psycopg.Error as exc:
            # Do not expose DSNs, provider secrets or constraint payloads in API errors.
            raise sqlite3.DatabaseError(
                "PostgreSQL metadata operation failed (SQLSTATE "
                + (exc.sqlstate or "connection")
                + ")"
            ) from None
