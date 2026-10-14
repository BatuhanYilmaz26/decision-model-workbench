"""Bounded regular-file USTAR packaging shared by cloud training wrappers."""

import tarfile
from decision_workbench.training.cloud_runner import ordinary


def pack_export(folder, archive, max_bytes):
    folder, archive = ordinary(folder), ordinary(archive)
    paths = sorted(p for p in folder.rglob("*") if p.is_file())
    if (
        not paths
        or len(paths) > 2048
        or sum(p.stat().st_size for p in paths) > max_bytes
    ):
        raise ValueError("Cloud export inventory exceeds its bounds")
    created = False
    try:
        with archive.open("xb") as stream:
            created = True
            with tarfile.open(
                fileobj=stream, mode="w:gz", format=tarfile.USTAR_FORMAT
            ) as out:
                for p in paths:
                    p = ordinary(p)
                    name = p.relative_to(folder).as_posix()
                    info = out.gettarinfo(str(p), arcname=name)
                    if not info.isfile():
                        raise ValueError("Cloud export contains a nonregular file")
                    info.uid, info.gid, info.uname, info.gname, info.mtime = (
                        0,
                        0,
                        "",
                        "",
                        0,
                    )
                    info.mode = 0o600
                    with p.open("rb") as data:
                        out.addfile(info, data)
        if archive.stat().st_size > max_bytes:
            raise ValueError("Compressed cloud export exceeds its byte bound")
    except BaseException:
        if created:
            archive.unlink(missing_ok=True)
        raise
