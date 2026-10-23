# Host Decision Model Workbench on Azure

[Application hosting](README.md#application-hosting) · [Project overview](../../README.md) · [Azure GPU training](../remote-training/azure.md#direct-submission)

Run separate API, worker and Streamlit services in Azure Container Apps, with private-network PostgreSQL metadata, versioned Blob artifacts and Key Vault configuration. The generated ARM template also supplies managed identities, initialization jobs, logs, a database-storage alert and deletion locks on durable resources.

**Deployment creates billable resources.** Review the template, What-If result, replica counts, database/availability choices and storage retention before creating them. The template generator itself makes no Azure calls.

## Prepare your account and network

Install the [local application](../../README.md#run-on-your-computer), Docker with Linux/amd64 support, and Azure CLI v2 with Container Apps commands. Authenticate with `az login` and select your intended subscription. Register the required Azure resource providers if your subscription has not used these services.

You need permission to create the resources, custom role definition and role assignments, and permission to use the existing network, registry and vault. Workspace roles inside the app do not grant these Azure permissions.

Prepare these prerequisites in your chosen region:

- An existing VNet with a dedicated Container Apps infrastructure subnet delegated to `Microsoft.App/environments`. Workload-profile environments require at least `/27`; reserve enough addresses for your replicas and future capacity. [Container Apps subnet requirements](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks?tabs=workload-profiles-env).
- A separate subnet delegated to `Microsoft.DBforPostgreSQL/flexibleServers`, plus a private DNS zone ending in `.postgres.database.azure.com` linked to the connected VNet. The zone must differ from the generated server's name. Allow database traffic between the application subnet and this private database. [PostgreSQL networking and DNS](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-private).
- Outbound access for image pulls, Key Vault, Microsoft Entra, Blob Storage, logging and the external providers you approve. A private endpoint or corporate DNS policy needs the corresponding routing/DNS configuration; the template does not create your landing-zone network.
- An existing ACR registry and RBAC-enabled Key Vault in the deployment resource group. The vault must permit ARM template deployment and be reachable from the application network. Give your configuration operator scoped secret-write access; Azure resource ownership alone does not grant Key Vault data-plane access. [Key Vault parameter references](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/key-vault-parameter).
- Quota and regional support for PostgreSQL 18, the selected general-purpose database size, Container Apps Consumption and Storage ZRS. Check the actual subscription capabilities before deployment. [Supported PostgreSQL versions](https://learn.microsoft.com/en-us/azure/postgresql/configure-maintain/concepts-supported-versions).

Defaults use two API replicas, one worker and one browser replica. The worker has 2 vCPU/4 GiB; API/browser replicas have 1 vCPU/2 GiB. The database defaults to `Standard_D2ds_v5` with 32 GiB initial storage, growth enabled and seven-day backup retention. Database HA and environment zone redundancy are configurable but disabled initially; choose supported availability settings before creation for your production requirements.

The Blob container is private and requires Entra authorization over HTTPS. Its account endpoint is network-accessible by default; this is not a private-endpoint deployment. Anonymous blob access and Shared Key authorization are disabled. The database has no public-network access.

## Build and publish your image

Build the CPU base, managed-service extension and Azure extension from the repository root:

```text
docker build --platform linux/amd64 -t dmt-base:release .
docker build --platform linux/amd64 --build-arg BASE_IMAGE=dmt-base:release -f docker/deployment/Dockerfile -t dmt-managed:release .
docker build --platform linux/amd64 --build-arg BASE_IMAGE=dmt-managed:release -f docker/deployment/Dockerfile.azure -t dmt-azure:release .
```

The Azure extension adds small hash-pinned Blob, identity and Key Vault SDK packages. It includes no foundation-model weights or GPU runtime. Keep the final image immutable after qualification.

Replace `REGISTRY`, `LOGIN_SERVER` and `RELEASE` with your registry name, its actual `loginServer`, and a release tag:

```text
az acr show --name REGISTRY --query loginServer --output tsv
az acr login --name REGISTRY
docker tag dmt-azure:release LOGIN_SERVER/decision-model-workbench:RELEASE
docker push LOGIN_SERVER/decision-model-workbench:RELEASE
az acr repository show --name REGISTRY --image decision-model-workbench:RELEASE --query digest --output tsv
```

Supply the returned digest's 64 hexadecimal characters as `imageDigest`, without the `sha256:` prefix. The template constructs an image URI pinned by digest. Keep `registryName` and the login server from the same registry in this resource group. [ACR managed-identity image pulls](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull).

## Create private configuration in Key Vault

Provision your initial workspace key and save the displayed bearer token in your password manager:

```text
domain-platform provision-key --file data/config/operator-keys.json --tenant your-team --actor owner --role admin
python -m decision_workbench.platform.deployment_azure --keys-file data/config/operator-keys.json --output data/config/azure
```

The second command creates a **new** directory containing `backend.json`, `database-admin.json` and `database-app.json`. Database passwords are generated independently; command output contains paths only. Existing output directories are refused. Protect these files with your operating system's access controls and keep them out of Git.

Store their JSON values in your vault, returning only secret IDs:

```text
az keyvault secret set --vault-name VAULT --name workbench-backend --file data/config/azure/backend.json --encoding utf-8 --query id --output tsv
az keyvault secret set --vault-name VAULT --name workbench-database-admin --file data/config/azure/database-admin.json --encoding utf-8 --query id --output tsv
az keyvault secret set --vault-name VAULT --name workbench-database-app --file data/config/azure/database-app.json --encoding utf-8 --query id --output tsv
```

Use the database administrator secret's exact version in the ARM parameter reference. Its JSON supplies the server's initial administrator login/password and is also read by the bootstrap job. Application tasks can read only the separate application database secret. The bootstrap creates a non-administrator login and owned schema; it does not silently rotate an existing application's database password.

The backend JSON accepts the same [typed provider, teacher, cloud and SSO fields](aws.md#prepare-service-private-configuration) as the other managed deployment. Put only approved profiles and credentials there. Key Vault secrets have a 25 KiB limit, so keep the configuration inventory bounded; datasets and model files belong in their normal stores. [Key Vault limits](https://learn.microsoft.com/en-us/azure/key-vault/general/service-limits).

For optional browser OIDC, create a separate `workbench-ui` JSON secret with only `secrets_toml`, using the [enterprise sign-in format](../authentication-guide.md#organization-sign-in). Set `uiAuthMode` to `oidc` after registering the deployed UI's HTTPS callback. Workspace-key mode requires no UI secret. The browser identity has no database or artifact permissions.

## Review and create infrastructure

```text
python -m decision_workbench.platform.azure_deployment --output data/deploy/azure-stack.json
```

Copy [the parameter example](../../examples/deployment/azure-parameters.example.json) to private `data/deploy/azure-parameters.json`. Replace every subscription/resource/registry/secret-version value and digest. Use a canonical lowercase `namePrefix`, the real infrastructure subnet/proxy CIDRs, and your connected private DNS zone. Keep `deployServices=false` initially.

```text
az deployment group validate --resource-group GROUP --template-file data/deploy/azure-stack.json --parameters "@data/deploy/azure-parameters.json"
az deployment group what-if --resource-group GROUP --template-file data/deploy/azure-stack.json --parameters "@data/deploy/azure-parameters.json"
```

Review all changes and costs, then create the approved resources:

```text
az deployment group create --name dmt-infrastructure --resource-group GROUP --mode Incremental --template-file data/deploy/azure-stack.json --parameters "@data/deploy/azure-parameters.json"
az deployment group show --name dmt-infrastructure --resource-group GROUP --query properties.outputs --output json
```

Outputs include the environment, initialization job names, database server, artifact account, log workspace and backend identity client ID. Passwords and secret values are not outputs. Role assignments can take time to propagate; inspect authorization failures and allow propagation before retrying a failed initialization execution.

## Initialize and publish workspace authorization

Start the output `bootstrapJob`, then inspect that execution until its status is `Succeeded`:

```text
az containerapp job start --name BOOTSTRAP_JOB --resource-group GROUP
az containerapp job execution list --name BOOTSTRAP_JOB --resource-group GROUP --output table
```

Inspect job console/system logs if it fails. Do not start application services until the bootstrap completes successfully. Next run `operatorJob`; its initial command publishes authorization revision `0`:

```text
az containerapp job start --name OPERATOR_JOB --resource-group GROUP
az containerapp job execution list --name OPERATOR_JOB --resource-group GROUP --output table
```

Require `Succeeded` for that execution too. The operator job reports the committed authorization revision/hash. APIs read this shared PostgreSQL authority, so later role changes and key revocations do not depend on restarting every instance.

Restrict Azure job-start/update permissions to infrastructure operators. An execution override can run a different command with that job's available identity; the bootstrap identity can read the database administrator secret. These are stronger permissions than ordinary tenant administration inside the application. [Job execution permissions and overrides](https://learn.microsoft.com/en-us/azure/container-apps/jobs?tabs=azure-cli).

## Start and use the application

Set `deployServices=true` in the same private parameter file, review a new What-If result, then deploy it in Incremental mode. Open output `webUrl`. Select **Shared workspace**, use output `apiUrl`, and enter the bearer token saved earlier. For OIDC, register `webUrl/oauth2callback`, configure the separate UI secret and enable the issuer settings described in the sign-in guide.

Follow [the first-project walkthrough](../app-guide.md#screen-manual--first-project-walkthrough): import/review examples, prepare independent holdouts, train the CPU baseline, release it and make predictions. Check `apiUrl/ready` and project operations before enabling external generation or GPU jobs.

The worker has ingress disabled and a positive minimum replica count, so the durable queue can be processed without an HTTP request. Worker jobs and API instances share PostgreSQL/Blob state. Local caches and unfinished files are ephemeral. [Background-service scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app).

This CPU image supports baseline training, hosted decision policies, data/review/generation workflows and configured remote-training controllers. Provider accounts, keys, images and approvals remain necessary. Native foundation-model execution needs its [matching runtime/resources](../setup.md#configuration--configure-model-training-and-serving); installing this hosting image does not add GPU support. Keep [remote Azure GPU training](../remote-training/azure.md#application-managed-training) separate from application hosting.

## Monitor, rotate and update

Use API `/ready`, [workspace operations](../shared-workspace-operations-guide.md), Container Apps revision/replica state, job execution history and the configured Log Analytics workspace. The template includes an 80% database-storage alert; supply existing action-group IDs in `actionGroupIds` for notification delivery. Configure/confirm those destinations in your account. [PostgreSQL metric names](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-dbforpostgresql-flexibleservers-metrics).

To run `status` or publish a later authorization revision, export the operator job's **complete** template:

```text
az containerapp job show --name OPERATOR_JOB --resource-group GROUP --query properties.template --output yaml > data/deploy/operator-execution.yaml
```

Retain its image, environment and resources. Replace only the `command` list with `python`, `-m`, `decision_workbench.platform.deployment`, then either `status` or `publish-auth`, `--expected-auth-revision`, and your current revision as separate list entries. Update the full approved backend secret before publishing changed trust settings. Start the override and inspect that execution:

```text
az containerapp job start --name OPERATOR_JOB --resource-group GROUP --yaml data/deploy/operator-execution.yaml
```

Overrides replace the whole execution template, not just the edited command; use the exported file rather than an incomplete hand-written container. Changed authorization with a stale expected revision is refused. Identical publication can be retried without creating another revision.

Provider secrets/profiles and browser OIDC files are loaded at process startup. Updating Key Vault alone does not refresh those process files. Drain work, approve a new immutable image/configuration and use a **new** `revisionLabel` when deploying updated services. Keep API and worker on the same approved source/image revision. A worker begins draining on `SIGTERM`, but the 120-second grace period can interrupt a longer job; inspect uncertain upstream operations before resubmission.

Rotate a database login and its matching Key Vault value through one approved procedure; rerunning bootstrap does not rotate an existing application login. Keep the pinned administrator-secret parameter reference current after an intentional administrator-password rotation, so a later infrastructure update does not reapply an older password.

`deployServices` is an initial **creation gate**. Setting it back to false in an Incremental ARM deployment does not stop or delete existing apps. For a planned shutdown, pause submissions, drain the queue and deliberately deactivate the active application revisions through Azure revision controls. Retain the database, objects and required recovery data. Restore a prior approved image/configuration as a new coordinated revision when rollback is needed; revision rollback does not undo database changes or external calls. [Revision operations](https://learn.microsoft.com/en-us/azure/container-apps/revisions-manage).

## Recover or remove the deployment

Use [coordinated recovery](../recovery-guide.md#shared-workspace-recovery) with [Azure live/vault configuration](../shared-artifacts-guide.md#azure-blob) from an authorized private-network operator environment. Database backups alone do not capture model objects. Keep independent recovery copies protected by their own identities and retention policy.

Restore into a fresh database schema, private home and object prefix. This transport copies within one storage account; it does not provide cross-account recovery. Replacement application infrastructure can reuse that retained account by setting `createArtifactAccount=false`, `artifactAccountName` and `artifactContainer` to the existing resources in this resource group. Set `databaseSchema` and `artifactPrefix` to the restored values before starting services. Configure the existing account's versioning and permissions deliberately; the template leaves its storage policy unchanged in reuse mode. Isolate the original deployment, publish current trust/revocation policy and verify cold predictions before exposing restored traffic.

The template's `CanNotDelete` locks prevent accidental removal of its database and newly created artifact account. After draining services and capturing/verifying recovery data, remove only the approved locks/resources through your infrastructure retention procedure. Existing vault/registry/network resources and their secrets remain separately managed. Soft-deleted blobs, prior versions, logs and retained backups can continue to incur charges; deleting an ARM deployment record does not remove those resources.

Before production adoption, validate deployed identity permissions, DNS/TLS, networking, role isolation, billing/quotas, model behavior, capacity and a cold restore in your account. Local SDK/template/container checks do not establish live Azure compatibility or production qualification.
