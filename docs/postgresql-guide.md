# Run workspace metadata on PostgreSQL

[All guides](README.md) · [Local setup](setup.md) · [Project overview](../README.md)

Use the experimental PostgreSQL backend for projects, reviewed datasets,
reservations, job ownership, releases and audit events. Personal localhost mode
continues to use SQLite.

**With metadata-only configuration, keep API and worker on one host with the same
workspace directory.** The optional [shared-artifact setup](shared-artifacts-guide.md)
publishes model exports to S3 and supports separate node caches. PostgreSQL alone
does not move job/staging files, provide interactive enterprise login or supply
coordinated database/object recovery.

## Prepare the database

Install the [CPU application](../README.md#run-on-your-computer), then add the
optional Psycopg 3.3.6 driver in that environment:

```bash
python -m pip install -r requirements/postgres.txt
python -m pip check
```

Use a supported PostgreSQL server; automated contracts use PostgreSQL 18.6. Ask
your administrator to create a dedicated database and application role. After
creating the `workbench_app` role, create its schema as the database administrator:

```sql
CREATE SCHEMA decision_workbench AUTHORIZATION workbench_app;
```

The application role needs usage/create privileges and access to its tables and
sequences. Prevent untrusted roles from creating objects in this schema. Use a
separate schema for each workspace. Schema names must start with a lowercase
letter and contain only lowercase letters, digits and underscores. Public/system
schemas and missing schemas are refused. Existing SQLite workspaces are not
automatically migrated. [PostgreSQL schemas](https://www.postgresql.org/docs/18/ddl-schemas.html).

Stop API and worker processes before upgrading a PostgreSQL workspace. The current
metadata protocol is 12; an older service version must not continue writing
during an upgrade. Downgrading against newer metadata is refused. The personal
SQLite protocol is unchanged by the optional object-storage route.

## Start the services

Provision a workspace key and save the bearer token displayed once:

```bash
domain-platform provision-key --file data/postgres-keys.json --tenant team --actor owner --role admin
```

Set these variables in **both the API and worker terminals**. Supply your own
connection URI and required TLS settings; percent-encode reserved characters in
URI credentials. Remote servers should use certificate verification, such as
`sslmode=verify-full` with the correct root CA.

```bash
export DMT_DATABASE_URL='postgresql://workbench_app:YOUR_ENCODED_PASSWORD@YOUR_DB_HOST/workbench?sslmode=verify-full'
export DMT_KEYS_FILE='data/postgres-keys.json'
```

In PowerShell use `$env:DMT_DATABASE_URL = 'YOUR_CONNECTION_URI'` and
`$env:DMT_KEYS_FILE = 'data/postgres-keys.json'`. Keep the connection string
server-side and outside Git. Never paste it into the browser.
[Connection URI and TLS parameters](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-CONNSTRING).

Start the API:

```bash
python examples/platform/postgres_workspace.py api --home data/postgres-workspace
```

Start the worker in the second configured terminal:

```bash
python examples/platform/postgres_workspace.py worker --home data/postgres-workspace
```

In a third terminal run `python -m streamlit run app.py`. Choose **Shared
workspace**, expand **Connect to an existing workspace**, enter
`http://127.0.0.1:8080` and your provisioned token, then
follow the [application guide](app-guide.md). Both services must use the same
`--home` and `--schema` settings. Stop with Ctrl+C and restart with the same
database, schema and directory.

This example explicitly selects PostgreSQL. Setting `DMT_DATABASE_URL` alone
does not redirect the ordinary personal launcher or migrate its SQLite data.
Connection failures do not silently fall back to SQLite.

Start with a new artifact directory. The backend refuses a directory containing
a SQLite database and records a secret-free binding to the PostgreSQL workspace
identity. Reopening it with another database/schema or as SQLite is refused.
Keep that binding with the directory; it is not a database backup or migration.

## Transaction and recovery limits

Short metadata transactions commit or roll back together. A database/schema
transaction advisory lock preserves quotas, compare-and-swap updates and job
fencing across connections/processes. Locks end on transaction completion or
connection death. Operations currently serialize per workspace; this is not a
high-throughput pooled backend. Model/provider work belongs outside these
transactions. [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PostgreSQL locks](https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS).

The bounded SQL adapter preserves numeric revision ordering, timestamp precision
and typed metadata fields. It is not a general SQLite SQL translator. Queries
keep values separate from SQL. [Parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html),
[JSON functions](https://www.postgresql.org/docs/18/functions-json.html).

Local SQLite backup commands **do not capture PostgreSQL**. Arrange database
backups and retain the corresponding workspace artifacts. A local backup does
not establish recoverability of this backend. Coordinated database/artifact
recovery is required before multi-host deployment.

## Verify a disposable database

Set `DMT_TEST_POSTGRES_URL` to a dedicated test database whose role can create/drop
schemas, then run:

```bash
python -m unittest discover -s tests -p test_postgres.py -v
```

Each case creates/removes its own random `dmt_test_...` schema. Checks cover
rollback, constraints, independent process claims, stale-worker fencing,
numerical JSON ordering and actual API/worker/CPU release workflows. Database
cases skip when the variable is absent; SQL-adapter checks still run. The
[CI job](../.github/CI.md) supplies a disposable server. These checks do not
establish production scale, cloud availability or domain model quality.

Use [shared-object retention](shared-object-retention-guide.md) to preview and reconcile deletion of old unreferenced S3 versions. Keep local staging, project deletion and coordinated backup recovery under their separate policies.
