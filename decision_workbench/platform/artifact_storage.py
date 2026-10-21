"""Immutable model objects and verified, atomic per-node artifact caches."""

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from decision_workbench.training.resources import ResourceLease, ResourceBusy

FORMAT = "domain-shared-artifact-v1"
MAX_MANIFEST = 8 * 1024**2
MAX_FILES = 10000
MAX_BYTES = 500 * 1024**3
CHUNK = 1024**2
HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


class ArtifactUnavailable(RuntimeError):
    """Object storage is temporarily unavailable; no unverified model is used."""


@contextmanager
def cache_lock(path, timeout):
    try:
        with ResourceLease(path).hold(timeout=timeout):
            yield
    except ResourceBusy:
        raise ArtifactUnavailable(
            "Model artifact cache is being prepared; retry later"
        ) from None


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def safe_name(name):
    if not isinstance(name, str) or len(name.encode("utf-8")) > 1024:
        raise ValueError("Artifact file name is invalid")
    path = PurePosixPath(name)
    if not name or not path.parts or path.is_absolute() or path.as_posix() != name:
        raise ValueError("Artifact file path must be canonical and relative")
    for part in path.parts:
        if (
            part in {".", ".."}
            or part[-1:] in {".", " "}
            or any(ord(c) < 32 or c in '\\:<>"|?*' for c in part)
        ):
            raise ValueError("Artifact file path is not portable")
        if re.fullmatch(
            r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0], re.I
        ):
            raise ValueError("Artifact file uses a reserved device name")
    return name


def checksum(path, check=lambda: None):
    sha, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            check()
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def validate_names(names):
    prefixes = {}
    for name in names:
        safe_name(name)
        parts = name.split("/")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            value = (prefix, depth == len(parts))
            previous = prefixes.setdefault(prefix.casefold(), value)
            if previous != value:
                raise ValueError(
                    "Artifact inventory has case or file/directory collisions"
                )


def owned_stage(path, parent):
    if (
        linked(path)
        or path.resolve().parent != parent.resolve()
        or not path.name.startswith(".dmt-download-")
    ):
        raise ValueError(
            "Refusing artifact staging outside its reserved cache directory"
        )


def tree(folder, check=lambda: None):
    if linked(folder) or not folder.is_dir():
        raise ValueError("Model artifact must be an ordinary directory")
    files, names, total = {}, set(), 0
    for path in sorted(folder.rglob("*")):
        if linked(path) or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Model artifact contains a linked or external path")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Model artifact contains a special file")
        name = safe_name(path.relative_to(folder).as_posix())
        if name.casefold() in names:
            raise ValueError("Model artifact has case-colliding paths")
        names.add(name.casefold())
        declared = path.stat().st_size
        if len(files) >= MAX_FILES or total + declared > MAX_BYTES:
            raise ValueError("Model artifact exceeds configured inventory bounds")
        sha, size = checksum(path, check)
        if size != declared:
            raise ValueError("Model artifact changed during publication")
        total += size
        files[name] = {"sha256": sha, "bytes": size}
        if len(files) > MAX_FILES or total > MAX_BYTES:
            raise ValueError("Model artifact exceeds configured inventory bounds")
    if not files:
        raise ValueError("Model artifact is empty")
    validate_names(files)
    return files


def checked_copy(stream, target, size, sha, check=lambda: None):
    received, actual = 0, hashlib.sha256()
    with target.open("xb") as out:
        while chunk := stream.read(min(CHUNK, size - received + 1)):
            check()
            received += len(chunk)
            if received > size:
                raise ValueError("Artifact object exceeds its registered size")
            actual.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if received != size or actual.hexdigest() != sha:
        raise ValueError("Artifact object differs from its registered content")


def reference_key(namespace, reference):
    if (
        not isinstance(reference, dict)
        or set(reference) != {"namespace", "scope", "sha256", "bytes"}
        or reference["namespace"] != namespace
        or not isinstance(namespace, str)
        or not IDENTIFIER.fullmatch(namespace)
        or not isinstance(reference["scope"], str)
        or not HEX.fullmatch(reference["scope"])
        or not isinstance(reference["sha256"], str)
        or not HEX.fullmatch(reference["sha256"])
        or type(reference["bytes"]) is not int
        or not 1 <= reference["bytes"] <= MAX_MANIFEST
    ):
        raise ValueError("Invalid shared artifact reference")
    return namespace + "/manifests/" + reference["scope"] + "/" + reference["sha256"]


