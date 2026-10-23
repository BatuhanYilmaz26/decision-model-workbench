"""Generate an Azure application ARM template without creating cloud resources."""

import argparse
import json
from pathlib import Path

APP_API = "2025-07-01"
PG_API = "2025-08-01"
IDENTITY_API = "2023-01-31"
AUTH_API = "2022-04-01"
STORAGE_API = "2025-06-01"


def arm(value):
    return "[" + value + "]"


def parameter(name):
    return "parameters('" + name + "')"


def variable(name):
    return "variables('" + name + "')"


def resource_id(kind, name):
    return "resourceId('" + kind + "', " + variable(name) + ")"


def template():
    parameters = {
        "namePrefix": {
            "type": "string",
            "defaultValue": "dmt",
            "minLength": 3,
            "maxLength": 16,
            "metadata": {
                "description": "Canonical lowercase name prefix; start with a letter, use letters/digits/single hyphens"
            },
        },
        "location": {"type": "string", "defaultValue": "[resourceGroup().location]"},
        "infrastructureSubnetId": {
            "type": "string",
            "metadata": {
                "description": "Dedicated Container Apps workload-profile subnet in the connected VNet"
            },
        },
        "databaseSubnetId": {
            "type": "string",
            "metadata": {
                "description": "Separate subnet delegated to Microsoft.DBforPostgreSQL/flexibleServers"
            },
        },
        "databasePrivateDnsZoneId": {
            "type": "string",
            "metadata": {
                "description": "Existing PostgreSQL private DNS zone linked to the same VNet"
            },
        },
        "trustedProxyCidrs": {
            "type": "string",
            "metadata": {
                "description": "Infrastructure subnet/proxy CIDRs accepted for forwarded headers; never use *"
            },
        },
        "registryName": {
            "type": "string",
            "metadata": {
                "description": "Existing ACR name in this resource group for the image-pull role"
            },
        },
        "registryServer": {
            "type": "string",
            "metadata": {
                "description": "Actual ACR loginServer, without scheme or credentials"
            },
        },
        "imageRepository": {
            "type": "string",
            "defaultValue": "decision-model-workbench",
        },
        "imageDigest": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "metadata": {
                "description": "The 64 lowercase hexadecimal SHA-256 digest; do not supply a mutable image tag"
            },
        },
        "keyVaultName": {
            "type": "string",
            "metadata": {
                "description": "Existing RBAC Key Vault in this resource group, reachable from the application network"
            },
        },
        "backendSecretName": {"type": "string", "defaultValue": "workbench-backend"},
        "databaseAppSecretName": {
            "type": "string",
            "defaultValue": "workbench-database-app",
        },
        "databaseAdminSecretName": {
            "type": "string",
            "defaultValue": "workbench-database-admin",
        },
        "databaseAdministratorConfiguration": {
            "type": "securestring",
            "metadata": {
                "description": "JSON admin username/password supplied by an ARM Key Vault parameter reference to databaseAdminSecretName"
            },
        },
        "uiAuthMode": {
            "type": "string",
            "defaultValue": "workspace",
            "allowedValues": ["workspace", "oidc"],
        },
        "uiSecretName": {"type": "string", "defaultValue": "workbench-ui"},
        "databaseSchema": {"type": "string", "defaultValue": "decision_workbench"},
        "databaseSku": {"type": "string", "defaultValue": "Standard_D2ds_v5"},
        "databaseHighAvailability": {
            "type": "string",
            "defaultValue": "Disabled",
            "allowedValues": ["Disabled", "ZoneRedundant", "SameZone"],
        },
        "zoneRedundantEnvironment": {"type": "bool", "defaultValue": False},
        "artifactAccountName": {
            "type": "string",
            "defaultValue": "[concat('dmt', uniqueString(resourceGroup().id, parameters('namePrefix')))]",
            "minLength": 3,
            "maxLength": 24,
        },
        "createArtifactAccount": {
            "type": "bool",
            "defaultValue": True,
            "metadata": {
                "description": "False reuses an existing versioned account/container in this resource group during recovery; its policy is not changed"
            },
        },
        "artifactContainer": {"type": "string", "defaultValue": "artifacts"},
        "artifactPrefix": {"type": "string", "defaultValue": "live"},
        "revisionLabel": {
            "type": "string",
            "defaultValue": "r1",
            "minLength": 1,
            "maxLength": 32,
        },
        "deployServices": {
            "type": "bool",
            "defaultValue": False,
            "metadata": {
                "description": "Creation gate: keep false until bootstrap and authorization publication succeed. False does not stop existing apps in incremental mode."
            },
        },
        "apiReplicas": {"type": "int", "defaultValue": 2, "minValue": 1, "maxValue": 4},
        "workerReplicas": {
            "type": "int",
            "defaultValue": 1,
            "minValue": 1,
            "maxValue": 4,
        },
        "actionGroupIds": {"type": "array", "defaultValue": []},
    }
    variables = {
        key: arm("concat(parameters('namePrefix'), '-" + suffix + "')")
        for key, suffix in {
            "environment": "environment",
            "logs": "logs",
            "database": "database",
            "api": "api",
            "worker": "worker",
            "web": "web",
            "bootstrap": "bootstrap",
            "operator": "operator",
            "backendIdentity": "backend",
            "webIdentity": "browser",
            "bootstrapIdentity": "db-init",
            "pullIdentity": "pull",
        }.items()
    }
    variables["database"] = arm(
        "concat(parameters('namePrefix'), '-pg-', uniqueString(resourceGroup().id))"
    )
    variables["image"] = arm(
        "concat(parameters('registryServer'), '/', parameters('imageRepository'), '@sha256:', parameters('imageDigest'))"
    )
    variables["artifactWriterDefinition"] = arm(
        "guid(resourceGroup().id, parameters('namePrefix'), 'artifact-writer-v1')"
    )
    resources = []

    def add(resource_type, version, name, properties, *, located=True, **extra):
        item = {
            "type": resource_type,
            "apiVersion": version,
            "name": name,
            "properties": properties,
            **extra,
        }
        if located:
            item["location"] = arm(parameter("location"))
        resources.append(item)
        return item

    def identity_id(name):
        return resource_id("Microsoft.ManagedIdentity/userAssignedIdentities", name)

    for name in ["backendIdentity", "webIdentity", "bootstrapIdentity", "pullIdentity"]:
        add(
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            IDENTITY_API,
            arm(variable(name)),
            {},
        )

    def grant(label, scope, identity, definition, *, condition=True):
        name = "guid(resourceGroup().id, parameters('namePrefix'), '" + label + "')"
        add(
            "Microsoft.Authorization/roleAssignments",
            AUTH_API,
            arm(name),
            {
                "principalId": arm(
                    "reference("
                    + identity_id(identity)
                    + ", '"
                    + IDENTITY_API
                    + "').principalId"
                ),
                "principalType": "ServicePrincipal",
                "roleDefinitionId": arm(definition),
            },
            located=False,
            scope=arm(
                "replace(" + scope + ", concat(resourceGroup().id, '/providers/'), '')"
            ),
            condition=condition,
            dependsOn=[arm(identity_id(identity))]
            + (
                [
                    arm(
                        "resourceId('Microsoft.Authorization/roleDefinitions', variables('artifactWriterDefinition'))"
                    )
                ]
                if label == "backend-artifacts"
                else []
            ),
        )
        return arm(
            "extensionResourceId("
            + scope
            + ", 'Microsoft.Authorization/roleAssignments', "
            + name
            + ")"
        )

    pull_grant = grant(
        "registry-pull",
        "resourceId('Microsoft.ContainerRegistry/registries', parameters('registryName'))",
        "pullIdentity",
        "subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')",
    )
    secret_grants = {}
    for label, secret, identity in [
        ("backend-config", "backendSecretName", "backendIdentity"),
        ("backend-db", "databaseAppSecretName", "backendIdentity"),
        ("bootstrap-admin", "databaseAdminSecretName", "bootstrapIdentity"),
        ("bootstrap-app", "databaseAppSecretName", "bootstrapIdentity"),
        ("browser-login", "uiSecretName", "webIdentity"),
    ]:
        secret_grants[label] = grant(
            label,
            "resourceId('Microsoft.KeyVault/vaults/secrets', parameters('keyVaultName'), "
            + parameter(secret)
            + ")",
            identity,
            "subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')",
            condition=(
                arm("equals(parameters('uiAuthMode'), 'oidc')")
                if label == "browser-login"
                else True
            ),
        )

    add(
        "Microsoft.Authorization/roleDefinitions",
        AUTH_API,
        arm(variable("artifactWriterDefinition")),
        {
            "roleName": arm(
                "concat(parameters('namePrefix'), '-artifacts-', uniqueString(resourceGroup().id))"
            ),
            "description": "Read/create artifact blobs without deletion or account administration",
            "type": "CustomRole",
            "assignableScopes": ["[resourceGroup().id]"],
            "permissions": [
                {
                    "actions": [],
                    "notActions": [],
                    "dataActions": [
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
                    ],
                    "notDataActions": [],
                }
            ],
        },
        located=False,
    )
    storage_id = "resourceId('Microsoft.Storage/storageAccounts', parameters('artifactAccountName'))"
    storage = add(
        "Microsoft.Storage/storageAccounts",
        STORAGE_API,
        arm(parameter("artifactAccountName")),
        {
            "minimumTlsVersion": "TLS1_2",
            "supportsHttpsTrafficOnly": True,
            "allowBlobPublicAccess": False,
            "allowSharedKeyAccess": False,
            "defaultToOAuthAuthentication": True,
            "publicNetworkAccess": "Enabled",
        },
        condition=arm(parameter("createArtifactAccount")),
        kind="StorageV2",
        sku={"name": "Standard_ZRS"},
    )
    add(
        "Microsoft.Storage/storageAccounts/blobServices",
        STORAGE_API,
        arm("concat(parameters('artifactAccountName'), '/default')"),
        {
            "isVersioningEnabled": True,
            "deleteRetentionPolicy": {"enabled": True, "days": 7},
            "containerDeleteRetentionPolicy": {"enabled": True, "days": 7},
        },
        located=False,
        condition=arm(parameter("createArtifactAccount")),
        dependsOn=[arm(storage_id)],
    )
    for container in [arm(parameter("artifactContainer")), "backups"]:
        container_expr = (
            container[1:-1] if container.startswith("[") else "'" + container + "'"
        )
        add(
            "Microsoft.Storage/storageAccounts/blobServices/containers",
            STORAGE_API,
            arm(
                "concat(parameters('artifactAccountName'), '/default/', "
                + container_expr
                + ")"
            ),
            {"publicAccess": "None"},
            located=False,
            condition=arm(parameter("createArtifactAccount")),
            dependsOn=[
                arm(
                    "resourceId('Microsoft.Storage/storageAccounts/blobServices', parameters('artifactAccountName'), 'default')"
                )
            ],
        )
    blob_scope = "resourceId('Microsoft.Storage/storageAccounts/blobServices/containers', parameters('artifactAccountName'), 'default', parameters('artifactContainer'))"
    blob_grant = grant(
        "backend-artifacts",
        blob_scope,
        "backendIdentity",
        "reference(resourceId('Microsoft.Authorization/roleDefinitions', variables('artifactWriterDefinition')), '"
        + AUTH_API
        + "', 'Full').id",
    )
    # The target may already exist during a restore; do not require a conditional account resource.
    resources[-1]["dependsOn"].append(
        arm(
            "resourceId('Microsoft.Storage/storageAccounts/blobServices/containers', parameters('artifactAccountName'), 'default', parameters('artifactContainer'))"
        )
    )

    add(
        "Microsoft.OperationalInsights/workspaces",
        "2023-09-01",
        arm(variable("logs")),
        {
            "sku": {"name": "PerGB2018"},
            "retentionInDays": 30,
            "features": {"enableLogAccessUsingOnlyResourcePermissions": True},
        },
    )
    logs_id = resource_id("Microsoft.OperationalInsights/workspaces", "logs")
    environment_id = resource_id("Microsoft.App/managedEnvironments", "environment")
    add(
        "Microsoft.App/managedEnvironments",
        APP_API,
        arm(variable("environment")),
        {
            "zoneRedundant": arm(parameter("zoneRedundantEnvironment")),
            "vnetConfiguration": {
                "infrastructureSubnetId": arm(parameter("infrastructureSubnetId")),
                "internal": False,
            },
            "workloadProfiles": [
                {"name": "Consumption", "workloadProfileType": "Consumption"}
            ],
            "appLogsConfiguration": {
                "destination": "log-analytics",
                "logAnalyticsConfiguration": {
                    "customerId": arm(
                        "reference(" + logs_id + ", '2023-09-01').customerId"
                    ),
                    "sharedKey": arm(
                        "listKeys(" + logs_id + ", '2023-09-01').primarySharedKey"
                    ),
                },
            },
        },
        dependsOn=[arm(logs_id)],
    )
    database_id = resource_id("Microsoft.DBforPostgreSQL/flexibleServers", "database")
    add(
        "Microsoft.DBforPostgreSQL/flexibleServers",
        PG_API,
        arm(variable("database")),
        {
            "version": "18",
            "createMode": "Default",
            "administratorLogin": arm(
                "json(parameters('databaseAdministratorConfiguration')).username"
            ),
            "administratorLoginPassword": arm(
                "json(parameters('databaseAdministratorConfiguration')).password"
            ),
            "authConfig": {
                "activeDirectoryAuth": "Disabled",
                "passwordAuth": "Enabled",
            },
            "network": {
                "delegatedSubnetResourceId": arm(parameter("databaseSubnetId")),
                "privateDnsZoneArmResourceId": arm(
                    parameter("databasePrivateDnsZoneId")
                ),
                "publicNetworkAccess": "Disabled",
            },
            "storage": {"storageSizeGB": 32, "autoGrow": "Enabled"},
            "backup": {"backupRetentionDays": 7, "geoRedundantBackup": "Disabled"},
            "highAvailability": {"mode": arm(parameter("databaseHighAvailability"))},
        },
        sku={"name": arm(parameter("databaseSku")), "tier": "GeneralPurpose"},
    )
    add(
        "Microsoft.DBforPostgreSQL/flexibleServers/databases",
        PG_API,
        arm("concat(variables('database'), '/workbench')"),
        {"charset": "UTF8", "collation": "en_US.utf8"},
        located=False,
        dependsOn=[arm(database_id)],
    )

    def env(identity, *, backend=False, administrator=False):
        values = {
            "DMT_DEPLOYMENT_PROVIDER": "azure",
            "DMT_HOME": "/app/data/workspace",
            "DMT_AZURE_KEY_VAULT_URL": arm(
                "concat('https://', parameters('keyVaultName'), '.vault.azure.net')"
            ),
            "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID": arm(
                "reference("
                + identity_id(identity)
                + ", '"
                + IDENTITY_API
                + "').clientId"
            ),
            "DMT_DATABASE_HOST": arm(
                "reference("
                + database_id
                + ", '"
                + PG_API
                + "').fullyQualifiedDomainName"
            ),
            "DMT_DATABASE_NAME": "workbench",
            "DMT_DATABASE_SCHEMA": arm(parameter("databaseSchema")),
            "DMT_DATABASE_CA": "/etc/ssl/certs/ca-certificates.crt",
            "DMT_DATABASE_SECRET_ID": arm(parameter("databaseAppSecretName")),
            "DMT_MODEL_RESOURCE_LOCK": "/app/data/model-capacity.lock",
        }
        if administrator:
            values["DMT_DATABASE_ADMIN_SECRET_ID"] = arm(
                parameter("databaseAdminSecretName")
            )
        if backend:
            values.update(
                {
                    "DMT_CONFIG_SECRET_ID": arm(parameter("backendSecretName")),
                    "DMT_ARTIFACT_AZURE_ACCOUNT_URL": arm(
                        "concat('https://', parameters('artifactAccountName'), '.blob.core.windows.net')"
                    ),
                    "DMT_ARTIFACT_AZURE_CONTAINER": arm(parameter("artifactContainer")),
                    "DMT_ARTIFACT_AZURE_PREFIX": arm(parameter("artifactPrefix")),
                    "DMT_ARTIFACT_AZURE_CLIENT_ID": values[
                        "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID"
                    ],
                    "DMT_TRUSTED_PROXY_IPS": arm(parameter("trustedProxyCidrs")),
                    "DMT_MONITOR_INTERVAL_SECONDS": "30",
                }
            )
        return values

    def identities(identity):
        return {
            "type": "UserAssigned",
            "userAssignedIdentities": {
                arm(identity_id(identity)): {},
                arm(identity_id("pullIdentity")): {},
            },
        }

    def registry():
        return [
            {
                "server": arm(parameter("registryServer")),
                "identity": arm(identity_id("pullIdentity")),
            }
        ]

    def container(role, values, cpu=1, memory="2Gi", port=None):
        value = {
            "name": "application",
            "image": arm(variable("image")),
            "command": ["python", "-m", "decision_workbench.platform.deployment", role],
            "env": [{"name": k, "value": v} for k, v in values.items()],
            "resources": {"cpu": cpu, "memory": memory},
        }
        if port:
            value["probes"] = [
                {
                    "type": "Startup",
                    "tcpSocket": {"port": port},
                    "periodSeconds": 5,
                    "failureThreshold": 30,
                },
                {
                    "type": "Liveness",
                    "tcpSocket": {"port": port},
                    "periodSeconds": 30,
                    "failureThreshold": 3,
                },
                {
                    "type": "Readiness",
                    "httpGet": {
                        "port": port,
                        "path": "/ready" if role == "api" else "/_stcore/health",
                    },
                    "periodSeconds": 30,
                    "timeoutSeconds": 10,
                    "failureThreshold": 3,
                },
            ]
        return value

    shared_dependencies = [
        arm(environment_id),
        pull_grant,
        arm(
            "resourceId('Microsoft.DBforPostgreSQL/flexibleServers/databases', variables('database'), 'workbench')"
        ),
    ]
    backend_dependencies = [
        *shared_dependencies,
        blob_grant,
        secret_grants["backend-config"],
        secret_grants["backend-db"],
    ]
    for name, role, identity, values, dependencies in [
        (
            "bootstrap",
            "bootstrap",
            "bootstrapIdentity",
            env("bootstrapIdentity", administrator=True),
            [
                *shared_dependencies,
                secret_grants["bootstrap-admin"],
                secret_grants["bootstrap-app"],
            ],
        ),
        (
            "operator",
            "publish-auth",
            "backendIdentity",
            env("backendIdentity", backend=True),
            backend_dependencies,
        ),
    ]:
        task = container(role, values)
        if role == "publish-auth":
            task["command"] += ["--expected-auth-revision", "0"]
        add(
            "Microsoft.App/jobs",
            APP_API,
            arm(variable(name)),
            {
                "environmentId": arm(environment_id),
                "workloadProfileName": "Consumption",
                "configuration": {
                    "triggerType": "Manual",
                    "replicaTimeout": 600,
                    "replicaRetryLimit": 0,
                    "manualTriggerConfig": {
                        "parallelism": 1,
                        "replicaCompletionCount": 1,
                    },
                    "registries": registry(),
                },
                "template": {"containers": [task]},
            },
            identity=identities(identity),
            dependsOn=dependencies,
        )

    for name, role, port in [
        ("api", "api", 8080),
        ("worker", "worker", None),
        ("web", "web", 8501),
    ]:
        browser = role == "web"
        identity = "webIdentity" if browser else "backendIdentity"
        values = env(identity, backend=not browser)
        if browser:
            values = {
                k: v
                for k, v in values.items()
                if k
                in {
                    "DMT_DEPLOYMENT_PROVIDER",
                    "DMT_AZURE_KEY_VAULT_URL",
                    "DMT_AZURE_MANAGED_IDENTITY_CLIENT_ID",
                }
            }
            values.update(
                {
                    "DMT_API_URL": arm(
                        "if(parameters('deployServices'), concat('https://', reference("
                        + resource_id("Microsoft.App/containerApps", "api")
                        + ", '"
                        + APP_API
                        + "').configuration.ingress.fqdn), '')"
                    ),
                    "DMT_UI_AUTH_MODE": arm(parameter("uiAuthMode")),
                    "DMT_UI_OIDC_PROVIDER": "enterprise",
                    "DMT_UI_SECRET_ID": arm(
                        "if(equals(parameters('uiAuthMode'), 'oidc'), parameters('uiSecretName'), '')"
                    ),
                }
            )
        configuration = {
            "activeRevisionsMode": "Single",
            "registries": registry(),
            "identitySettings": [
                {"identity": arm(identity_id("pullIdentity")), "lifecycle": "None"}
            ],
        }
        if port:
            configuration["ingress"] = {
                "external": True,
                "allowInsecure": False,
                "targetPort": port,
                "transport": "auto",
                "traffic": [{"latestRevision": True, "weight": 100}],
            }
        minimum = (
            arm(parameter("workerReplicas" if role == "worker" else "apiReplicas"))
            if not browser
            else 1
        )
        add(
            "Microsoft.App/containerApps",
            APP_API,
            arm(variable(name)),
            {
                "environmentId": arm(environment_id),
                "workloadProfileName": "Consumption",
                "configuration": configuration,
                "template": {
                    "revisionSuffix": arm(parameter("revisionLabel")),
                    "terminationGracePeriodSeconds": 120,
                    "containers": [
                        container(
                            role,
                            values,
                            2 if role == "worker" else 1,
                            "4Gi" if role == "worker" else "2Gi",
                            port,
                        )
                    ],
                    "scale": {
                        "minReplicas": minimum,
                        "maxReplicas": 1 if browser else 4,
                    },
                },
            },
            identity=identities(identity),
            condition=arm(parameter("deployServices")),
            dependsOn=(
                [
                    *shared_dependencies,
                    secret_grants["browser-login"],
                    arm(resource_id("Microsoft.App/containerApps", "api")),
                ]
                if browser
                else backend_dependencies
            ),
        )

    for label, scope, condition in [
        ("database", database_id, True),
        ("artifacts", storage_id, arm(parameter("createArtifactAccount"))),
    ]:
        add(
            "Microsoft.Authorization/locks",
            "2020-05-01",
            arm("concat(parameters('namePrefix'), '-" + label + "-retain')"),
            {
                "level": "CanNotDelete",
                "notes": "Capture and verify recovery data before deliberate removal",
            },
            located=False,
            scope=arm(
                "replace(" + scope + ", concat(resourceGroup().id, '/providers/'), '')"
            ),
            condition=condition,
            dependsOn=[arm(scope)],
        )
    add(
        "Microsoft.Insights/metricAlerts",
        "2018-03-01",
        arm("concat(parameters('namePrefix'), '-database-storage')"),
        {
            "description": "Inspect database capacity before storage exhaustion",
            "severity": 2,
            "enabled": True,
            "scopes": [arm(database_id)],
            "evaluationFrequency": "PT1M",
            "windowSize": "PT5M",
            "criteria": {
                "odata.type": "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
                "allOf": [
                    {
                        "name": "StoragePercent",
                        "metricNamespace": "Microsoft.DBforPostgreSQL/flexibleServers",
                        "metricName": "storage_percent",
                        "operator": "GreaterThanOrEqual",
                        "threshold": 80,
                        "timeAggregation": "Average",
                        "criterionType": "StaticThresholdCriterion",
                    }
                ],
            },
            "copy": [
                {
                    "name": "actions",
                    "count": "[length(parameters('actionGroupIds'))]",
                    "input": {
                        "actionGroupId": "[parameters('actionGroupIds')[copyIndex('actions')]]"
                    },
                }
            ],
        },
        located=False,
        location="global",
        dependsOn=[arm(database_id)],
    )
    outputs = {
        "environment": {"type": "string", "value": arm(variable("environment"))},
        "bootstrapJob": {"type": "string", "value": arm(variable("bootstrap"))},
        "operatorJob": {"type": "string", "value": arm(variable("operator"))},
        "databaseServer": {"type": "string", "value": arm(variable("database"))},
        "artifactAccount": {
            "type": "string",
            "value": arm(parameter("artifactAccountName")),
        },
        "backendIdentityClientId": {
            "type": "string",
            "value": arm(
                "reference("
                + identity_id("backendIdentity")
                + ", '"
                + IDENTITY_API
                + "').clientId"
            ),
        },
        "logWorkspace": {"type": "string", "value": arm(variable("logs"))},
    }
    for name in ["api", "web"]:
        outputs[name + "Url"] = {
            "type": "string",
            "value": arm(
                "if(parameters('deployServices'), concat('https://', reference("
                + resource_id("Microsoft.App/containerApps", name)
                + ", '"
                + APP_API
                + "').configuration.ingress.fqdn), '')"
            ),
        }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": parameters,
        "variables": variables,
        "resources": resources,
        "outputs": outputs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        help="New JSON template file; existing files are preserved",
    )
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        output.write(json.dumps(template(), indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
