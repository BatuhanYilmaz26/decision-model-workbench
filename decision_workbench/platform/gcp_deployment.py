"""Generate Terraform JSON for Cloud Run, Cloud SQL and versioned GCS application hosting."""

import argparse
import json
from pathlib import Path

PROVIDER_VERSION = "7.22.0"


def expression(value):
    return "${" + value + "}"


def template():
    variables = {
        "project_id": {"type": "string"},
        "region": {"type": "string", "default": "us-central1"},
        "name_prefix": {
            "type": "string",
            "default": "dmt",
            "validation": [
                {
                    "condition": expression(
                        'can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.name_prefix))'
                    ),
                    "error_message": "Use 3-16 lowercase letters/digits/hyphens, starting with a letter.",
                }
            ],
        },
        "image": {
            "type": "string",
            "validation": [
                {
                    "condition": expression(
                        'can(regex("^[a-z0-9-]+-docker[.]pkg[.]dev/[^@ ]+@sha256:[a-f0-9]{64}$", var.image))'
                    ),
                    "error_message": "Use an immutable Artifact Registry image URI pinned by SHA-256.",
                }
            ],
        },
        "registry_repository": {"type": "string"},
        "backend_secret": {"type": "string", "default": "workbench-backend"},
        "app_secret": {"type": "string", "default": "workbench-database-app"},
        "admin_secret": {"type": "string", "default": "workbench-database-admin"},
        "ui_secret": {"type": "string", "default": "workbench-ui"},
        "backend_version": {"type": "string", "default": "1"},
        "app_version": {"type": "string", "default": "1"},
        "admin_version": {"type": "string", "default": "1"},
        "ui_version": {"type": "string", "default": "1"},
        "ui_auth_mode": {
            "type": "string",
            "default": "workspace",
            "validation": [
                {
                    "condition": expression(
                        'contains(["workspace", "oidc"], var.ui_auth_mode)'
                    ),
                    "error_message": "Choose workspace or oidc.",
                }
            ],
        },
        "database_schema": {"type": "string", "default": "decision_workbench"},
        "database_tier": {"type": "string", "default": "db-custom-2-7680"},
        "database_availability": {
            "type": "string",
            "default": "ZONAL",
            "validation": [
                {
                    "condition": expression(
                        'contains(["ZONAL", "REGIONAL"], var.database_availability)'
                    ),
                    "error_message": "Choose ZONAL or REGIONAL availability.",
                }
            ],
        },
        "artifact_prefix": {"type": "string", "default": "live"},
        "deploy_services": {"type": "bool", "default": False},
        "worker_replicas": {
            "type": "number",
            "default": 1,
            "validation": [
                {
                    "condition": expression(
                        "var.worker_replicas >= 1 && var.worker_replicas <= 4 && floor(var.worker_replicas) == var.worker_replicas"
                    ),
                    "error_message": "Use an integer worker replica count from 1 through 4.",
                }
            ],
        },
        "notification_channels": {"type": "list(string)", "default": []},
    }
    resources = {}

    def add(kind, name, value):
        resources.setdefault(kind, {})[name] = value

    services = [
        "run.googleapis.com",
        "sqladmin.googleapis.com",
        "secretmanager.googleapis.com",
        "storage.googleapis.com",
        "artifactregistry.googleapis.com",
        "monitoring.googleapis.com",
        "iam.googleapis.com",
    ]
    add(
        "google_project_service",
        "apis",
        {
            "for_each": expression("toset(" + json.dumps(services) + ")"),
            "service": expression("each.value"),
            "disable_on_destroy": False,
        },
    )
    add(
        "google_artifact_registry_repository_iam_member",
        "pull",
        {
            "location": expression("var.region"),
            "repository": expression("var.registry_repository"),
            "role": "roles/artifactregistry.reader",
            "member": expression(
                '"serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"'
            ),
        },
    )
    for name in ["backend", "browser", "bootstrap"]:
        add(
            "google_service_account",
            name,
            {
                "account_id": expression('"${var.name_prefix}-' + name + '"'),
                "display_name": "Decision workbench " + name,
                "depends_on": ["google_project_service.apis"],
            },
        )
    for name in ["backend", "bootstrap"]:
        add(
            "google_project_iam_member",
            name + "_database",
            {
                "project": expression("var.project_id"),
                "role": "roles/cloudsql.client",
                "member": expression(
                    '"serviceAccount:${google_service_account.' + name + '.email}"'
                ),
            },
        )

    def secret_grant(name, identity, secret, condition=None):
        value = {
            "project": expression("var.project_id"),
            "secret_id": expression("var." + secret),
            "role": "roles/secretmanager.secretAccessor",
            "member": expression(
                '"serviceAccount:${google_service_account.' + identity + '.email}"'
            ),
            "depends_on": ["google_project_service.apis"],
        }
        if condition:
            value["count"] = expression(condition)
        add("google_secret_manager_secret_iam_member", name, value)

    for name, identity, secret in [
        ("backend_config", "backend", "backend_secret"),
        ("backend_database", "backend", "app_secret"),
        ("bootstrap_admin", "bootstrap", "admin_secret"),
        ("bootstrap_app", "bootstrap", "app_secret"),
    ]:
        secret_grant(name, identity, secret)
    secret_grant(
        "browser_login", "browser", "ui_secret", 'var.ui_auth_mode == "oidc" ? 1 : 0'
    )

    for name, suffix in [("artifacts", "artifacts"), ("backups", "backups")]:
        add(
            "google_storage_bucket",
            name,
            {
                "name": expression(
                    '"${var.project_id}-${var.name_prefix}-' + suffix + '"'
                ),
                "location": expression("var.region"),
                "uniform_bucket_level_access": True,
                "public_access_prevention": "enforced",
                "versioning": [{"enabled": True}],
                "soft_delete_policy": [{"retention_duration_seconds": 604800}],
                "force_destroy": False,
                "lifecycle": {"prevent_destroy": True},
                "depends_on": ["google_project_service.apis"],
            },
        )
    for name, role in [
        ("read", "roles/storage.objectViewer"),
        ("create", "roles/storage.objectCreator"),
    ]:
        add(
            "google_storage_bucket_iam_member",
            "backend_" + name,
            {
                "bucket": expression("google_storage_bucket.artifacts.name"),
                "role": role,
                "member": expression(
                    '"serviceAccount:${google_service_account.backend.email}"'
                ),
            },
        )
    add(
        "google_sql_database_instance",
        "workspace",
        {
            "name": expression('"${var.name_prefix}-database"'),
            "database_version": "POSTGRES_18",
            "region": expression("var.region"),
            "root_password": expression(
                "jsondecode(data.google_secret_manager_secret_version_access.admin.secret_data).password"
            ),
            "deletion_protection": True,
            "settings": [
                {
                    "tier": expression("var.database_tier"),
                    "edition": "ENTERPRISE",
                    "availability_type": expression("var.database_availability"),
                    "disk_size": 32,
                    "disk_autoresize": True,
                    "connector_enforcement": "REQUIRED",
                    "deletion_protection_enabled": True,
                    "ip_configuration": [{"ipv4_enabled": True}],
                    "backup_configuration": [
                        {
                            "enabled": True,
                            "point_in_time_recovery_enabled": True,
                            "start_time": "03:00",
                            "transaction_log_retention_days": 7,
                            "backup_retention_settings": [
                                {"retained_backups": 7, "retention_unit": "COUNT"}
                            ],
                        }
                    ],
                }
            ],
            "lifecycle": {"prevent_destroy": True},
            "depends_on": ["google_project_service.apis"],
        },
    )
    add(
        "google_sql_database",
        "workspace",
        {
            "name": "workbench",
            "instance": expression("google_sql_database_instance.workspace.name"),
        },
    )

    def secret(name, version):
        return expression(
            '"projects/${var.project_id}/secrets/${var.'
            + name
            + "}/versions/${var."
            + version
            + '}"'
        )

    def environment(backend=False, admin=False):
        values = {
            "DMT_DEPLOYMENT_PROVIDER": "gcp",
            "DMT_GCP_CLOUD_SQL_CONNECTION_NAME": expression(
                "google_sql_database_instance.workspace.connection_name"
            ),
            "DMT_DATABASE_NAME": "workbench",
            "DMT_DATABASE_SCHEMA": expression("var.database_schema"),
            "DMT_DATABASE_SECRET_ID": secret("app_secret", "app_version"),
            "DMT_MODEL_RESOURCE_LOCK": "/app/data/model-capacity.lock",
        }
        if admin:
            values["DMT_DATABASE_ADMIN_SECRET_ID"] = secret(
                "admin_secret", "admin_version"
            )
        if backend:
            values.update(
                {
                    "DMT_CONFIG_SECRET_ID": secret("backend_secret", "backend_version"),
                    "DMT_ARTIFACT_GCS_BUCKET": expression(
                        "google_storage_bucket.artifacts.name"
                    ),
                    "DMT_ARTIFACT_GCS_PROJECT": expression("var.project_id"),
                    "DMT_ARTIFACT_GCS_PREFIX": expression("var.artifact_prefix"),
                }
            )
        return [{"name": k, "value": v} for k, v in values.items()]

    def container(role, env, port=None):
        value = {
            "image": expression("var.image"),
            "command": ["python", "-m", "decision_workbench.platform.deployment"],
            "args": [role],
            "env": env,
            "resources": [
                {
                    "limits": {
                        "cpu": "2" if role == "worker" else "1",
                        "memory": "4Gi" if role == "worker" else "2Gi",
                    }
                }
            ],
        }
        if role != "web":
            value["volume_mounts"] = [{"name": "cloudsql", "mount_path": "/cloudsql"}]
        if port:
            value["ports"] = [{"container_port": port}]
            value["startup_probe"] = [
                {
                    "tcp_socket": [{"port": port}],
                    "period_seconds": 5,
                    "failure_threshold": 24,
                }
            ]
            value["readiness_probe"] = [
                {
                    "http_get": [
                        {
                            "path": "/ready" if role == "api" else "/_stcore/health",
                            "port": port,
                        }
                    ],
                    "period_seconds": 30,
                    "timeout_seconds": 10,
                }
            ]
            value["liveness_probe"] = [
                {
                    "http_get": [
                        {
                            "path": "/health" if role == "api" else "/_stcore/health",
                            "port": port,
                        }
                    ],
                    "period_seconds": 30,
                    "timeout_seconds": 10,
                }
            ]
        return value

    volume = [
        {
            "name": "cloudsql",
            "cloud_sql_instance": [
                {
                    "instances": [
                        expression(
                            "google_sql_database_instance.workspace.connection_name"
                        )
                    ]
                }
            ],
        }
    ]
    dependencies = [
        "google_sql_database.workspace",
        "google_project_iam_member.backend_database",
        "google_secret_manager_secret_iam_member.backend_config",
        "google_secret_manager_secret_iam_member.backend_database",
        "google_storage_bucket_iam_member.backend_read",
        "google_storage_bucket_iam_member.backend_create",
        "google_artifact_registry_repository_iam_member.pull",
    ]
    for name, role, identity in [
        ("bootstrap", "bootstrap", "bootstrap"),
        ("operator", "publish-auth", "backend"),
    ]:
        task = container(
            role, environment(backend=name == "operator", admin=name == "bootstrap")
        )
        if name == "operator":
            task["args"] += ["--expected-auth-revision", "0"]
        depends = (
            dependencies
            if name == "operator"
            else [
                "google_sql_database.workspace",
                "google_project_iam_member.bootstrap_database",
                "google_secret_manager_secret_iam_member.bootstrap_admin",
                "google_secret_manager_secret_iam_member.bootstrap_app",
                "google_artifact_registry_repository_iam_member.pull",
            ]
        )
        add(
            "google_cloud_run_v2_job",
            name,
            {
                "name": expression('"${var.name_prefix}-' + name + '"'),
                "location": expression("var.region"),
                "deletion_protection": True,
                "template": [
                    {
                        "task_count": 1,
                        "parallelism": 1,
                        "template": [
                            {
                                "service_account": expression(
                                    "google_service_account." + identity + ".email"
                                ),
                                "execution_environment": "EXECUTION_ENVIRONMENT_GEN2",
                                "max_retries": 0,
                                "timeout": "600s",
                                "containers": [task],
                                "volumes": volume,
                            }
                        ],
                    }
                ],
                "depends_on": depends,
            },
        )
    for name, port in [("api", 8080), ("web", 8501)]:
        browser = name == "web"
        values = (
            [
                {"name": "DMT_DEPLOYMENT_PROVIDER", "value": "gcp"},
                {
                    "name": "DMT_API_URL",
                    "value": expression("google_cloud_run_v2_service.api[0].uri"),
                },
                {"name": "DMT_UI_AUTH_MODE", "value": expression("var.ui_auth_mode")},
                {"name": "DMT_UI_OIDC_PROVIDER", "value": "enterprise"},
                {
                    "name": "DMT_UI_SECRET_ID",
                    "value": expression(
                        'var.ui_auth_mode == "oidc" ? "projects/${var.project_id}/secrets/${var.ui_secret}/versions/${var.ui_version}" : ""'
                    ),
                },
            ]
            if browser
            else environment(backend=True)
        )
        task = {
            "service_account": expression(
                "google_service_account."
                + ("browser" if browser else "backend")
                + ".email"
            ),
            "execution_environment": "EXECUTION_ENVIRONMENT_GEN2",
            "timeout": "3600s",
            "max_instance_request_concurrency": 16,
            "containers": [container(name, values, port)],
            "scaling": [
                {
                    "min_instance_count": 1 if browser else 2,
                    "max_instance_count": 1 if browser else 4,
                }
            ],
            "session_affinity": browser,
        }
        if not browser:
            task["volumes"] = volume
        add(
            "google_cloud_run_v2_service",
            name,
            {
                "count": expression("var.deploy_services ? 1 : 0"),
                "name": expression('"${var.name_prefix}-' + name + '"'),
                "location": expression("var.region"),
                "ingress": "INGRESS_TRAFFIC_ALL",
                "invoker_iam_disabled": True,
                "deletion_protection": True,
                "template": [task],
                "depends_on": (
                    dependencies
                    if not browser
                    else [
                        "google_secret_manager_secret_iam_member.browser_login",
                        "google_artifact_registry_repository_iam_member.pull",
                    ]
                ),
            },
        )
    add(
        "google_cloud_run_v2_worker_pool",
        "worker",
        {
            "count": expression("var.deploy_services ? 1 : 0"),
            "name": expression('"${var.name_prefix}-worker"'),
            "location": expression("var.region"),
            "deletion_protection": True,
            "scaling": [
                {
                    "scaling_mode": "MANUAL",
                    "manual_instance_count": expression("var.worker_replicas"),
                }
            ],
            "instance_splits": [
                {"type": "INSTANCE_SPLIT_ALLOCATION_TYPE_LATEST", "percent": 100}
            ],
            "template": [
                {
                    "service_account": expression(
                        "google_service_account.backend.email"
                    ),
                    "containers": [container("worker", environment(backend=True))],
                    "volumes": volume,
                }
            ],
            "depends_on": dependencies,
        },
    )
    add(
        "google_monitoring_alert_policy",
        "database_storage",
        {
            "display_name": "Decision workbench database storage",
            "combiner": "OR",
            "enabled": True,
            "notification_channels": expression("var.notification_channels"),
            "conditions": [
                {
                    "display_name": "Database storage above 80%",
                    "condition_threshold": [
                        {
                            "filter": expression(
                                '"metric.type=\\"cloudsql.googleapis.com/database/disk/utilization\\" AND resource.type=\\"cloudsql_database\\" AND resource.labels.database_id=\\"${var.project_id}:${google_sql_database_instance.workspace.name}\\""'
                            ),
                            "comparison": "COMPARISON_GT",
                            "threshold_value": 0.8,
                            "duration": "300s",
                            "aggregations": [
                                {
                                    "alignment_period": "60s",
                                    "per_series_aligner": "ALIGN_MEAN",
                                }
                            ],
                        }
                    ],
                }
            ],
            "depends_on": ["google_project_service.apis"],
        },
    )
    return {
        "terraform": {
            "required_version": ">= 1.5, < 2.0",
            "required_providers": {
                "google": {"source": "hashicorp/google", "version": PROVIDER_VERSION}
            },
        },
        "provider": {
            "google": [
                {
                    "project": expression("var.project_id"),
                    "region": expression("var.region"),
                }
            ]
        },
        "variable": variables,
        "data": {
            "google_project": {"current": {"project_id": expression("var.project_id")}},
            "google_secret_manager_secret_version_access": {
                "admin": {
                    "project": expression("var.project_id"),
                    "secret": expression("var.admin_secret"),
                    "version": expression("var.admin_version"),
                }
            },
        },
        "resource": resources,
        "output": {
            "api_url": {
                "value": expression('try(google_cloud_run_v2_service.api[0].uri, "")')
            },
            "web_url": {
                "value": expression('try(google_cloud_run_v2_service.web[0].uri, "")')
            },
            "bootstrap_job": {
                "value": expression("google_cloud_run_v2_job.bootstrap.name")
            },
            "operator_job": {
                "value": expression("google_cloud_run_v2_job.operator.name")
            },
            "cloud_sql_connection": {
                "value": expression(
                    "google_sql_database_instance.workspace.connection_name"
                )
            },
            "artifact_bucket": {
                "value": expression("google_storage_bucket.artifacts.name")
            },
            "backup_bucket": {
                "value": expression("google_storage_bucket.backups.name")
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        help="New main.tf.json file in a private deployment directory",
    )
    args = parser.parse_args()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(template(), stream, indent=2)
        stream.write("\n")
    print("Terraform configuration written to " + str(path))


if __name__ == "__main__":
    main()