def manifest_content(body, namespace, reference, model=None):
    if (
        len(body) != reference["bytes"]
        or hashlib.sha256(body).hexdigest() != reference["sha256"]
    ):
        raise ValueError("Shared artifact manifest integrity check failed")
    try:
        manifest = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValueError("Shared artifact manifest is malformed") from None
    if not isinstance(manifest, dict):
        raise ValueError("Shared artifact manifest must be an object")
    model = model or {
        key: manifest.get(key)
        for key in ("artifact_id", "backend", "model_sha256", "schema_sha256")
    }
    if (
        not isinstance(model.get("artifact_id"), str)
        or not IDENTIFIER.fullmatch(model["artifact_id"])
        or not isinstance(model.get("backend"), str)
        or not 1 <= len(model["backend"]) <= 128
        or any(
            not isinstance(model.get(key), str) or not HEX.fullmatch(model[key])
            for key in ("model_sha256", "schema_sha256")
        )
    ):
        raise ValueError("Invalid shared model identity")
    expected = {
        "format": FORMAT,
        "namespace": namespace,
        "scope": reference["scope"],
        **{
            key: model[key]
            for key in ("artifact_id", "backend", "model_sha256", "schema_sha256")
        },
    }
    if (
        not isinstance(manifest, dict)
        or canonical(manifest) != body
        or set(manifest) != set(expected) | {"files"}
        or any(manifest.get(k) != v for k, v in expected.items())
    ):
        raise ValueError("Shared artifact does not match the registered model")
    files, names, total = manifest["files"], set(), 0
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Invalid shared artifact inventory")
    for name, item in files.items():
        safe_name(name)
        if (
            name.casefold() in names
            or not isinstance(item, dict)
            or set(item) != {"sha256", "bytes"}
            or not isinstance(item["sha256"], str)
            or not HEX.fullmatch(item["sha256"])
            or type(item["bytes"]) is not int
            or not 0 <= item["bytes"] <= MAX_BYTES
        ):
            raise ValueError("Invalid shared artifact file")
        names.add(name.casefold())
        total += item["bytes"]
    if total > MAX_BYTES:
        raise ValueError("Shared artifact exceeds total size limits")
    validate_names(files)
    root_name = (
        "model.json"
        if model["backend"] in {"cpu-baseline", "hosted"}
        else "manifest.json"
    )
    if files.get(root_name, {}).get("sha256") != model["model_sha256"]:
        raise ValueError("Shared artifact model root differs from the registry")
    return manifest, files, total


