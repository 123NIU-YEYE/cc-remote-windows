"""Bounded in-memory projections of append-only JSONL metadata.

The native file remains authoritative. Replacement, truncation, rewrite or an
unfinished last record prevents reuse; no transcript bodies are retained here.
"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import json
from threading import RLock

from cc_remote.wrapper.history_store import (
    HistorySourceFingerprint,
    history_source_extends,
)


@dataclass
class _Projection:
    source: HistorySourceFingerprint
    state: dict
    row_count: int
    size: int


class IncrementalJsonlProjection:
    def __init__(self, *, max_entries=16, max_bytes=8 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries: OrderedDict[str, _Projection] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def read(self, path, factory, update, bounded_lines):
        source = HistorySourceFingerprint.capture(path)
        with self._lock:
            previous = self._entries.get(source.path)
            if previous is not None:
                self._entries.move_to_end(source.path)
        if previous is not None and previous.source == source:
            return deepcopy(previous.state)
        if (
            previous is not None
            and source.size > previous.source.size
            and history_source_extends(previous.source, source)
        ):
            state, row_count = deepcopy(previous.state), previous.row_count
            offset = previous.source.size
        else:
            state, row_count, offset = factory(), 0, 0
        # Use the same bounded text-record iterator as the uncached translator:
        # skipped oversized rows must not change fallback message identities.
        with open(source.path, encoding="utf-8") as stream:
            stream.seek(offset)
            for line in bounded_lines(stream):
                index = row_count
                row_count += 1
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict):
                    update(state, row, index)
        with open(source.path, "rb") as stream:
            if source.size:
                stream.seek(source.size - 1)
            complete = not source.size or stream.read(1) == b"\n"
        # A valid JSON object without a newline can be rendered now, but must
        # be reread if the writer subsequently extends that record. A moving
        # source similarly cannot become a cache checkpoint.
        if complete and HistorySourceFingerprint.capture(path) == source:
            size = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
            if size <= self.max_bytes:
                entry = _Projection(source, deepcopy(state), row_count, size)
                with self._lock:
                    old = self._entries.pop(source.path, None)
                    if old is not None:
                        self._bytes -= old.size
                    self._entries[source.path] = entry
                    self._bytes += size
                    while (
                        len(self._entries) > self.max_entries
                        or self._bytes > self.max_bytes
                    ):
                        self._bytes -= self._entries.popitem(last=False)[1].size
        return state
