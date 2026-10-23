"""Generate the AWS application stack without creating or modifying cloud resources."""

import argparse
import json
from pathlib import Path


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def get(name, attribute="Arn"):
    return {"Fn::GetAtt": [name, attribute]}


def template():
    parameters = {
        "VpcId": {"Type": "AWS::EC2::VPC::Id"},
        "PublicSubnets": {
            "Type": "List<AWS::EC2::Subnet::Id>",
            "Description": "At least two public subnets in different Availability Zones for the load balancer",
        },
        "PrivateSubnets": {
            "Type": "List<AWS::EC2::Subnet::Id>",
            "Description": "At least two private subnets with outbound access to ECR, Logs, Secrets Manager, S3 and approved provider endpoints",
        },
        "VpcCidr": {
            "Type": "String",
            "AllowedPattern": "[0-9.]+/[0-9]+",
            "Description": "Trusted reverse-proxy CIDR; task ingress remains restricted to the load balancer security group",
        },
        "ImageUri": {
            "Type": "String",
            "AllowedPattern": r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?/[a-z0-9/_.-]+@sha256:[a-f0-9]{64}",
            "Description": "Linux/amd64 deployment image in ECR, pinned by digest",
        },
        "CertificateArn": {
            "Type": "String",
            "Description": "ACM certificate ARN in this region for the public hostname",
        },
        "Hostname": {
            "Type": "String",
            "AllowedPattern": "[a-z0-9][a-z0-9.-]+",
            "Description": "DNS name covered by the certificate; create its DNS alias after stack creation",
        },
        "BackendSecretArn": {
            "Type": "String",
            "Description": "Secrets Manager JSON backend configuration, containing operator_keys or sso and optional provider settings",
        },
        "UiSecretArn": {
            "Type": "String",
            "Default": "",
            "Description": "Optional separate JSON secret containing secrets_toml for Streamlit OIDC",
        },
        "UiAuthMode": {
            "Type": "String",
            "Default": "workspace",
            "AllowedValues": ["workspace", "oidc"],
        },
        "SecretsKmsKeyArn": {
            "Type": "String",
            "Default": "",
            "Description": "Customer KMS key for imported configuration secrets, if applicable",
        },
        "AdditionalBackendPolicyArn": {
            "Type": "String",
            "Default": "",
            "Description": "Optional operator-managed policy for approved remote training services; not attached to the browser",
        },
        "DatabaseVersion": {
            "Type": "String",
            "Default": "18",
            "AllowedPattern": r"18(?:\.[0-9]+)?",
            "Description": "Available PostgreSQL 18 major/minor in your region; verify with describe-db-engine-versions",
        },
        "DatabaseClass": {"Type": "String", "Default": "db.t4g.small"},
        "DatabaseSchema": {
            "Type": "String",
            "Default": "decision_workbench",
            "AllowedPattern": "(?!(?:pg_|public$|information_schema$))[a-z][a-z0-9_]{0,62}",
            "Description": "Dedicated application schema; select the restored schema during recovery",
        },
        "ArtifactPrefix": {
            "Type": "String",
            "Default": "live",
            "AllowedPattern": "[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)*",
            "Description": "Canonical model-object prefix; select the returned independent prefix after restore",
        },
        "DatabaseMultiAZ": {
            "Type": "String",
            "Default": "true",
            "AllowedValues": ["true", "false"],
        },
        "ServicesEnabled": {
            "Type": "String",
            "Default": "false",
            "AllowedValues": ["true", "false"],
            "Description": "Keep false until the bootstrap task has initialized the application role and schema",
        },
        "ApiCount": {"Type": "Number", "Default": 2, "MinValue": 1, "MaxValue": 8},
        "WorkerCount": {"Type": "Number", "Default": 1, "MinValue": 1, "MaxValue": 8},
        "AlarmTopicArn": {
            "Type": "String",
            "Default": "",
            "Description": "Optional existing SNS topic for infrastructure alarms",
        },
    }
    conditions = {
        name: {"Fn::Not": [{"Fn::Equals": [ref(parameter), ""]}]}
        for name, parameter in [
            ("HasUiSecret", "UiSecretArn"),
            ("HasSecretsKey", "SecretsKmsKeyArn"),
            ("HasExtraPolicy", "AdditionalBackendPolicyArn"),
            ("HasAlarmTopic", "AlarmTopicArn"),
        ]
    }
    conditions["RunServices"] = {"Fn::Equals": [ref("ServicesEnabled"), "true"]}
    conditions["MultiAZ"] = {"Fn::Equals": [ref("DatabaseMultiAZ"), "true"]}
    assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    resources = {}

    def resource(name, kind, properties, **attributes):
        resources[name] = {
            "Type": "AWS::" + kind,
            "Properties": properties,
            **attributes,
        }

    def policy(name, statements):
        return {
            "PolicyName": name,
            "PolicyDocument": {"Version": "2012-10-17", "Statement": statements},
        }

    def allow(actions, identifiers):
        return {"Effect": "Allow", "Action": actions, "Resource": identifiers}

    def role(name, statements, managed=None):
        props = {"AssumeRolePolicyDocument": assume}
        if statements:
            props["Policies"] = [policy("ApplicationAccess", statements)]
        if managed:
            props["ManagedPolicyArns"] = managed
        resource(name, "IAM::Role", props)

    kms = {
        "Fn::If": [
            "HasSecretsKey",
            allow(["kms:Decrypt"], ref("SecretsKmsKeyArn")),
            ref("AWS::NoValue"),
        ]
    }
    role(
        "ExecutionRole",
        [],
        [
            sub(
                "arn:${AWS::Partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
            )
        ],
    )
    role(
        "BackendRole",
        [
            allow(
                ["secretsmanager:GetSecretValue"],
                [ref("BackendSecretArn"), ref("AppDatabaseSecret")],
            ),
            allow(
                [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:PutObject",
                    "s3:AbortMultipartUpload",
                ],
                [sub("${Artifacts.Arn}/${ArtifactPrefix}/*")],
            ),
            kms,
        ],
        [
            {
                "Fn::If": [
                    "HasExtraPolicy",
                    ref("AdditionalBackendPolicyArn"),
                    ref("AWS::NoValue"),
                ]
            }
        ],
    )
    role(
        "BootstrapRole",
        [
            allow(
                ["secretsmanager:GetSecretValue"],
                [
                    get("Database", "MasterUserSecret.SecretArn"),
                    ref("AppDatabaseSecret"),
                ],
            )
        ],
    )
    role("UiRole", [])
    resources["UiRole"]["Properties"]["Policies"] = {
        "Fn::If": [
            "HasUiSecret",
            [
                policy(
                    "BrowserOidcAccess",
                    [allow(["secretsmanager:GetSecretValue"], ref("UiSecretArn")), kms],
                )
            ],
            ref("AWS::NoValue"),
        ]
    }
    resource(
        "Cluster",
        "ECS::Cluster",
        {"ClusterSettings": [{"Name": "containerInsights", "Value": "enabled"}]},
    )
    resource(
        "Logs",
        "Logs::LogGroup",
        {"RetentionInDays": 30},
        DeletionPolicy="Retain",
        UpdateReplacePolicy="Retain",
    )
    resource(
        "LoadBalancerGroup",
        "EC2::SecurityGroup",
        {
            "GroupDescription": "Public HTTPS application ingress",
            "VpcId": ref("VpcId"),
            "SecurityGroupIngress": [
                {"IpProtocol": "tcp", "FromPort": p, "ToPort": p, "CidrIp": "0.0.0.0/0"}
                for p in (80, 443)
            ],
        },
    )
    resource(
        "TaskGroup",
        "EC2::SecurityGroup",
        {
            "GroupDescription": "Application tasks; ingress only from this load balancer",
            "VpcId": ref("VpcId"),
            "SecurityGroupIngress": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": p,
                    "ToPort": p,
                    "SourceSecurityGroupId": ref("LoadBalancerGroup"),
                }
                for p in (8080, 8501)
            ],
        },
    )
    resource(
        "DatabaseGroup",
        "EC2::SecurityGroup",
        {
            "GroupDescription": "PostgreSQL from application tasks only",
            "VpcId": ref("VpcId"),
            "SecurityGroupIngress": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "SourceSecurityGroupId": ref("TaskGroup"),
                }
            ],
        },
    )
    resource(
        "DatabaseSubnets",
        "RDS::DBSubnetGroup",
        {
            "DBSubnetGroupDescription": "Private application metadata subnets",
            "SubnetIds": ref("PrivateSubnets"),
        },
    )
    resource(
        "AppDatabaseSecret",
        "SecretsManager::Secret",
        {
            "Description": "Application-only database login",
            "GenerateSecretString": {
                "SecretStringTemplate": '{"username":"workbench_app"}',
                "GenerateStringKey": "password",
                "PasswordLength": 48,
            },
        },
        DeletionPolicy="Retain",
        UpdateReplacePolicy="Retain",
    )
    resource(
        "Database",
        "RDS::DBInstance",
        {
            "Engine": "postgres",
            "EngineVersion": ref("DatabaseVersion"),
            "DBInstanceClass": ref("DatabaseClass"),
            "DBName": "workbench",
            "MasterUsername": "workbench_admin",
            "ManageMasterUserPassword": True,
            "AllocatedStorage": "20",
            "MaxAllocatedStorage": 200,
            "StorageType": "gp3",
            "StorageEncrypted": True,
            "PubliclyAccessible": False,
            "MultiAZ": {"Fn::If": ["MultiAZ", True, False]},
            "BackupRetentionPeriod": 7,
            "DeletionProtection": True,
            "AutoMinorVersionUpgrade": True,
            "DBSubnetGroupName": ref("DatabaseSubnets"),
            "VPCSecurityGroups": [ref("DatabaseGroup")],
        },
        DeletionPolicy="Snapshot",
        UpdateReplacePolicy="Snapshot",
    )
    resource(
        "Artifacts",
        "S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                k: True
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            },
            "OwnershipControls": {
                "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
            "LifecycleConfiguration": {
                "Rules": [
                    {
                        "Id": "AbortIncompleteUploads",
                        "Status": "Enabled",
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            },
        },
        DeletionPolicy="Retain",
        UpdateReplacePolicy="Retain",
    )
    resource(
        "ArtifactsPolicy",
        "S3::BucketPolicy",
        {
            "Bucket": ref("Artifacts"),
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [get("Artifacts"), sub("${Artifacts.Arn}/*")],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            },
        },
    )
    resource(
        "LoadBalancer",
        "ElasticLoadBalancingV2::LoadBalancer",
        {
            "Scheme": "internet-facing",
            "Type": "application",
            "Subnets": ref("PublicSubnets"),
            "SecurityGroups": [ref("LoadBalancerGroup")],
            "LoadBalancerAttributes": [
                {"Key": "idle_timeout.timeout_seconds", "Value": "120"}
            ],
        },
    )
    for name, port, health in [
        ("ApiTarget", 8080, "/ready"),
        ("UiTarget", 8501, "/_stcore/health"),
    ]:
        resource(
            name,
            "ElasticLoadBalancingV2::TargetGroup",
            {
                "VpcId": ref("VpcId"),
                "Port": port,
                "Protocol": "HTTP",
                "TargetType": "ip",
                "HealthCheckPath": health,
                "Matcher": {"HttpCode": "200"},
                "TargetGroupAttributes": [
                    {"Key": "deregistration_delay.timeout_seconds", "Value": "30"}
                ],
            },
        )
    resource(
        "Https",
        "ElasticLoadBalancingV2::Listener",
        {
            "LoadBalancerArn": ref("LoadBalancer"),
            "Port": 443,
            "Protocol": "HTTPS",
            "Certificates": [{"CertificateArn": ref("CertificateArn")}],
            "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
            "DefaultActions": [{"Type": "forward", "TargetGroupArn": ref("UiTarget")}],
        },
    )
    resource(
        "HttpRedirect",
        "ElasticLoadBalancingV2::Listener",
        {
            "LoadBalancerArn": ref("LoadBalancer"),
            "Port": 80,
            "Protocol": "HTTP",
            "DefaultActions": [
                {
                    "Type": "redirect",
                    "RedirectConfig": {
                        "Protocol": "HTTPS",
                        "Port": "443",
                        "StatusCode": "HTTP_301",
                    },
                }
            ],
        },
    )
    resource(
        "ApiRouting",
        "ElasticLoadBalancingV2::ListenerRule",
        {
            "ListenerArn": ref("Https"),
            "Priority": 1,
            "Conditions": [
                {
                    "Field": "path-pattern",
                    "Values": ["/v1/*", "/health", "/ready", "/docs*", "/openapi.json"],
                }
            ],
            "Actions": [{"Type": "forward", "TargetGroupArn": ref("ApiTarget")}],
        },
    )
    common = {
        "DMT_HOME": "/app/data/workspace",
        "DMT_DATABASE_NAME": "workbench",
        "DMT_DATABASE_SCHEMA": ref("DatabaseSchema"),
        "DMT_DATABASE_HOST": get("Database", "Endpoint.Address"),
        "DMT_DATABASE_SECRET_ID": ref("AppDatabaseSecret"),
        "AWS_REGION": ref("AWS::Region"),
        "DMT_MODEL_RESOURCE_LOCK": "/app/data/model-capacity.lock",
    }

    def task(name, service_role, iam_role, env, cpu, memory, port=None):
        container = {
            "Name": "application",
            "Image": ref("ImageUri"),
            "Essential": True,
            "Command": [
                "python",
                "-m",
                "decision_workbench.platform.deployment",
                service_role,
            ],
            "Environment": [{"Name": k, "Value": v} for k, v in env.items()],
            "StopTimeout": 120,
            "LinuxParameters": {"InitProcessEnabled": True},
            "LogConfiguration": {
                "LogDriver": "awslogs",
                "Options": {
                    "awslogs-group": ref("Logs"),
                    "awslogs-region": ref("AWS::Region"),
                    "awslogs-stream-prefix": service_role,
                },
            },
        }
        if port:
            container["PortMappings"] = [{"ContainerPort": port, "Protocol": "tcp"}]
        resource(
            name,
            "ECS::TaskDefinition",
            {
                "RequiresCompatibilities": ["FARGATE"],
                "NetworkMode": "awsvpc",
                "Cpu": str(cpu),
                "Memory": str(memory),
                "RuntimePlatform": {
                    "OperatingSystemFamily": "LINUX",
                    "CpuArchitecture": "X86_64",
                },
                "ExecutionRoleArn": get("ExecutionRole"),
                "TaskRoleArn": get(iam_role),
                "EphemeralStorage": {"SizeInGiB": 40},
                "ContainerDefinitions": [container],
            },
        )

    backend = {
        **common,
        "DMT_CONFIG_SECRET_ID": ref("BackendSecretArn"),
        "DMT_ARTIFACT_S3_BUCKET": ref("Artifacts"),
        "DMT_ARTIFACT_S3_PREFIX": ref("ArtifactPrefix"),
        "DMT_ARTIFACT_S3_REGION": ref("AWS::Region"),
        "DMT_TRUSTED_PROXY_IPS": ref("VpcCidr"),
        "DMT_MONITOR_INTERVAL_SECONDS": "30",
    }
    task("ApiTask", "api", "BackendRole", backend, 1024, 2048, 8080)
    task("WorkerTask", "worker", "BackendRole", backend, 2048, 4096)
    task(
        "BootstrapTask",
        "bootstrap",
        "BootstrapRole",
        {
            **common,
            "DMT_DATABASE_ADMIN_SECRET_ID": get(
                "Database", "MasterUserSecret.SecretArn"
            ),
        },
        512,
        1024,
    )
    task(
        "UiTask",
        "web",
        "UiRole",
        {
            "DMT_API_URL": sub("https://${Hostname}"),
            "DMT_UI_AUTH_MODE": ref("UiAuthMode"),
            "DMT_UI_OIDC_PROVIDER": "enterprise",
            "DMT_UI_SECRET_ID": ref("UiSecretArn"),
            "AWS_REGION": ref("AWS::Region"),
        },
        1024,
        2048,
        8501,
    )
    for name, definition, count, target, port in [
        ("ApiService", "ApiTask", ref("ApiCount"), "ApiTarget", 8080),
        ("WorkerService", "WorkerTask", ref("WorkerCount"), None, None),
        ("UiService", "UiTask", 1, "UiTarget", 8501),
    ]:
        props = {
            "Cluster": ref("Cluster"),
            "TaskDefinition": ref(definition),
            "LaunchType": "FARGATE",
            "PlatformVersion": "1.4.0",
            "DesiredCount": {"Fn::If": ["RunServices", count, 0]},
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "AssignPublicIp": "DISABLED",
                    "Subnets": ref("PrivateSubnets"),
                    "SecurityGroups": [ref("TaskGroup")],
                }
            },
            "DeploymentConfiguration": {
                "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
                "MaximumPercent": 200,
                "MinimumHealthyPercent": 100,
            },
        }
        if target:
            props.update(
                LoadBalancers=[
                    {
                        "ContainerName": "application",
                        "ContainerPort": port,
                        "TargetGroupArn": ref(target),
                    }
                ],
                HealthCheckGracePeriodSeconds=120,
            )
        resource(
            name,
            "ECS::Service",
            props,
            DependsOn="ApiRouting" if target else "Database",
        )
    for name, metric, dimensions, threshold in [
        (
            "ApiUnhealthy",
            "UnHealthyHostCount",
            [
                {
                    "Name": "TargetGroup",
                    "Value": get("ApiTarget", "TargetGroupFullName"),
                },
                {
                    "Name": "LoadBalancer",
                    "Value": get("LoadBalancer", "LoadBalancerFullName"),
                },
            ],
            1,
        ),
        (
            "DatabaseStorageLow",
            "FreeStorageSpace",
            [{"Name": "DBInstanceIdentifier", "Value": ref("Database")}],
            5 * 1024**3,
        ),
    ]:
        db = name == "DatabaseStorageLow"
        resource(
            name,
            "CloudWatch::Alarm",
            {
                "AlarmDescription": "Inspect application readiness and scoped operations; alarms do not replay jobs",
                "Namespace": "AWS/RDS" if db else "AWS/ApplicationELB",
                "MetricName": metric,
                "Dimensions": dimensions,
                "Statistic": "Minimum" if db else "Maximum",
                "Period": 60,
                "EvaluationPeriods": 2,
                "Threshold": threshold,
                "ComparisonOperator": (
                    "LessThanThreshold" if db else "GreaterThanOrEqualToThreshold"
                ),
                "TreatMissingData": "missing",
                "AlarmActions": {
                    "Fn::If": ["HasAlarmTopic", [ref("AlarmTopicArn")], []]
                },
            },
        )
    outputs = {
        "Url": sub("https://${Hostname}"),
        "LoadBalancerDns": get("LoadBalancer", "DNSName"),
        "LoadBalancerZoneId": get("LoadBalancer", "CanonicalHostedZoneID"),
        "Cluster": ref("Cluster"),
        "BootstrapTask": ref("BootstrapTask"),
        "ApiTask": ref("ApiTask"),
        "TaskSecurityGroup": ref("TaskGroup"),
        "ApiService": get("ApiService", "Name"),
        "WorkerService": get("WorkerService", "Name"),
        "UiService": get("UiService", "Name"),
        "DatabaseEndpoint": get("Database", "Endpoint.Address"),
        "DatabaseInstance": ref("Database"),
        "DatabaseAppSecret": ref("AppDatabaseSecret"),
        "ArtifactsBucket": ref("Artifacts"),
        "BackendRole": get("BackendRole"),
        "LogGroup": ref("Logs"),
    }
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Decision Model Workbench application: private PostgreSQL/S3, ECS Fargate API/worker/browser and HTTPS ingress. Review paid resources before execution.",
        "Parameters": parameters,
        "Conditions": conditions,
        "Rules": {
            "OidcNeedsUiSecret": {
                "RuleCondition": {"Fn::Equals": [ref("UiAuthMode"), "oidc"]},
                "Assertions": [
                    {
                        "Assert": {
                            "Fn::Not": [{"Fn::Equals": [ref("UiSecretArn"), ""]}]
                        },
                        "AssertDescription": "OIDC browser mode requires its separate UI secret",
                    }
                ],
            }
        },
        "Resources": resources,
        "Outputs": {k: {"Value": v} for k, v in outputs.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        help="New JSON template file; existing files are preserved",
    )
    args = parser.parse_args()
    body = json.dumps(template(), indent=2) + "\n"
    if len(body.encode()) > 51200:
        parser.error("Template exceeds the CloudFormation inline-body limit")
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(body)
    print(str(target))


if __name__ == "__main__":
    main()
