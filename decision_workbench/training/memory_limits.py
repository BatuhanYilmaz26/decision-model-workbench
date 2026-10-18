"""Conservative available Linux memory, including inherited container limits."""

from pathlib import Path, PurePosixPath


def available_linux_memory(proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup")):
    proc, cgroups = Path(proc), Path(cgroups)
    lines = (proc / "meminfo").read_text().splitlines()
    available = (
        int(next(line.split()[1] for line in lines if line.startswith("MemAvailable:")))
        * 1024
    )
    if available < 0:
        raise ValueError("Available host memory must be nonnegative")
    try:
        memberships = (proc / "self/cgroup").read_text().splitlines()
    except FileNotFoundError:
        memberships = []
    candidates = [
        (cgroups, "memory.max", "memory.current"),
        (cgroups / "memory", "memory.limit_in_bytes", "memory.usage_in_bytes"),
    ]
    for line in memberships:
        fields = line.split(":", 2)
        if len(fields) != 3:
            raise ValueError("Cannot inspect this process's memory control group")
        relative = PurePosixPath(fields[2])
        if (
            not relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) > 64
        ):
            raise ValueError("Invalid process memory control-group path")
        if fields[0] == "0" and not fields[1]:
            root, limit, current = cgroups, "memory.max", "memory.current"
        elif "memory" in fields[1].split(","):
            root, limit, current = (
                cgroups / "memory",
                "memory.limit_in_bytes",
                "memory.usage_in_bytes",
            )
        else:
            continue
        directory = root.joinpath(*relative.parts[1:])
        while directory != root:
            candidates.append((directory, limit, current))
            directory = directory.parent
    visited = set()
    for directory, limit_name, usage_name in candidates:
        key = (directory, limit_name)
        if key in visited:
            continue
        visited.add(key)
        try:
            raw = (directory / limit_name).read_text().strip()
        except FileNotFoundError:
            continue
        if raw == "max":
            continue
        limit = int(raw)
        if limit < 0:
            raise ValueError("Memory control-group limit must be nonnegative")
        try:
            used = int((directory / usage_name).read_text().strip())
        except FileNotFoundError:
            # A known limit with unknown usage cannot establish free capacity.
            return 0
        if used < 0:
            raise ValueError("Memory control-group usage must be nonnegative")
        available = min(available, max(0, limit - used))
    return available
