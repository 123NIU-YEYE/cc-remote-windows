"""Bounded completed pages; native files and history revisions stay authoritative."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from threading import RLock

from cc_remote.protocol import History
from cc_remote.wrapper.history_store import (
    HistorySourceFingerprint,
    history_source_extends,
)


def unrelated_codex_append(previous, current, native_ids: frozenset[str]) -> bool:
    """Prove a small append belongs to newer tasks, otherwise rebuild normally.

    Unknown ownership, partial records, compaction and rollback fail closed.
    A byte limit prevents validating a cache from becoming another huge scan.
    """
    if (
        not native_ids
        or not 0 < current.size - previous.size <= 4 * 1024 * 1024
        or not history_source_extends(previous, current)
    ):
        return False
    with open(current.path, "rb") as source:
        if previous.size:
            source.seek(previous.size - 1)
            if source.read(1) != b"\n":
                return False
        data = source.read(current.size - previous.size)
    if not data.endswith(b"\n"):
        return False
    owner = None
    native_bytes = tuple(native.encode() for native in native_ids)
    for line in data.splitlines():
        if not line.strip():
            continue
        # Conservative even for references in text: late results for a cached
        # native task must never be hidden by an apparently unchanged summary.
        if any(native in line for native in native_bytes):
            return False
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            return False
        if not isinstance(row, dict):
            return False
        kind, payload = row.get("type"), row.get("payload")
        if kind not in {"event_msg", "response_item", "turn_context"} or not isinstance(
            payload, dict
        ):
            return False
        event = payload.get("type")
        if event in {
            "thread_rolled_back",
            "context_compacted",
            "compaction",
            "contextCompaction",
        }:
            return False
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") in {
            "ContextCompaction",
            "contextCompaction",
        }:
            return False
        if kind == "turn_context" or event == "task_started":
            owner = payload.get("turn_id")
            if not isinstance(owner, str) or not owner or owner in native_ids:
                return False
        if owner is None:
            return False
        if payload.get("turn_id", owner) != owner:
            return False
    return owner is not None


@dataclass
class _Page:
    source: HistorySourceFingerprint
    history: History
    size: int
    native_ids: frozenset[str]


class HistoryPageCache:
    def __init__(self, max_pages=32, max_bytes=8 * 1024 * 1024):
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.pages: OrderedDict[tuple, _Page] = OrderedDict()
        self.bytes = 0
        self._lock = RLock()

    def discard(self, key):
        with self._lock:
            page = self.pages.pop(key, None)
            if page:
                self.bytes -= page.size

    def invalidate(self, sid):
        with self._lock:
            for key in tuple(self.pages):
                if key[0] == sid:
                    self.discard(key)

    def get(self, key, source, *, codex=False):
        with self._lock:
            page = self.pages.get(key)
            if page is None:
                return None
            previous = page.source
        # Native I/O runs in a worker. Do not hold up a live-thread invalidation
        # or cache admission on the event loop while validating an append.
        try:
            valid = previous == source or (
                codex and unrelated_codex_append(previous, source, page.native_ids)
            )
        except OSError:
            valid = False
        with self._lock:
            if self.pages.get(key) is not page:
                return None
            if page.source != previous and page.source != source:
                return None
            if not valid:
                if page.source == previous:
                    self.discard(key)
                return None
            page.source = source
            self.pages.move_to_end(key)
            return page.history.model_copy(deep=True)

    def put(self, key, source, history, *, native_ids=frozenset()):
        with self._lock:
            if (
                not history.before
                or history.error
                or history.authoritative is False
                or not history.turns
                or any(not turn.done for turn in history.turns)
            ):
                return
            raw = history.model_dump_json()
            size = len(raw.encode())
            if size > self.max_bytes:
                return
            self.discard(key)
            self.pages[key] = _Page(
                source,
                history.model_copy(deep=True),
                size,
                frozenset(native_ids),
            )
            self.bytes += size
            while len(self.pages) > self.max_pages or self.bytes > self.max_bytes:
                self.discard(next(iter(self.pages)))
