"""Local operator commands for credential provisioning and API startup."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    audit_verify = commands.add_parser(
        "verify-audit-export",
        help="Verify a complete audit JSONL window against a separately retained manifest checksum",
    )
    audit_verify.add_argument("--file", required=True)
    audit_verify.add_argument("--manifest-sha256", required=True)
    provision = commands.add_parser("provision-key")
    provision.add_argument("--file", required=True)
    provision.add_argument("--tenant", required=True)
    provision.add_argument("--actor", required=True)
    provision.add_argument(
        "--role", choices=["admin", "editor", "reviewer", "inference"], default="admin"
    )
    for action in ("rotate-key", "revoke-key", "list-keys"):
        command = commands.add_parser(action)
        command.add_argument("--file", required=True)
        if action != "list-keys":
            command.add_argument("--tenant", required=True)
            command.add_argument("--actor", required=True)
        if action == "rotate-key":
            command.add_argument(
                "--role", choices=["admin", "editor", "reviewer", "inference"]
            )
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    run = commands.add_parser(
        "run", help="Start the local API, worker and browser app together"
    )
    run.add_argument(
        "--home",
        help="Workspace directory (personal and operator modes use separate defaults)",
    )
    run.add_argument(
        "--keys-file",
        help="Operator credential file; omit for a personal local workspace",
    )
    run.add_argument("--api-port", type=int, default=8080)
    run.add_argument("--ui-port", type=int, default=8501)
    backup = commands.add_parser(
        "backup", help="Capture a consistent local workspace and registered models"
    )
    backup.add_argument("--home", default=os.environ.get("DMT_HOME", "data/workspace"))
    backup.add_argument(
        "--output", required=True, help="New directory outside the source workspace"
    )
    backup.add_argument("--timeout", type=float, default=300)
    verify = commands.add_parser(
        "verify-backup", help="Verify backup against a separately retained checksum"
    )
    verify.add_argument("--backup", required=True)
    verify.add_argument("--manifest-sha256", required=True)
    restore = commands.add_parser(
        "restore",
        help="Restore into a new local workspace without replaying pending jobs",
    )
    restore.add_argument("--backup", required=True)
    restore.add_argument(
        "--home", required=True, help="New recovered workspace directory"
    )
    restore.add_argument("--manifest-sha256", required=True)
    encrypted_key = commands.add_parser(
        "create-backup-key",
        help="Create a private binary recovery key; existing keys are never overwritten",
    )
    encrypted_key.add_argument(
        "--output", required=True, help="New private .dmtkey file"
    )
    for action, description in (
        (
            "backup-encrypted",
            "Capture a consistent workspace into an authenticated encrypted bundle",
        ),
        (
            "verify-encrypted-backup",
            "Decrypt and validate using a separately retained encrypted manifest checksum",
        ),
        (
            "restore-encrypted",
            "Validate encrypted backup and restore into a new workspace",
        ),
    ):
        encrypted = commands.add_parser(action, help=description)
        encrypted.add_argument("--key-file", required=True)
        encrypted.add_argument("--timeout", type=float, default=900)
        if action == "backup-encrypted":
            encrypted.add_argument(
                "--home", default=os.environ.get("DMT_HOME", "data/workspace")
            )
            encrypted.add_argument(
                "--output",
                required=True,
                help="New backup directory outside the workspace",
            )
        else:
            encrypted.add_argument("--backup", required=True)
            encrypted.add_argument("--encrypted-manifest-sha256", required=True)
            if action == "restore-encrypted":
                encrypted.add_argument(
                    "--home", required=True, help="New recovered workspace directory"
                )
            else:
                encrypted.add_argument(
                    "--scratch-parent",
                    required=True,
                    help="Approved private storage for temporary plaintext, outside the backup",
                )
    schedule = commands.add_parser(
        "configure-backups",
        help="Create a private encrypted backup policy, dedicated vault and durable catalog",
    )
    schedule.add_argument("--home", required=True)
    schedule.add_argument("--vault", required=True)
    schedule.add_argument("--catalog", required=True)
    schedule.add_argument("--config", required=True)
    schedule.add_argument("--key-file", required=True)
    schedule.add_argument("--interval-seconds", type=int, default=86400)
    schedule.add_argument("--keep-last", type=int, default=7)
    schedule.add_argument("--timeout", type=float, default=900)
    schedule.add_argument("--retry-seconds", type=int, default=300)
    schedule.add_argument(
        "--auto-prune",
        action="store_true",
        help="Authorize pruning owned older backups after a successful capture",
    )
    for action, description in (
        ("backup-run", "Capture when due and recover interrupted durable receipts"),
        ("backup-service", "Run scheduled captures until interrupted"),
        ("backups-status", "Inspect authenticated receipts and the retention plan"),
        ("prune-backups", "Apply a reviewed retention plan to owned bundles only"),
    ):
        command = commands.add_parser(action, help=description)
        command.add_argument("--config", required=True)
        command.add_argument("--key-file", required=True)
        if action == "prune-backups":
            command.add_argument("--expected-plan-sha256", required=True)
        if action in ("backup-run", "backup-service"):
            command.add_argument("--monitor-key-file")
            command.add_argument("--health-file")
        if action == "backup-service":
            command.add_argument("--health-interval-seconds", type=float, default=30)
    monitor_key = commands.add_parser(
        "create-backup-monitor-key",
        help="Create separate signing and public verification keys for backup health",
    )
    monitor_key.add_argument("--private-file", required=True)
    monitor_key.add_argument("--public-file", required=True)
    monitor_profile = commands.add_parser(
        "configure-backup-monitor",
        help="Create a private public-key-only API backup monitor profile",
    )
    monitor_profile.add_argument("--config", required=True)
    monitor_profile.add_argument("--key-file", required=True)
    monitor_profile.add_argument("--public-key-file", required=True)
    monitor_profile.add_argument("--health-file", required=True)
    monitor_profile.add_argument("--output", required=True)
    monitor_profile.add_argument("--max-snapshot-age-seconds", type=int, default=120)
    monitor_profile.add_argument("--max-backup-age-seconds", type=int, default=90000)
    monitor_profile.add_argument("--required", action="store_true")
    publish_health = commands.add_parser(
        "publish-backup-health",
        help="Publish a signed minimal health summary from the authenticated backup catalog",
    )
    publish_health.add_argument("--config", required=True)
    publish_health.add_argument("--key-file", required=True)
    publish_health.add_argument("--monitor-key-file", required=True)
    publish_health.add_argument("--health-file", required=True)
    admission = commands.add_parser(
        "import-remote",
        help="Stage a frozen-study export and queue local runtime admission",
    )
    admission.add_argument(
        "--home", default=os.environ.get("DMT_HOME", "data/workspace")
    )
    admission.add_argument("--keys-file", default=os.environ.get("DMT_KEYS_FILE"))
    admission.add_argument("--sso-file", default=os.environ.get("DMT_SSO_FILE"))
    admission.add_argument("--project", required=True)
    admission.add_argument("--study", required=True)
    admission.add_argument("--export", required=True)
    admission.add_argument("--manifest-sha256", required=True)
    admission.add_argument("--receipt", required=True)
    admission.add_argument("--request-key", required=True)
    admission.add_argument(
        "--database-schema",
        default=os.environ.get("DMT_DATABASE_SCHEMA", "decision_workbench"),
    )
    revoke_access = commands.add_parser(
        "revoke-access-token",
        help="Atomically deny a JWT issuer/jti pair in trusted SSO configuration",
    )
    revoke_access.add_argument("--sso-file", required=True)
    revoke_access.add_argument("--issuer", required=True)
    revoke_access.add_argument("--jti", required=True)
    args = parser.parse_args()
    if args.action in ("provision-key", "rotate-key", "revoke-key", "list-keys"):
        from .credentials import credentials
        from .auth import AuthenticationUnavailable

        action = {
            "provision-key": "provision",
            "rotate-key": "rotate",
            "revoke-key": "revoke",
            "list-keys": "list",
        }[args.action]
        try:
            result = credentials(
                args.file,
                action,
                getattr(args, "tenant", None),
                getattr(args, "actor", None),
                getattr(args, "role", None),
            )
        except (ValueError, OSError, AuthenticationUnavailable) as exc:
            parser.error(str(exc))
        if action in ("provision", "rotate"):
            print(
                "Credential configuration saved. Keep this bearer token private; it is shown once:"
            )
            print(result["token"])
        else:
            print(json.dumps(result, indent=2))
    elif args.action == "revoke-access-token":
        from .sso_credentials import revoke_access_token
        from .auth import AuthenticationUnavailable

        try:
            result = revoke_access_token(args.sso_file, args.issuer, args.jti)
        except (ValueError, OSError, AuthenticationUnavailable) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action == "verify-audit-export":
        from .audit_exports import verify_file

        try:
            result = verify_file(args.file, args.manifest_sha256)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action in (
        "create-backup-monitor-key",
        "configure-backup-monitor",
        "publish-backup-health",
    ):
        from .backup_monitor import create_keys, configure_monitor, Publisher

        try:
            if args.action == "create-backup-monitor-key":
                result = create_keys(args.private_file, args.public_file)
            elif args.action == "configure-backup-monitor":
                result = configure_monitor(
                    args.config,
                    args.key_file,
                    args.public_key_file,
                    args.health_file,
                    args.output,
                    args.max_snapshot_age_seconds,
                    args.max_backup_age_seconds,
                    args.required,
                )
            else:
                result = Publisher(
                    args.config, args.key_file, args.monitor_key_file, args.health_file
                ).publish()
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action in (
        "configure-backups",
        "backup-run",
        "backup-service",
        "backups-status",
        "prune-backups",
    ):
        from . import backup_schedule
        import sqlite3

        try:
            publisher = None
            if args.action in ("backup-run", "backup-service"):
                if bool(args.monitor_key_file) != bool(args.health_file):
                    raise ValueError("Supply both monitor-key-file and health-file")
                if args.monitor_key_file:
                    from .backup_monitor import Publisher

                    publisher = Publisher(
                        args.config,
                        args.key_file,
                        args.monitor_key_file,
                        args.health_file,
                    )
            if args.action == "configure-backups":
                result = backup_schedule.configure(
                    args.home,
                    args.vault,
                    args.catalog,
                    args.config,
                    args.key_file,
                    interval=args.interval_seconds,
                    keep_last=args.keep_last,
                    timeout=args.timeout,
                    retry=args.retry_seconds,
                    auto_prune=args.auto_prune,
                )
            elif args.action == "backup-run":
                try:
                    result = backup_schedule.run_due(args.config, args.key_file)
                finally:
                    if publisher:
                        publisher.publish()
            elif args.action == "backups-status":
                result = backup_schedule.status(args.config, args.key_file)
            elif args.action == "prune-backups":
                result = backup_schedule.prune(
                    args.config, args.key_file, args.expected_plan_sha256
                )
            else:
                import signal
                import threading

                stop = threading.Event()
                previous = {
                    sig: signal.getsignal(sig)
                    for sig in (signal.SIGINT, signal.SIGTERM)
                }
                for sig in previous:
                    signal.signal(sig, lambda number, frame: stop.set())
                try:
                    if publisher:
                        publisher.start(args.health_interval_seconds)

                    def report(item):
                        if publisher:
                            publisher.publish()
                        print(json.dumps(item), flush=True)

                    backup_schedule.serve(
                        args.config,
                        args.key_file,
                        stop=stop,
                        on_result=report,
                    )
                finally:
                    if publisher:
                        publisher.close()
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
                return
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action in (
        "create-backup-key",
        "backup-encrypted",
        "verify-encrypted-backup",
        "restore-encrypted",
    ):
        from .encrypted_recovery import (
            create_key,
            backup_encrypted,
            verify_encrypted,
            restore_encrypted,
        )

        try:
            if args.action == "create-backup-key":
                result = create_key(args.output)
            elif args.action == "backup-encrypted":
                result = backup_encrypted(
                    args.home, args.output, args.key_file, args.timeout
                )
            elif args.action == "verify-encrypted-backup":
                result = verify_encrypted(
                    args.backup,
                    args.key_file,
                    args.encrypted_manifest_sha256,
                    args.scratch_parent,
                    args.timeout,
                )
            else:
                result = restore_encrypted(
                    args.backup,
                    args.home,
                    args.key_file,
                    args.encrypted_manifest_sha256,
                    args.timeout,
                )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action in ("backup", "verify-backup", "restore"):
        from .recovery import backup_workspace, verify_backup, restore_workspace

        try:
            if args.action == "backup":
                result = backup_workspace(args.home, args.output, args.timeout)
            elif args.action == "verify-backup":
                manifest = verify_backup(args.backup, args.manifest_sha256)
                result = {
                    "verified": True,
                    "manifest_sha256": args.manifest_sha256,
                    "summary": manifest["summary"],
                }
            else:
                result = restore_workspace(args.backup, args.home, args.manifest_sha256)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.action == "import-remote":
        from .auth import (
            Authentication,
            AuthenticationError,
            AuthenticationUnavailable,
            UnmappedIdentity,
        )
        from .remote import stage_import
        from .service import Service
        from .store import Store, Conflict

        service = None
        try:
            auth = Authentication(keys_file=args.keys_file, sso_file=args.sso_file)
            authorization = "Bearer " + os.environ.get("DMT_OPERATOR_TOKEN", "")
            principal = auth.authenticate(authorization)

            def reauthorize():
                if auth.authenticate(authorization) != principal:
                    raise AuthenticationError(
                        "Operator identity changed during staging"
                    )

            database_url = os.environ.get("DMT_DATABASE_URL") or None
            artifacts = None
            if database_url is not None:
                from .artifact_s3 import repository_from_environment

                artifacts = repository_from_environment()
            service = Service(
                Store(
                    args.home,
                    database_url=database_url,
                    database_schema=args.database_schema,
                    artifact_store=artifacts,
                )
            )
            with Path(args.receipt).open("rb") as stream:
                body = stream.read(65537)
            if len(body) > 65536:
                raise ValueError("Receipt exceeds 64 KB")
            result = stage_import(
                service,
                principal,
                args.project,
                args.study,
                args.export,
                args.manifest_sha256,
                json.loads(body),
                args.request_key,
                authorize=reauthorize,
            )
            print(
                json.dumps(
                    {
                        "job_id": result["id"],
                        "status": result["status"],
                        "release_required": True,
                    },
                    indent=2,
                )
            )
        except (
            ValueError,
            OSError,
            PermissionError,
            LookupError,
            Conflict,
            AuthenticationError,
            AuthenticationUnavailable,
            UnmappedIdentity,
        ) as exc:
            parser.error(str(exc))
        finally:
            if service is not None:
                service.runtime.close()
    elif args.action == "run":
        launch(args)
    else:
        import uvicorn

        uvicorn.run(
            "decision_workbench.platform.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            access_log=False,
        )


def launch(args, stop_event=None):
    import httpx

    app_path = Path(
        os.environ.get("DMT_APP_PATH", str(Path.cwd() / "app.py"))
    ).resolve()
    if not app_path.is_file():
        raise SystemExit(
            "Run from the repository root or set DMT_APP_PATH to the browser app file"
        )
    root = app_path.parent
    if not args.keys_file:
        from .local import launch_personal

        return launch_personal(args, app_path, stop_event)
    keys = Path(args.keys_file).resolve()
    if not keys.is_file():
        raise SystemExit("Provision an operator credential file first")
    environment = os.environ.copy()
    environment.update(
        DMT_KEYS_FILE=str(keys),
        DMT_HOME=str(Path(args.home or "data/workspace").resolve()),
        DMT_API_URL=f"http://127.0.0.1:{args.api_port}",
    )
    # Complete an upgrade before API and worker can race their startup migrations.
    from .store import Store

    Store(environment["DMT_HOME"])
    commands = [
        [
            sys.executable,
            "-m",
            "decision_workbench.platform.cli",
            "serve",
            "--port",
            str(args.api_port),
        ],
        [
            environment.get("DMT_WORKER_PYTHON") or sys.executable,
            "-m",
            "decision_workbench.platform.worker",
        ],
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(args.ui_port),
            "--server.headless",
            "true",
        ],
    ]
    processes = []
    try:
        for command in commands:
            child_environment = environment
            if command[2] == "streamlit":
                child_environment = {
                    k: v
                    for k, v in environment.items()
                    if not k.startswith(("DMT_PROVIDER_", "DMT_CLOUD_"))
                    and k
                    not in (
                        "DMT_DECISION_PROFILES_FILE",
                        "DMT_TEACHER_API_KEY",
                        "TEACHER_API_KEY",
                    )
                }
            processes.append(
                subprocess.Popen(command, cwd=root, env=child_environment, shell=False)
            )
        print(
            f"Open http://127.0.0.1:{args.ui_port} and connect using your workspace credential.",
            flush=True,
        )
        print(
            "Press Ctrl+C to stop the local services. Workspace data is persisted.",
            flush=True,
        )
        while stop_event is None or not stop_event.is_set():
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(
                    "A local service exited. Inspect its output; all services are stopping."
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
