"""Offline installed-SDK serialization seam; network stubs fail closed."""

import json
import sys
from types import SimpleNamespace


def main():
    import modal
    from modal import types
    from modal._utils.async_utils import synchronizer
    from modal_proto import api_pb2 as api
    from google.protobuf.json_format import MessageToDict
    from decision_workbench.platform.modal_transport import execute
    from decision_workbench.platform.cloud_modal import tags

    data = json.loads(sys.stdin.buffer.read())
    p, run = data["profile"], data["body"]["run"]
    identifier = "sb-" + "F" * 22
    requests = []

    class Stub:
        def __getattr__(self, key):
            raise AssertionError("Unexpected SDK RPC: " + key)

        async def WorkspaceNameLookup(self, request, **kw):
            return api.WorkspaceNameLookupResponse(username=p["account_id"])

        async def AppGetOrCreate(self, request, **kw):
            assert not request.object_creation_type
            assert request.environment_name == p["environment"]
            return api.AppGetOrCreateResponse(app_id=p["app_id"])

        async def VolumeGetOrCreate(self, request, **kw):
            assert not request.object_creation_type
            assert request.environment_name == p["environment"]
            return api.VolumeGetOrCreateResponse(
                volume_id=p["volume_id"],
                metadata=api.VolumeMetadata(
                    name=p["volume_name"], version=api.VOLUME_FS_VERSION_V1
                ),
            )

        async def SandboxGetFromName(self, request, **kw):
            raise modal.exception.NotFoundError("Absent fixture")

        async def ImageFromId(self, request, **kw):
            assert request.image_id == p["modal_image_id"]
            return api.ImageFromIdResponse(image_id=p["modal_image_id"])

        async def SecretGetOrCreate(self, request, **kw):
            assert dict(request.env_dict) == {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
            return api.SecretGetOrCreateResponse(secret_id="st-fixture")

        async def SandboxCreate(self, request, **kw):
            requests.append(MessageToDict(request, preserving_proto_field_name=True))
            return api.SandboxCreateResponse(sandbox_id=identifier)

        async def SandboxGetTaskId(self, request, **kw):
            return api.SandboxGetTaskIdResponse(task_id="ta-fixture")

        async def SandboxTagsGet(self, request, **kw):
            return api.SandboxTagsGetResponse(
                tags=[
                    api.SandboxTag(tag_name=k, tag_value=v)
                    for k, v in tags(run).items()
                ]
            )

    client = modal.Client(
        "https://api.modal.com",
        api.CLIENT_TYPE_CLIENT,
        ("fixture-id", "fixture-secret"),
    )
    inner = synchronizer._translate_in(client)
    inner._control_plane_stub = Stub()
    modal.Client.from_credentials = lambda *creds: client
    modal.types = types
    result = execute(modal, p, data["credentials"], "create", data["body"])
    print(json.dumps({"result": {"receipt": result, "requests": requests}}))


if __name__ == "__main__":
    main()
