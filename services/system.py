from __future__ import annotations

import os

try:
    import psutil
except ImportError:
    psutil = None


def _read_cgroup_mem(path: str) -> int | None:
    try:
        with open(path) as f:
            val = f.read().strip()
            if val and val != "max":
                return int(val)
    except Exception:
        pass
    return None


def _get_container_memory_total() -> int | None:
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3 and "memory" in parts[1]:
                    cgroup_path = parts[2].lstrip("/")
                    for base in ("/sys/fs/cgroup",):
                        mem_max = _read_cgroup_mem(os.path.join(base, cgroup_path, "memory.max"))
                        if mem_max is not None:
                            return mem_max
    except Exception:
        pass
    for path in ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory.max"):
        val = _read_cgroup_mem(path)
        if val is not None:
            return val
    if psutil is not None:
        try:
            return psutil.virtual_memory().total
        except Exception:
            pass
    return None
