"""Explicit managed-provider dispatch; profiles cannot select arbitrary endpoints/code."""

from . import cloud_aws


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("Invalid cloud profile")
    if profile.get("provider") == "aws":
        return cloud_aws.validate_profile(profile)
    if profile.get("provider") == "gcp":
        from . import cloud_gcp

        return cloud_gcp.validate_profile(profile)
    if profile.get("provider") == "azure":
        from . import cloud_azure

        return cloud_azure.validate_profile(profile)
    if profile.get("provider") == "runpod":
        from . import cloud_runpod

        return cloud_runpod.validate_profile(profile)
    if profile.get("provider") == "modal":
        from . import cloud_modal

        return cloud_modal.validate_profile(profile)
    raise ValueError("Managed controls support AWS, Azure, GCP, RunPod and Modal")


def gateway_from_operator(profile):
    validate_profile(profile)
    if profile["provider"] == "aws":
        return cloud_aws.AwsGateway.from_operator(profile)
    if profile["provider"] == "azure":
        from .cloud_azure import AzureGateway

        return AzureGateway.from_operator(profile)
    if profile["provider"] == "runpod":
        from .cloud_runpod import RunPodGateway

        return RunPodGateway.from_operator(profile)
    if profile["provider"] == "modal":
        from .cloud_modal import ModalGateway

        return ModalGateway.from_operator(profile)
    from .cloud_gcp import GcpGateway

    return GcpGateway.from_operator(profile)


def valid_job_id(run, identifier):
    if run["profile"]["provider"] == "aws":
        return identifier == cloud_aws.AwsGateway.arn(run)
    if run["profile"]["provider"] == "azure":
        from .cloud_azure import valid_job_id as valid

        return valid(run, identifier)
    if run["profile"]["provider"] == "runpod":
        from .cloud_runpod import valid_job_id as valid

        return valid(run, identifier)
    if run["profile"]["provider"] == "modal":
        from .cloud_modal import valid_job_id as valid

        return valid(run, identifier)
    from .cloud_gcp import valid_job_id as valid

    return valid(run, identifier)


def artifact_location(run, uri):
    if run["profile"]["provider"] == "aws":
        return cloud_aws.artifact_location(run, uri)
    if run["profile"]["provider"] == "azure":
        from .cloud_azure import artifact_location as location

        return location(run, uri)
    if run["profile"]["provider"] == "runpod":
        from .cloud_runpod import artifact_location as location

        return location(run, uri)
    if run["profile"]["provider"] == "modal":
        from .cloud_modal import artifact_location as location

        return location(run, uri)
    from .cloud_gcp import artifact_location as location

    return location(run, uri)
