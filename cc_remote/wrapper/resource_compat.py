"""Minimal ``resource`` compatibility shim.

``resource`` is a Unix-only stdlib module. The DSH/Claude/Codex wrapper only uses
it to inspect and raise ``RLIMIT_NOFILE``; on Windows there is no such limit, so
the shim reports infinity and ignores attempts to change it. Import sites keep
using ``resource.X`` unchanged, which keeps the Unix code path byte-identical.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    RLIM_INFINITY = -1
    RLIMIT_NOFILE = 0

    def getrlimit(_resource_id: int) -> tuple[int, int]:
        return (RLIM_INFINITY, RLIM_INFINITY)

    def setrlimit(_resource_id: int, _limits: tuple[int, int]) -> None:
        return None

    def getrusage(_who: int):  # pragma: no cover - not used on Windows
        raise NotImplementedError("resource.getrusage is unavailable on Windows")
else:  # pragma: no cover - exercised on Unix
    from resource import (  # noqa: F401
        RLIM_INFINITY,
        RLIMIT_NOFILE,
        getrlimit,
        setrlimit,
    )