class DirectoryObjects:
    """Local object-transport conformance backend; not a cloud service."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.descriptor = {"provider": "directory", "root": str(self.root)}

    def path(self, key):
        if not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*", key) or any(
            p in {"", ".", ".."} for p in key.split("/")
        ):
            raise ValueError("Invalid object key")
        path = self.root / key
        for ancestor in (path, *path.parents):
            if ancestor == self.root.parent:
                break
            if linked(ancestor):
                raise ValueError("Object directory contains a link")
        return path

    def open(self, key):
        try:
            return self.path(key).open("rb")
        except OSError:
            raise ArtifactUnavailable("Artifact object is unavailable") from None

    def put_file(self, key, source, sha, size, check=lambda: None):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".dmt-object-", dir=target.parent)
        os.close(handle)
        temporary = Path(temporary)
        temporary.unlink()
        try:
            with source.open("rb") as stream:
                checked_copy(stream, temporary, size, sha, check)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if checksum(target, check) != (sha, size):
                    raise ValueError("Existing immutable object has different content")
        finally:
            temporary.unlink(missing_ok=True)


class ArtifactRepository:
    """Publish complete bundles before registry visibility; fetch by pinned hash."""

    def __init__(self, objects):
        self.objects = objects
        self.descriptor = objects.descriptor

    def publish(self, namespace, scope, folder, model, check=lambda: None):
        if not IDENTIFIER.fullmatch(namespace) or not HEX.fullmatch(scope):
            raise ValueError("Invalid artifact storage scope")
        folder = Path(folder)
        files = tree(folder, check)
        root_name = (
            "model.json"
            if model["backend"] in {"cpu-baseline", "hosted"}
            else "manifest.json"
        )
        if files.get(root_name, {}).get("sha256") != model["model_sha256"]:
            raise ValueError("Published model root differs from the registry candidate")
        manifest = {
            "format": FORMAT,
            "namespace": namespace,
            "scope": scope,
            **{
                key: model[key]
                for key in ("artifact_id", "backend", "model_sha256", "schema_sha256")
            },
            "files": files,
        }
        body = canonical(manifest)
        if len(body) > MAX_MANIFEST:
            raise ValueError("Shared artifact manifest exceeds its size limit")
        for name, item in files.items():
            check()
            self.objects.put_file(
                namespace + "/blobs/" + item["sha256"],
                folder / name,
                item["sha256"],
                item["bytes"],
                check,
            )
        sha = hashlib.sha256(body).hexdigest()
        handle, temporary = tempfile.mkstemp(prefix=".dmt-manifest-", dir=folder.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(body)
                out.flush()
                os.fsync(out.fileno())
            check()
            self.objects.put_file(
                namespace + "/manifests/" + scope + "/" + sha,
                temporary,
                sha,
                len(body),
                check,
            )
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "namespace": namespace,
            "scope": scope,
            "sha256": sha,
            "bytes": len(body),
        }

    def inspect(self, namespace, reference, model=None):
        """Read a bounded immutable manifest without downloading model files."""
        key = reference_key(namespace, reference)
        with closing(self.objects.open(key)) as stream:
            body = bytearray()
            while chunk := stream.read(min(CHUNK, reference["bytes"] - len(body) + 1)):
                body.extend(chunk)
                if len(body) > reference["bytes"]:
                    break
        return manifest_content(bytes(body), namespace, reference, model)[0]

    def restore(
        self,
        namespace,
        reference,
        model,
        folder,
        max_download_bytes=8 * 1024**2,
        check=lambda: None,
    ):
        check()
        reference_key(namespace, reference)
        folder = Path(folder)
        index_dir = folder.parent / ".dmt-manifests"
        index = index_dir / (reference["sha256"] + ".json")
        if linked(index_dir) or linked(index):
            raise ValueError("Artifact manifest cache contains a link")
        if index.exists():
            if index.stat().st_size != reference["bytes"]:
                raise ValueError("Artifact manifest cache integrity check failed")
            body = index.read_bytes()
        else:
            key = (
                namespace
                + "/manifests/"
                + reference["scope"]
                + "/"
                + reference["sha256"]
            )
            with closing(self.objects.open(key)) as stream:
                parts, received = [], 0
                while chunk := stream.read(
                    min(CHUNK, reference["bytes"] - received + 1)
                ):
                    check()
                    received += len(chunk)
                    parts.append(chunk)
                    if received > reference["bytes"]:
                        break
                body = b"".join(parts)
        manifest, files, total = manifest_content(body, namespace, reference, model)
        if not index.exists():
            index_dir.mkdir(parents=True, exist_ok=True)
            handle, temporary_index = tempfile.mkstemp(
                prefix=".dmt-index-", dir=index_dir
            )
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(body)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary_index, index)
            finally:
                Path(temporary_index).unlink(missing_ok=True)
        folder.parent.mkdir(parents=True, exist_ok=True)
        with cache_lock(
            folder.parent / (".dmt-" + folder.name + ".lock"),
            30 if max_download_bytes is None else 1,
        ):
            if folder.exists():
                if tree(folder, check) != files:
                    raise ValueError(
                        "Artifact cache integrity check failed; local files differ from the immutable manifest"
                    )
                check()
                return folder
            if max_download_bytes is not None and total > max_download_bytes:
                raise ArtifactUnavailable(
                    "Model artifacts need operator cache warming on this node before release or prediction"
                )
            if shutil.disk_usage(folder.parent).free < total + 64 * 1024**2:
                raise ArtifactUnavailable(
                    "Not enough free disk space for a verified artifact cache; free space before warming"
                )
            from .owned_staging import OwnedStage

            stage = OwnedStage(folder.parent, "download")
            temporary = stage.path
            try:
                owned_stage(temporary, folder.parent)
                for name, item in files.items():
                    check()
                    target = temporary / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with closing(
                        self.objects.open(namespace + "/blobs/" + item["sha256"])
                    ) as stream:
                        checked_copy(
                            stream, target, item["bytes"], item["sha256"], check
                        )
                owned_stage(temporary, folder.parent)
                check()
                if linked(folder) or folder.resolve().parent != folder.parent.resolve():
                    raise ValueError(
                        "Artifact cache target moved outside its reserved directory"
                    )
                temporary.rename(folder)
            finally:
                stage.close()
        return folder
