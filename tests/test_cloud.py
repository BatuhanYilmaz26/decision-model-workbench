"""Actual AWS SDK serialization with stubbed responses; no AWS or paid compute calls."""

import base64
import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch
import boto3
from botocore.config import Config
from botocore.stub import Stubber, ANY
from decision_workbench.platform.cloud import validate_history
from decision_workbench.platform.cloud_aws import (
    AwsGateway,
    CloudUnavailable,
    artifact_location,
    plan,
    validate_profile,
)
from decision_workbench.platform.cloud_archive import extract_model
from decision_workbench.platform.store import Conflict
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.cloud_runner import prepare_archive, trainer_command
from decision_workbench.training.provenance import source_fingerprint
import test_remote_admission as remote_fixtures

OWNER = remote_fixtures.OWNER


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.f = remote_fixtures.RemoteAdmissionTests("runTest")
        self.f.setUp()
        self.p = {
            "id": "aws-fixture",
            "provider": "aws",
            "tenants": ["A"],
            "credential_prefix": "DMT_CLOUD_AWS",
            "account_id": "123456789012",
            "region": "eu-west-1",
            "bucket": "dmt-fixture",
            "prefix": "decision-runs",
            "role_arn": "arn:aws:iam::123456789012:role/dmt-trainer",
            "image_digest": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/dmt/trainer@sha256:"
            + "a" * 64,
            "gpu": "ml.g5.xlarge",
            "volume_gb": 100,
            "max_runtime_seconds": 7200,
            "max_runs_day": 2,
            "max_inflight": 1,
            "max_artifact_bytes": 1024**2,
            "source_sha256": source_fingerprint(),
        }
        self.profiles_file = self.f.root / "profiles.json"
        self.write_profiles()
        self.env = patch.dict(
            os.environ, {"DMT_CLOUD_PROFILES_FILE": str(self.profiles_file)}
        )
        self.env.start()
        self.f.config.update(
            {k: self.p[k] for k in ("provider", "region", "gpu", "image_digest")}
        )
        self.study = self.f.study()
        self.cloud = self.f.service.cloud
        config = Config(
            connect_timeout=1, read_timeout=1, retries={"total_max_attempts": 1}
        )
        self.clients = {
            s: boto3.client(
                s,
                region_name=self.p["region"],
                aws_access_key_id="fixture-key",
                aws_secret_access_key="fixture-secret",
                config=config,
            )
            for s in ("sagemaker", "s3", "sts")
        }
        self.stubs = {s: Stubber(c) for s, c in self.clients.items()}
        for stub in self.stubs.values():
            stub.activate()
        self.gateway = AwsGateway(self.p, **self.clients)
        self.f.worker.service.cloud.gateway_factory = lambda p: self.gateway

    def write_profiles(self):
        self.profiles_file.write_text(
            json.dumps({"format": "domain-cloud-profiles-v1", "profiles": [self.p]})
        )

    def tearDown(self):
        for stub in self.stubs.values():
            stub.deactivate()
        for client in self.clients.values():
            client.close()
        self.env.stop()
        self.f.tearDown()

    def create(self):
        return self.cloud.create(
            OWNER,
            self.f.project,
            self.study["id"],
            self.p["id"],
            True,
            True,
            "approve-cloud-once",
        )

    def identity(self):
        self.stubs["sts"].add_response(
            "get_caller_identity",
            {
                "Account": self.p["account_id"],
                "Arn": "arn:aws:iam::123456789012:user/fixture",
                "UserId": "fixture",
            },
            {},
        )

    def expect_submission(self, run, unknown=False):
        self.identity()
        checksum = base64.b64encode(
            bytes.fromhex(run["payload"]["archive_sha256"])
        ).decode()
        params = {
            "Bucket": self.p["bucket"],
            "Key": self.p["prefix"] + "/" + run["id"] + "/inputs/study.zip",
            "ExpectedBucketOwner": self.p["account_id"],
            "IfNoneMatch": "*",
            "ChecksumSHA256": checksum,
            "ContentType": "application/zip",
            "ServerSideEncryption": "AES256",
            "Body": ANY,
        }
        self.stubs["s3"].add_response(
            "put_object",
            {"ChecksumSHA256": checksum, "VersionId": "fixture-version"},
            params,
        )
        if unknown:
            self.stubs["sagemaker"].add_client_error(
                "create_training_job",
                service_error_code="InternalFailure",
                service_message="fictional ambiguous create",
                expected_params=plan(run["payload"]),
            )
        else:
            self.stubs["sagemaker"].add_response(
                "create_training_job",
                {"TrainingJobArn": self.gateway.arn(run["payload"])},
                plan(run["payload"]),
            )

    def start(self, run, unknown=False):
        self.expect_submission(run, unknown)
        return self.action(run, "submit")

    def action(self, run, action):
        current = self.cloud.get(OWNER, self.f.project, run["id"])
        job = self.cloud.queue(
            OWNER,
            self.f.project,
            run["id"],
            action,
            current["state"]["revision"],
            "action-" + uuid.uuid4().hex,
        )
        self.f.worker.once()
        return self.f.store.job(job["id"], "A", self.f.project)

    def description(self, run, status="Completed"):
        response = copy.deepcopy(plan(run["payload"]))
        for key in ("ProfilerConfig", "RemoteDebugConfig"):
            response.pop(key)
        uri = (
            "s3://"
            + self.p["bucket"]
            + "/"
            + self.p["prefix"]
            + "/"
            + run["id"]
            + "/outputs/dmt-"
            + run["id"]
            + "/output/model.tar.gz"
        )
        response.update(
            TrainingJobArn=self.gateway.arn(run["payload"]),
            TrainingJobStatus=status,
            SecondaryStatus="Completed" if status == "Completed" else "Training",
            CreationTime=datetime.now(timezone.utc),
            LastModifiedTime=datetime.now(timezone.utc),
            ModelArtifacts={"S3ModelArtifacts": uri},
            TrainingTimeInSeconds=45,
            BillableTimeInSeconds=48,
        )
        return response

    def observe(self, run, status="Completed"):
        self.identity()
        self.stubs["sagemaker"].add_response(
            "describe_training_job",
            self.description(run, status),
            {"TrainingJobName": "dmt-" + run["id"]},
        )
        return self.action(run, "observe")

    def test_sdk_creation_immutable_attempts_observation_and_no_replay(self):
        run = self.create()
        self.assertEqual(self.create()["id"], run["id"])
        result = self.start(run)
        self.assertEqual(result["status"], "succeeded", result["error"])
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, run["id"])["state"]["phase"],
            "submitted",
        )
        result = self.action(run, "submit")
        self.assertEqual(result["status"], "failed")
        self.assertIn("never replay", result["error"])
        result = self.observe(run)
        self.assertEqual(result["status"], "succeeded", result["error"])
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertEqual(state["phase"], "Completed")
        self.assertEqual(state["timing"]["BillableTimeInSeconds"], 48)
        validate_history(self.f.store)
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()

    def test_ambiguous_create_holds_capacity_and_never_replays(self):
        run = self.create()
        result = self.start(run, unknown=True)
        self.assertEqual(result["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertTrue(state["create_attempted"])
        self.assertEqual(self.action(run, "submit")["status"], "failed")
        self.assertEqual(self.action(run, "abandon")["status"], "failed")
        job = self.f.service.submit(
            OWNER, self.f.project, "remote-study", self.f.config, "second-study"
        )
        self.f.worker.once()
        with self.assertRaises(Conflict):
            self.cloud.create(
                OWNER,
                self.f.project,
                job["id"],
                self.p["id"],
                True,
                True,
                "second-approval",
            )
        self.assertEqual(self.observe(run, "InProgress")["status"], "succeeded")
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, run["id"])["state"]["phase"],
            "InProgress",
        )
        validate_history(self.f.store)

    def test_stop_is_identity_bound_and_capacity_requires_terminal_observation(self):
        run = self.create()
        self.assertEqual(self.start(run)["status"], "succeeded")
        self.identity()
        self.stubs["sagemaker"].add_response(
            "describe_training_job",
            self.description(run, "InProgress"),
            {"TrainingJobName": "dmt-" + run["id"]},
        )
        self.stubs["sagemaker"].add_response(
            "stop_training_job", {}, {"TrainingJobName": "dmt-" + run["id"]}
        )
        self.assertEqual(self.action(run, "stop")["status"], "succeeded")
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, run["id"])["state"]["phase"],
            "stop_requested",
        )
        self.assertEqual(self.observe(run, "Stopped")["status"], "succeeded")
        validate_history(self.f.store)

    def test_scope_roles_consents_profile_drift_and_stale_revision(self):
        url = f"/v1/projects/{self.f.project}/cloud-runs"
        body = {
            "study": self.study["id"],
            "profile_id": self.p["id"],
            "data_approved": True,
            "compute_approved": False,
            "request_key": "cloud-approval",
        }
        self.assertEqual(self.f.client.post(url, json=body).status_code, 400)
        body["compute_approved"] = True
        for token in ("review-secret", "infer-secret"):
            self.assertEqual(
                self.f.client.post(
                    url, json=body, headers={"Authorization": "Bearer " + token}
                ).status_code,
                403,
            )
        self.assertEqual(
            self.f.client.post(
                url, json=body, headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        response = self.f.client.post(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertNotIn("credential_prefix", response.text)
        self.assertNotIn("tenants", response.text)
        self.assertEqual(self.f.client.get(url).json()[0]["id"], run["id"])
        self.assertEqual(
            self.f.client.get(
                url + "/" + run["id"], headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        self.cloud.event(OWNER, self.f.project, run["id"], 1, "test-cas", {})
        with self.assertRaises(Conflict):
            self.cloud.queue(
                OWNER, self.f.project, run["id"], "submit", 1, "stale-action"
            )
        self.p["gpu"] = "ml.g5.2xlarge"
        self.write_profiles()
        with self.assertRaises(CloudUnavailable):
            self.cloud.queue(
                OWNER, self.f.project, run["id"], "submit", 2, "changed-profile"
            )

    def test_observation_rejects_resource_and_artifact_identity_drift(self):
        run = self.create()
        for change in (
            lambda r: r.update(
                TrainingJobArn=r["TrainingJobArn"].replace(
                    "123456789012", "999999999999"
                )
            ),
            lambda r: r["ResourceConfig"].update(InstanceCount=2),
            lambda r: r["ResourceConfig"].update(KeepAlivePeriodInSeconds=600),
            lambda r: r["ModelArtifacts"].update(
                S3ModelArtifacts="s3://foreign-bucket/model.tar.gz"
            ),
        ):
            response = self.description(run)
            change(response)
            self.stubs["sagemaker"].add_response(
                "describe_training_job",
                response,
                {"TrainingJobName": "dmt-" + run["id"]},
            )
            with self.assertRaises(CloudUnavailable):
                self.gateway.describe(run["payload"])
        for uri in (
            "https://evil.example/model.tar.gz",
            "s3://dmt-fixture/x/../model.tar.gz",
            self.description(run)["ModelArtifacts"]["S3ModelArtifacts"]
            + "?versionId=x",
        ):
            with self.assertRaises(CloudUnavailable):
                artifact_location(run["payload"], uri)

    def prepare_return(self):
        run = self.create()
        self.assertEqual(self.start(run)["status"], "succeeded")
        self.assertEqual(self.observe(run)["status"], "succeeded")
        folder, checksum, receipt = self.f.export(self.study)
        archive = self.f.root / "returned.tar.gz"
        with tarfile.open(archive, "w:gz", format=tarfile.USTAR_FORMAT) as out:
            for file in sorted(folder.rglob("*")):
                if file.is_file():
                    out.add(
                        file,
                        arcname=file.relative_to(folder).as_posix(),
                        recursive=False,
                    )
        payload = archive.read_bytes()
        self.identity()
        description = self.description(run)
        self.stubs["sagemaker"].add_response(
            "describe_training_job",
            description,
            {"TrainingJobName": "dmt-" + run["id"]},
        )
        bucket, key = artifact_location(
            run["payload"], description["ModelArtifacts"]["S3ModelArtifacts"]
        )
        params = {
            "Bucket": bucket,
            "Key": key,
            "ExpectedBucketOwner": self.p["account_id"],
        }
        self.stubs["s3"].add_response(
            "head_object",
            {
                "ContentLength": len(payload),
                "VersionId": "export-v1",
                "ETag": "fixture-etag",
            },
            params,
        )
        self.stubs["s3"].add_response(
            "get_object",
            {"ContentLength": len(payload), "Body": io.BytesIO(payload)},
            {**params, "VersionId": "export-v1"},
        )
        return run, checksum

    def test_bounded_download_safe_extract_and_actual_admission_queue(self):
        run, checksum = self.prepare_return()
        result = self.action(run, "retrieve")
        self.assertEqual(result["status"], "succeeded", result["error"])
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        job = self.f.store.job(state["admission_job"], "A", self.f.project)
        self.assertEqual(job["kind"], "remote-import")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["config"]["manifest_sha256"], checksum)
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])
        self.assertIsNone(self.f.store.active("A", self.f.project)["model_id"])
        validate_history(self.f.store)

    def test_interrupted_final_retrieval_event_recovers_existing_admission_without_download(
        self,
    ):
        run, checksum = self.prepare_return()
        controller = self.f.worker.service.cloud
        original = controller.event

        def cancel_before_final(
            principal, project, identifier, revision, action, changes, job=None
        ):
            if action == "retrieve":
                self.f.store.cancel(job["id"], "A", self.f.project, "owner")
            return original(
                principal, project, identifier, revision, action, changes, job
            )

        with patch.object(controller, "event", cancel_before_final):
            result = self.action(run, "retrieve")
        self.assertEqual(result["status"], "cancelled")
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertEqual(state["phase"], "Completed")
        self.assertNotIn("admission_job", state)
        admission = next(
            j
            for j in self.f.store.jobs("A", self.f.project)
            if j["kind"] == "remote-import"
        )
        # Move its timestamp after the recovery control so the fixture never loads fake model weights.
        with self.f.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET created=created+86400 WHERE id=?", (admission["id"],)
            )
        self.identity()
        recovered = self.action(run, "retrieve")
        self.assertEqual(recovered["status"], "succeeded", recovered["error"])
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertEqual(state["admission_job"], admission["id"])
        self.assertEqual(state["manifest_sha256"], checksum)
        self.assertEqual(
            len(
                [
                    j
                    for j in self.f.store.jobs("A", self.f.project)
                    if j["kind"] == "remote-import"
                ]
            ),
            1,
        )
        validate_history(self.f.store)
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()

    def test_download_truncation_and_oversize_refused_and_stream_closed(self):
        run = self.create()
        uri = self.description(run)["ModelArtifacts"]["S3ModelArtifacts"]
        bucket, key = artifact_location(run["payload"], uri)
        params = {
            "Bucket": bucket,
            "Key": key,
            "ExpectedBucketOwner": self.p["account_id"],
        }
        self.stubs["s3"].add_response(
            "head_object", {"ContentLength": self.p["max_artifact_bytes"] + 1}, params
        )
        with self.assertRaises(CloudUnavailable):
            self.gateway.download(run["payload"], uri, self.f.root / "oversized.tar.gz")
        self.assertFalse((self.f.root / "oversized.tar.gz").exists())
        self.stubs["s3"].add_response(
            "head_object", {"ContentLength": 10, "ETag": "fictional-etag"}, params
        )
        reader = io.BytesIO(b"short")
        self.stubs["s3"].add_response(
            "get_object",
            {"ContentLength": 10, "Body": reader},
            {**params, "IfMatch": "fictional-etag"},
        )
        with self.assertRaises(CloudUnavailable):
            self.gateway.download(run["payload"], uri, self.f.root / "partial.tar.gz")
        self.assertTrue(reader.closed)
        self.assertEqual((self.f.root / "partial.tar.gz").read_bytes(), b"short")

    def test_frozen_runner_real_process_preflight_and_backend_arguments(self):
        payload = self.study["payload"]
        archive = (
            self.f.store.folder / "jobs" / self.study["id"] / "remote-study/study.zip"
        )
        work = self.f.root / "cloud-input"
        request = prepare_archive(
            archive, work, payload["archive_sha256"], payload["request_sha256"]
        )
        command = trainer_command(request, work, self.f.root / "export")
        self.assertIn("--study-request", command)
        nullable = copy.deepcopy(request)
        nullable["configuration"]["max_length"] = None
        self.assertNotIn(
            "--max-length", trainer_command(nullable, work, self.f.root / "export")
        )
        self.assertEqual(command[-2:], ["--device", "cuda"])
        altered = copy.deepcopy(request)
        altered["configuration"]["backend"] = "unsloth"
        altered["configuration"]["rank"] = 16
        self.assertIn("--rank", trainer_command(altered, work, self.f.root / "export"))
        altered["configuration"]["epochs"] = True
        with self.assertRaises(ValueError):
            trainer_command(altered, work, self.f.root / "export")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.training.cloud_runner",
                "--archive",
                str(archive),
                "--archive-sha256",
                payload["archive_sha256"],
                "--request-sha256",
                payload["request_sha256"],
                "--work",
                str(self.f.root / "child-input"),
                "--out",
                str(self.f.root / "child-output"),
                "--check-only",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["training_executed"])

    def test_runner_refuses_rehashed_package_and_data_and_zip_entries(self):
        original = (
            self.f.store.folder / "jobs" / self.study["id"] / "remote-study/study.zip"
        )
        with zipfile.ZipFile(original) as source:
            members = {n: source.read(n) for n in source.namelist()}
        for index, what in enumerate(("package", "data", "extra", "compression")):
            changed = copy.deepcopy(members)
            request = json.loads(changed["request.json"])
            if what == "package":
                request["source_sha256"] = "b" * 64
            elif what == "data":
                changed["data/train.jsonl"] = changed["data/train.jsonl"].replace(
                    b"sourcecase", b"other-case"
                )
            elif what == "extra":
                changed["../escape.py"] = b"bad"
            changed["request.json"] = json.dumps(request).encode()
            candidate = self.f.root / f"bad-{index}.zip"
            with zipfile.ZipFile(
                candidate,
                "w",
                compression=(
                    zipfile.ZIP_DEFLATED
                    if what == "compression"
                    else zipfile.ZIP_STORED
                ),
            ) as out:
                for name, body in changed.items():
                    out.writestr(name, body)
            destination = self.f.root / f"bad-work-{index}"
            import hashlib

            with self.assertRaises(ValueError):
                prepare_archive(
                    candidate,
                    destination,
                    file_sha(candidate),
                    hashlib.sha256(changed["request.json"]).hexdigest(),
                )
            self.assertFalse(destination.exists())

    def test_archive_refuses_links_traversal_duplicates_expansion_and_pax(self):
        for index, what in enumerate(
            ("link", "traversal", "duplicate", "expansion", "pax")
        ):
            archive = self.f.root / f"unsafe-{index}.tar.gz"
            with tarfile.open(
                archive,
                "w:gz",
                format=tarfile.PAX_FORMAT if what == "pax" else tarfile.USTAR_FORMAT,
            ) as out:
                if what == "pax":
                    item = tarfile.TarInfo("manifest.json")
                    item.pax_headers = {"comment": "untrusted"}
                    out.addfile(item, io.BytesIO())
                elif what == "link":
                    item = tarfile.TarInfo("link")
                    item.type = tarfile.SYMTYPE
                    item.linkname = "../outside"
                    out.addfile(item)
                else:
                    names = (
                        ["../outside.json"]
                        if what == "traversal"
                        else (
                            ["manifest.json", "manifest.json"]
                            if what == "duplicate"
                            else ["manifest.json"]
                        )
                    )
                    for name in names:
                        item = tarfile.TarInfo(name)
                        item.size = 100 if what == "expansion" else 2
                        out.addfile(item, io.BytesIO(b"x" * item.size))
            destination = self.f.root / f"bad-export-{index}"
            with self.assertRaises(ValueError):
                extract_model(archive, destination, 32)
            self.assertFalse(destination.exists())
        self.assertFalse((self.f.root / "outside.json").exists())

    def test_backup_restore_preserves_unknown_external_capacity_and_history_tamper_refusal(
        self,
    ):
        run = self.create()
        self.start(run, unknown=True)
        backup = self.f.root / "backup"
        restored = self.f.root / "restored"
        backup_workspace(self.f.store.folder, backup)
        restore_workspace(backup, restored, file_sha(backup / "backup-manifest.json"))
        from decision_workbench.platform.store import Store
        from decision_workbench.platform.cloud import Cloud

        recovered = Store(restored)
        state = Cloud(recovered).get(OWNER, self.f.project, run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertTrue(state["create_attempted"])
        validate_history(recovered)
        with recovered.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=json_set(payload,'$.previous_sha256','forged') WHERE kind='cloud-event' AND json_extract(payload,'$.revision')=2"
            )
        with self.assertRaises(ValueError):
            validate_history(recovered)

    def test_cancel_local_control_after_create_retains_unknown_cloud_compute(self):
        run = self.create()
        original = self.gateway.create

        def created_then_cancelled(value):
            result = original(value)
            job = next(
                j
                for j in self.f.store.jobs("A", self.f.project)
                if j["kind"] == "cloud-control" and j["status"] == "running"
            )
            self.f.store.cancel(job["id"], "A", self.f.project, "owner")
            return result

        with patch.object(self.gateway, "create", created_then_cancelled):
            result = self.start(run)
        self.assertEqual(result["status"], "cancelled")
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertTrue(state["create_attempted"])
        self.assertEqual(state["phase"], "submitting")
        self.assertEqual(self.action(run, "submit")["status"], "failed")
        self.assertEqual(self.observe(run, "InProgress")["status"], "succeeded")
        validate_history(self.f.store)

    def test_upload_unknown_cannot_submit_again_but_precompute_abandon_is_safe(self):
        run = self.create()
        self.identity()
        self.stubs["s3"].add_client_error(
            "put_object",
            service_error_code="InternalFailure",
            service_message="fictional uncertain upload",
        )
        result = self.action(run, "submit")
        self.assertEqual(result["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, run["id"])["state"]
        self.assertTrue(state["upload_attempted"])
        self.assertFalse(state["create_attempted"])
        self.assertEqual(state["phase"], "unknown")
        self.assertEqual(self.action(run, "submit")["status"], "failed")
        self.assertEqual(self.action(run, "abandon")["status"], "succeeded")
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, run["id"])["state"]["phase"],
            "abandoned",
        )
        validate_history(self.f.store)

    def test_competing_approvals_and_actions_preserve_one_logical_run(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.create(), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(self.f.store.list("A", self.f.project, "cloud-run")), 1)
        self.assertEqual(results[0]["state"]["revision"], 1)
        run = results[0]
        first = self.cloud.queue(
            OWNER, self.f.project, run["id"], "abandon", 1, "race-abandon-001"
        )
        replay = self.cloud.queue(
            OWNER, self.f.project, run["id"], "abandon", 1, "race-abandon-001"
        )
        self.assertEqual(first["id"], replay["id"])
        second = self.cloud.queue(
            OWNER, self.f.project, run["id"], "submit", 1, "race-submit-001"
        )
        self.f.worker.once()
        self.f.worker.once()
        self.assertEqual(
            self.f.store.job(first["id"], "A", self.f.project)["status"], "succeeded"
        )
        self.assertEqual(
            self.f.store.job(second["id"], "A", self.f.project)["status"], "failed"
        )
        self.assertFalse(
            self.cloud.get(OWNER, self.f.project, run["id"])["state"][
                "create_attempted"
            ]
        )
        validate_history(self.f.store)

    def test_real_http_browser_approves_queues_and_observes_sdk_worker(self):
        import socket
        import threading
        import time
        import uvicorn
        from streamlit.testing.v1 import AppTest
        from pathlib import Path

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(self.f.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if time.monotonic() > deadline:
                    self.fail("Cloud browser HTTP startup timed out")
                time.sleep(0.02)
            at = AppTest.from_file(str(Path("app.py").resolve()))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = url
            at.session_state["principal"] = OWNER
            at.run(timeout=30)
            self.assertFalse(at.exception)

            def widget(kind, label):
                return next(w for w in getattr(at, kind) if w.label == label)

            widget(
                "checkbox",
                "I approve transferring this study to the selected cloud account and region",
            ).check()
            widget(
                "checkbox",
                "I approve paid training within this profile's instance and runtime limits",
            ).check()
            widget("button", "Approve cloud run").click().run(timeout=30)
            self.assertFalse(at.exception)
            runs = self.cloud.get(
                OWNER,
                self.f.project,
                self.f.store.list("A", self.f.project, "cloud-run")[0]["id"],
            )
            self.assertEqual(runs["state"]["phase"], "ready")
            widget("button", "Submit approved paid training").click().run(timeout=30)
            self.assertFalse(at.exception)
            job = next(
                j
                for j in self.f.store.jobs("A", self.f.project)
                if j["kind"] == "cloud-control"
            )
            self.assertEqual(job["status"], "queued")
            self.expect_submission(runs)
            self.f.worker.once()
            self.assertEqual(
                self.f.store.job(job["id"], "A", self.f.project)["status"], "succeeded"
            )
            at.run(timeout=30)
            self.assertFalse(at.exception)
            self.assertFalse(
                any(w.label == "Submit approved paid training" for w in at.button)
            )
            self.assertTrue(
                any(w.label == "Observe the same provider job" for w in at.button)
            )
            self.assertEqual(
                self.cloud.get(OWNER, self.f.project, runs["id"])["state"]["phase"],
                "submitted",
            )
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())

    def test_operator_sdk_factory_ignores_global_and_per_service_endpoint_overrides(
        self,
    ):
        override = {
            "DMT_CLOUD_AWS_ACCESS_KEY_ID": "fixture-key",
            "DMT_CLOUD_AWS_SECRET_ACCESS_KEY": "fixture-secret",
            "AWS_ENDPOINT_URL": "https://unapproved.invalid",
            "AWS_ENDPOINT_URL_S3": "https://unapproved-s3.invalid",
        }
        with patch.dict(os.environ, override):
            gateway = AwsGateway.from_operator(self.p)
            try:
                for client in (gateway.sagemaker, gateway.s3, gateway.sts):
                    self.assertTrue(client.meta.endpoint_url.endswith(".amazonaws.com"))
                    self.assertEqual(
                        client.meta.config.retries["total_max_attempts"], 1
                    )
            finally:
                gateway.close()

    def test_profile_validation_no_ambient_credentials_and_wrong_account(self):
        validate_profile(self.p)
        for key, value in (
            ("gpu", "ml.m5.xlarge"),
            ("max_inflight", True),
            ("image_digest", self.p["image_digest"].replace("@sha256:", ":latest-")),
            ("source_sha256", "old-source"),
        ):
            wrong = copy.deepcopy(self.p)
            wrong[key] = value
            with self.assertRaises(ValueError):
                validate_profile(wrong)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CloudUnavailable):
                AwsGateway.from_operator(self.p)
        self.stubs["sts"].add_response(
            "get_caller_identity",
            {
                "Account": "999999999999",
                "Arn": "arn:aws:iam::999999999999:user/x",
                "UserId": "x",
            },
            {},
        )
        with self.assertRaises(CloudUnavailable):
            self.gateway.identity()


if __name__ == "__main__":
    unittest.main()
