# Application deployment configuration

Copy [AWS parameters](aws-parameters.example.json) into a private file under `data/deploy/`, replace every account/network/image/secret value, and follow the [AWS hosting guide](../../docs/application-hosting/aws.md). Generate the CloudFormation template with the packaged module; the example parameter file is not a deployable configuration until you supply your own resources.

Backend and UI secret values belong in your secret manager and private operator files. Do not commit real configuration or credentials.

For Azure, copy [the ARM parameter example](azure-parameters.example.json), supply your own network, registry and Key Vault references, and follow [Azure application hosting](../../docs/application-hosting/azure.md). Keep `deployServices=false` until database and authorization initialization succeed. The example contains fictional identifiers and requires replacement; it creates no resources by itself.

For Google Cloud, copy [the variable example](gcp-variables.example.json) to a private `*.tfvars.json` file and follow [GCP application hosting](../../docs/application-hosting/gcp.md). The [offline Terraform tests](gcp.tftest.hcl) exercise both initialization and service-start phases with a mocked provider; they create no cloud resources.
