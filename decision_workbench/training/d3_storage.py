"""Refuse insufficient native checkpoint/export storage before large transfers."""

from pathlib import Path
import shutil


def existing_parent(path):
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    if not path.is_dir():
        raise ValueError("Choose a directory for D3 model storage")
    return path


def missing_weights(cache, profile):
    snapshot = (
        Path(cache)
        / ("models--" + profile.model.replace("/", "--"))
        / "snapshots"
        / profile.revision
    )
    return sum(
        value["bytes"]
        for name, value in profile.weights.items()
        if not (snapshot / name).is_file()
        or (snapshot / name).stat().st_size != value["bytes"]
    )


def training_storage(cache, output, profile, *, download):
    """Budget cache misses and a complete separate export, on their actual volumes."""
    cache_volume = existing_parent(cache)
    output_volume = existing_parent(output)
    transfer = missing_weights(cache, profile) if download else 0
    export = profile.weight_bytes + profile.readout_bytes
    margin = 1024**3
    if cache_volume.stat().st_dev == output_volume.stat().st_dev:
        required = transfer + export + margin
        if shutil.disk_usage(output_volume).free < required:
            raise ValueError(
                f"D3 checkpoint and export need at least {required / 1024**3:.1f} GiB free on their shared volume"
            )
    elif (
        shutil.disk_usage(cache_volume).free < transfer + margin
        or shutil.disk_usage(output_volume).free < export + margin
    ):
        raise ValueError(
            "D3 needs more free storage for the checkpoint cache and full export"
        )


def cache_storage(cache, profile):
    volume = existing_parent(cache)
    required = missing_weights(cache, profile) + profile.readout_bytes + 512 * 1024**2
    if shutil.disk_usage(volume).free < required:
        raise ValueError(
            f"D3 checkpoint download needs at least {required / 1024**3:.1f} GiB free in its cache volume"
        )
