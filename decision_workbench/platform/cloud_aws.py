"""SageMaker/S3 boundary. Called only by the durable owner-approved cloud controller."""

import base64
import copy
import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from decision_workbench.training.artifacts import file_sha

TERMINAL = {"Completed", "Failed", "Stopped"}
STATUSES = TERMINAL | {"InProgress", "Stopping"}


class CloudUnavailable(RuntimeError):
    pass


def validate_profile(p):
    required = {
        "id",
        "provider",
        "tenants",
        "credential_prefix",
        "account_id",
        "region",
        "bucket",
        "prefix",
        "role_arn",
        "image_digest",
        "gpu",
        "volume_gb",
        "max_runtime_seconds",
        "max_runs_day",
        "max_inflight",
        "max_artifact_bytes",
        "source_sha256",
    }
    optional = {"vpc", "kms_key_arn"}
    if (
        not isinstance(p, dict)
        or not required <= set(p)
        or set(p) - required - optional
    ):
        raise ValueError("Invalid cloud profile fields")
    patterns = {
        "source_sha256": r"[a-f0-9]{64}",
        "id": r"[A-Za-z0-9_-]{1,64}",
        "account_id": r"[0-9]{12}",
        "region": r"[a-z]{2}-[a-z]+-[0-9]",
        "credential_prefix": r"DMT_CLOUD_[A-Z0-9_]{1,64}",
        "bucket": r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
        "prefix": r"[A-Za-z0-9_-]{1,80}",
        "gpu": r"ml\.(g[4567]|p[3456])[a-z0-9]*\.[a-z0-9]+",
    }
    if p["provider"] != "aws":
        raise ValueError("Only the AWS managed adapter is implemented")
    for key, pattern in patterns.items():
        if not isinstance(p[key], str) or not re.fullmatch(pattern, p[key]):
            raise ValueError("Invalid cloud resource identity: " + key)
    if not re.fullmatch(
        r"arn:aws:iam::" + p["account_id"] + r":role/[A-Za-z0-9_+=,.@/-]{1,512}",
        p["role_arn"],
    ):
        raise ValueError("Cloud role must belong to the approved AWS account")
    image = (
        re.escape(p["account_id"] + ".dkr.ecr." + p["region"] + ".amazonaws.com/")
        + r"[a-z0-9_./-]+@sha256:[a-f0-9]{64}"
    )
    if not isinstance(p["image_digest"], str) or not re.fullmatch(
        image, p["image_digest"]
    ):
        raise ValueError("Pin an ECR image digest in the approved account and region")
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("Cloud profile must name approved tenants")
    for key, low, high in (
        ("volume_gb", 20, 1024),
        ("max_runtime_seconds", 60, 14400),
        ("max_runs_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_artifact_bytes", 1024, 32 * 1024**3),
    ):
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError("Invalid cloud limit: " + key)
    if "kms_key_arn" in p and not re.fullmatch(
        r"arn:aws:kms:" + p["region"] + ":" + p["account_id"] + r":key/[a-f0-9-]{36}",
        p["kms_key_arn"],
    ):
        raise ValueError("KMS key must belong to the approved account and region")
    if "vpc" in p:
        vpc = p["vpc"]
        if not isinstance(vpc, dict) or set(vpc) != {"Subnets", "SecurityGroupIds"}:
            raise ValueError("Invalid cloud VPC fields")
        for key, stem in (("Subnets", "subnet"), ("SecurityGroupIds", "sg")):
            if (
                not isinstance(vpc[key], list)
                or not 1 <= len(vpc[key]) <= 5
                or any(
                    not isinstance(v, str)
                    or not re.fullmatch(stem + r"-[a-f0-9]{8,17}", v)
                    for v in vpc[key]
                )
            ):
                raise ValueError("Invalid cloud VPC identities")


def public_profile(p):
    return {
        k: copy.deepcopy(v)
        for k, v in p.items()
        if k not in ("credential_prefix", "tenants")
    }


