# Application deployment configuration

Copy [AWS parameters](aws-parameters.example.json) into a private file under `data/deploy/`, replace every account/network/image/secret value, and follow the [AWS hosting guide](../../docs/application-hosting/aws.md). Generate the CloudFormation template with the packaged module; the example parameter file is not a deployable configuration until you supply your own resources.

Backend and UI secret values belong in your secret manager and private operator files. Do not commit real configuration or credentials.
