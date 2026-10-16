"""Per-request history costs, without prompts, cursors or native payloads."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from inspect import iscoroutinefunction
import time


@dataclass
class HistoryReadProfile:
    prefetch: bool = False
    cache_allowed: bool = True
    stages: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)


history_profile: ContextVar[HistoryReadProfile | None] = ContextVar(
    "history_profile", default=None
)


@contextmanager
def history_stage(name):
    profile = history_profile.get()
    started = time.perf_counter()
    try:
        yield
    finally:
        if profile is not None:
            profile.stages[name] = (
                profile.stages.get(name, 0) + (time.perf_counter() - started) * 1000
            )
            profile.calls[name] = profile.calls.get(name, 0) + 1


def history_timed(name):
    def decorate(fn):
        if not iscoroutinefunction(fn):

            @wraps(fn)
            def measured_sync(*args, **kwargs):
                with history_stage(name):
                    return fn(*args, **kwargs)

            return measured_sync

        @wraps(fn)
        async def measured(*args, **kwargs):
            with history_stage(name):
                return await fn(*args, **kwargs)

        return measured

    return decorate