def plan(run):
    p = run["profile"]
    name = "dmt-" + run["id"]
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid cloud run identity")
    root = p["prefix"] + "/" + run["id"]
    uri = "s3://" + p["bucket"] + "/" + root
    args = [
        "-m",
        "decision_workbench.training.cloud_runner",
        "--archive",
        "/opt/ml/input/data/study/study.zip",
        "--archive-sha256",
        run["archive_sha256"],
        "--request-sha256",
        run["request_sha256"],
        "--work",
        "/tmp/domain-study",
        "--out",
        "/opt/ml/model",
    ]
    result = {
        "TrainingJobName": name,
        "RoleArn": p["role_arn"],
        "AlgorithmSpecification": {
            "TrainingImage": p["image_digest"],
            "TrainingInputMode": "File",
            "ContainerEntrypoint": ["python"],
            "ContainerArguments": args,
        },
        "InputDataConfig": [
            {
                "ChannelName": "study",
                "CompressionType": "None",
                "ContentType": "application/zip",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": uri + "/inputs/",
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
            }
        ],
        "OutputDataConfig": {
            "S3OutputPath": uri + "/outputs/",
            "CompressionType": "GZIP",
        },
        "ResourceConfig": {
            "InstanceType": p["gpu"],
            "InstanceCount": 1,
            "VolumeSizeInGB": p["volume_gb"],
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": p["max_runtime_seconds"]},
        "EnableManagedSpotTraining": False,
        "EnableInterContainerTrafficEncryption": True,
        "EnableNetworkIsolation": False,
        "ProfilerConfig": {"DisableProfiler": True},
        "RemoteDebugConfig": {"EnableRemoteDebug": False},
    }
    if "vpc" in p:
        result["VpcConfig"] = copy.deepcopy(p["vpc"])
    if "kms_key_arn" in p:
        result["OutputDataConfig"]["KmsKeyId"] = p["kms_key_arn"]
        result["ResourceConfig"]["VolumeKmsKeyId"] = p["kms_key_arn"]
    return result


class AwsGateway:
    def __init__(self, profile, sagemaker, s3, sts):
        self.profile, self.sagemaker, self.s3, self.sts = profile, sagemaker, s3, sts

    @classmethod
    def from_operator(cls, profile):
        import boto3
        from botocore.config import Config

        validate_profile(profile)
        prefix = profile["credential_prefix"]
        access = os.environ.get(prefix + "_ACCESS_KEY_ID")
        secret = os.environ.get(prefix + "_SECRET_ACCESS_KEY")
        token = os.environ.get(prefix + "_SESSION_TOKEN")
        if not access or not secret:
            raise CloudUnavailable("Selected cloud credentials are unavailable")
        options = dict(
            region_name=profile["region"],
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            aws_session_token=token,
            config=Config(
                connect_timeout=5,
                read_timeout=20,
                retries={"total_max_attempts": 1, "mode": "standard"},
                ignore_configured_endpoint_urls=True,
            ),
        )
        return cls(
            profile,
            boto3.client("sagemaker", **options),
            boto3.client("s3", **options),
            boto3.client("sts", **options),
        )

    def close(self):
        for client in (self.sagemaker, self.s3, self.sts):
            client.close()

    def identity(self):
        value = self.sts.get_caller_identity()
        if value.get("Account") != self.profile["account_id"]:
            raise CloudUnavailable("Cloud credentials belong to a different account")
        return {"account_id": value["Account"], "region": self.profile["region"]}

    def upload(self, run, archive):
        p = run["profile"]
        if file_sha(archive) != run["archive_sha256"]:
            raise ValueError("Cloud input archive differs from the frozen study")
        checksum = base64.b64encode(bytes.fromhex(run["archive_sha256"])).decode()
        args = {
            "Bucket": p["bucket"],
            "Key": p["prefix"] + "/" + run["id"] + "/inputs/study.zip",
            "ExpectedBucketOwner": p["account_id"],
            "IfNoneMatch": "*",
            "ChecksumSHA256": checksum,
            "ContentType": "application/zip",
            "ServerSideEncryption": "AES256",
        }
        if "kms_key_arn" in p:
            args.update(ServerSideEncryption="aws:kms", SSEKMSKeyId=p["kms_key_arn"])
        with Path(archive).open("rb") as body:
            response = self.s3.put_object(**args, Body=body)
        if response.get("ChecksumSHA256") != checksum:
            raise CloudUnavailable("Cloud input checksum confirmation is missing")
        return {
            "checksum_sha256": run["archive_sha256"],
            "version_id": response.get("VersionId"),
        }

    def create(self, run):
        response = self.sagemaker.create_training_job(**plan(run))
        if response.get("TrainingJobArn") != self.arn(run):
            raise CloudUnavailable("Cloud create response identity differs")
        return {"provider_job_id": response["TrainingJobArn"]}

    @staticmethod
    def arn(run):
        p = run["profile"]
        return (
            "arn:aws:sagemaker:"
            + p["region"]
            + ":"
            + p["account_id"]
            + ":training-job/dmt-"
            + run["id"]
        )

    def describe(self, run):
        response = self.sagemaker.describe_training_job(
            TrainingJobName="dmt-" + run["id"]
        )
        expected = plan(run)
        if (
            response.get("TrainingJobArn") != self.arn(run)
            or response.get("TrainingJobName") != expected["TrainingJobName"]
            or response.get("TrainingJobStatus") not in STATUSES
        ):
            raise CloudUnavailable("Cloud observation identity/status differs")
        for key in (
            "RoleArn",
            "AlgorithmSpecification",
            "InputDataConfig",
            "OutputDataConfig",
            "ResourceConfig",
            "StoppingCondition",
            "EnableManagedSpotTraining",
            "EnableNetworkIsolation",
            "EnableInterContainerTrafficEncryption",
            "VpcConfig",
        ):
            # Compare supplied fields; additional provider defaults may be reported.
            if key in expected:
                if not subset(expected[key], response.get(key)):
                    raise CloudUnavailable(
                        "Cloud observation resource configuration differs"
                    )
            elif key == "VpcConfig" and response.get(key):
                raise CloudUnavailable(
                    "Cloud observation network configuration differs"
                )
        if (
            response.get("RetryStrategy")
            or response.get("Environment")
            or response.get("HyperParameters")
        ):
            raise CloudUnavailable(
                "Cloud observation contains an unapproved retry/environment policy"
            )
        resource = response["ResourceConfig"]
        if (
            resource.get("KeepAlivePeriodInSeconds", 0) != 0
            or any(
                resource.get(k)
                for k in (
                    "InstanceGroups",
                    "TrainingPlanArn",
                    "InstancePlacementConfig",
                    "InstancePreferences",
                )
            )
            or response.get("WarmPoolStatus", {}).get("Status")
            in ("Available", "InUse")
        ):
            raise CloudUnavailable(
                "Cloud observation contains unapproved retained/distributed resources"
            )
        status = response["TrainingJobStatus"]
        uri = response.get("ModelArtifacts", {}).get("S3ModelArtifacts")
        if status == "Completed":
            artifact_location(run, uri)
        else:
            uri = None
        times = {}
        for key in ("TrainingTimeInSeconds", "BillableTimeInSeconds"):
            value = response.get(key)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise CloudUnavailable("Invalid cloud timing evidence")
                times[key] = value
        return {
            "provider_job_id": self.arn(run),
            "status": status,
            "artifact_uri": uri,
            "timing": times,
            "verification": "AWS SDK control-plane observation; not supply-chain or domain acceptance",
        }

    def stop(self, run):
        self.sagemaker.stop_training_job(TrainingJobName="dmt-" + run["id"])
        return {"stop_requested": True, "terminal_observed": False}

    def download(self, run, uri, destination):
        p = run["profile"]
        bucket, key = artifact_location(run, uri)
        args = dict(Bucket=bucket, Key=key, ExpectedBucketOwner=p["account_id"])
        head = self.s3.head_object(**args)
        size = head.get("ContentLength")
        if type(size) is not int or not 1 <= size <= p["max_artifact_bytes"]:
            raise CloudUnavailable("Cloud artifact exceeds its transfer limit")
        version = head.get("VersionId")
        if version and version != "null":
            args["VersionId"] = version
        elif isinstance(head.get("ETag"), str) and head["ETag"]:
            args["IfMatch"] = head["ETag"]
        else:
            raise CloudUnavailable("Cloud artifact has no stable retrieval identity")
        response = self.s3.get_object(**args)
        reader = response["Body"]
        try:
            if response.get("ContentLength") != size:
                raise CloudUnavailable("Cloud artifact size changed")
            deadline = time.monotonic() + 600
            total, checksum = 0, hashlib.sha256()
            with Path(destination).open("xb") as writer:
                while chunk := reader.read(1024**2):
                    total += len(chunk)
                    if total > size or time.monotonic() > deadline:
                        raise CloudUnavailable(
                            "Cloud artifact transfer exceeded its byte/time bound"
                        )
                    checksum.update(chunk)
                    writer.write(chunk)
            if total != size:
                raise CloudUnavailable("Cloud artifact transfer was truncated")
            return {
                "bytes": total,
                "archive_sha256": checksum.hexdigest(),
                "version_id": version,
                "etag": head.get("ETag"),
            }
        finally:
            reader.close()


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(subset(a, b) for a, b in zip(expected, actual))
        )
    return type(expected) is type(actual) and expected == actual


def artifact_location(run, uri):
    if not isinstance(uri, str) or len(uri) > 2048:
        raise CloudUnavailable("Cloud artifact URI is missing or invalid")
    parsed = urlsplit(uri)
    p = run["profile"]
    expected_key = (
        p["prefix"]
        + "/"
        + run["id"]
        + "/outputs/dmt-"
        + run["id"]
        + "/output/model.tar.gz"
    )
    if (
        parsed.scheme != "s3"
        or parsed.netloc != p["bucket"]
        or parsed.path != "/" + expected_key
        or parsed.query
        or parsed.fragment
    ):
        raise CloudUnavailable("Cloud artifact URI is outside the approved job output")
    return p["bucket"], expected_key
