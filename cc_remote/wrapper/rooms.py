"""Collaboration rooms: persistent room/member/timeline state and @mention routing.

A room is *only* an orchestration layer over ordinary sessions: it records which
agents participate in one working directory and which timeline entries belong to
the conversation. Execution, models, permissions and usage all stay with the
existing per-session machinery, so a room never holds credentials of its own.

See ``docs/collaboration-rooms.md`` for the full design and the wire contract.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
import inspect
from typing import Any, Callable, Iterable, Optional, Protocol

ROOM_LIMIT = 200
MEMBER_LIMIT = 8
MESSAGE_LIMIT = 500
NAME_LIMIT = 200
TEXT_LIMIT = 32_000
CWD_LIMIT = 4096

# "@全体" style broadcasts. Kept explicit so an agent named "全体" would still
# win by exact handle match before the broadcast rule applies.
_BROADCAST = ("全体", "所有人", "all", "everyone")
_MENTION = re.compile(r"@([A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_.\-\u00b7\u4e00-\u9fff]*)")

_ENGINES = ("claude", "codex", "dsh")


class RoomError(ValueError):
    """Rejected room mutation (bad shape, unknown room, over a bound)."""


@dataclass
class RoomMember:
    member_id: str
    label: str
    engine: str = "claude"
    profile_id: Optional[str] = None
    # The member's own session, created lazily on first dispatch. Persisted so a
    # wrapper restart resumes the same conversation instead of starting over.
    session_id: Optional[str] = None
    is_default: bool = False

    def handles(self) -> tuple[str, ...]:
        """Names this member answers to.

        A display label is often ``"Claude · iris"``, which contains spaces and
        therefore cannot be written as one ``@`` token. Split it on the usual
        separators so both the engine segment (``@Claude``, what people actually
        type) and the agent name (``@iris``) resolve to this member. A label
        without whitespace is also accepted whole.
        """
        raw: list[str] = []
        label = self.label.strip()
        if label and not any(char.isspace() for char in label):
            raw.append(label)
        for part in re.split(r"[\u00b7·/|\-]", label):
            candidate = part.strip()
            if candidate and candidate not in raw:
                raw.append(candidate)
        return tuple(raw)

    def as_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "label": self.label,
            "engine": self.engine,
            "profile_id": self.profile_id,
            "session_id": self.session_id,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RoomMember":
        return cls(
            member_id=str(payload["member_id"]),
            label=str(payload["label"]),
            engine=str(payload.get("engine") or "claude"),
            profile_id=(str(payload["profile_id"])
                        if payload.get("profile_id") else None),
            session_id=(str(payload["session_id"])
                        if payload.get("session_id") else None),
            is_default=bool(payload.get("is_default")),
        )


@dataclass
class RoomMessage:
    msg_id: str
    ts: float
    author_kind: str            # "user" | "member"
    author_label: str
    text: str
    mentions: list[str] = field(default_factory=list)
    state: str = "done"         # member messages: pending|running|done|failed

    def as_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "ts": self.ts,
            "author_kind": self.author_kind,
            "author_label": self.author_label,
            "text": self.text,
            "mentions": list(self.mentions),
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RoomMessage":
        return cls(
            msg_id=str(payload["msg_id"]),
            ts=float(payload.get("ts") or 0.0),
            author_kind=str(payload.get("author_kind") or "user"),
            author_label=str(payload.get("author_label") or ""),
            text=str(payload.get("text") or "")[:TEXT_LIMIT],
            mentions=[str(item) for item in (payload.get("mentions") or [])],
            state=str(payload.get("state") or "done"),
        )


@dataclass
class Room:
    room_id: str
    name: str
    cwd: str
    members: list[RoomMember] = field(default_factory=list)
    messages: list[RoomMessage] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def member(self, member_id: str) -> Optional[RoomMember]:
        for item in self.members:
            if item.member_id == member_id:
                return item
        return None

    def as_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "name": self.name,
            "cwd": self.cwd,
            "members": [member.as_dict() for member in self.members],
            "messages": [message.as_dict() for message in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Room":
        return cls(
            room_id=str(payload["room_id"]),
            name=str(payload.get("name") or "")[:NAME_LIMIT],
            cwd=str(payload.get("cwd") or "")[:CWD_LIMIT],
            members=[RoomMember.from_dict(item)
                     for item in (payload.get("members") or [])][:MEMBER_LIMIT],
            messages=[RoomMessage.from_dict(item)
                      for item in (payload.get("messages") or [])][-MESSAGE_LIMIT:],
            created_at=float(payload.get("created_at") or 0.0),
            updated_at=float(payload.get("updated_at") or 0.0),
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000):x}-{os.urandom(4).hex()}"


def _bounded_text(value: str, limit: int = TEXT_LIMIT) -> str:
    return value if len(value) <= limit else value[:limit]


def parse_mentions(
    text: str, members: Iterable[RoomMember],
) -> list[str]:
    """Return the member ids addressed by ``@`` handles in ``text``.

    Matching is longest-handle-first so ``@Claude · iris`` wins over ``@Claude``,
    and case-insensitive because the labels are display names. ``@全体`` (and its
    documented aliases) expands to every member. Unknown handles are ignored: a
    user may legitimately write ``@media`` in prose.
    """
    room_members = list(members)
    if not room_members or not text:
        return []

    by_handle: dict[str, list[str]] = {}
    for member in room_members:
        for handle in member.handles():
            bucket = by_handle.setdefault(handle.casefold(), [])
            if member.member_id not in bucket:
                bucket.append(member.member_id)

    matched: list[str] = []
    broadcast = False
    for raw in _MENTION.findall(text):
        needle = raw.casefold()
        if needle in {item.casefold() for item in _BROADCAST}:
            broadcast = True
            continue
        for member_id in by_handle.get(needle, ()):
            if member_id not in matched:
                matched.append(member_id)

    if broadcast:
        return [member.member_id for member in room_members]
    return matched


class RoomStore:
    """Bounded, atomically persisted room state for one wrapper machine."""

    def __init__(self, state_dir: Path | str):
        self._dir = Path(state_dir)
        self._path = self._dir / "rooms.json"
        self._lock = threading.RLock()
        self._rooms: dict[str, Room] = {}
        self._load()

    # ---- read ----------------------------------------------------------
    def list_rooms(self) -> list[Room]:
        with self._lock:
            return sorted(self._rooms.values(),
                          key=lambda room: room.updated_at, reverse=True)

    def get(self, room_id: str) -> Optional[Room]:
        with self._lock:
            return self._rooms.get(room_id)

    # ---- write ---------------------------------------------------------
    def create(self, name: str, cwd: str, members: Iterable[RoomMember],
               *, room_id: Optional[str] = None) -> Room:
        clean_members = self._validate_members(members)
        now = time.time()
        room = Room(
            room_id=room_id or _new_id("room"),
            name=_bounded_text(name.strip(), NAME_LIMIT),
            cwd=_bounded_text(cwd.strip(), CWD_LIMIT),
            members=clean_members,
            created_at=now,
            updated_at=now,
        )
        if not room.name:
            raise RoomError("room name must not be empty")
        if not room.cwd:
            raise RoomError("room cwd must not be empty")
        with self._lock:
            if room.room_id in self._rooms:
                raise RoomError("room already exists")
            if len(self._rooms) >= ROOM_LIMIT:
                raise RoomError("room limit reached")
            self._rooms[room.room_id] = room
            self._persist()
        return room

    def update(self, room_id: str, *, name: Optional[str] = None,
               members: Optional[Iterable[RoomMember]] = None) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomError("unknown room")
            if name is not None:
                candidate = _bounded_text(name.strip(), NAME_LIMIT)
                if not candidate:
                    raise RoomError("room name must not be empty")
                room.name = candidate
            if members is not None:
                # Keep existing session bindings for members that survive the edit.
                previous = {member.member_id: member for member in room.members}
                validated = self._validate_members(members)
                for member in validated:
                    old = previous.get(member.member_id)
                    if old is not None and member.session_id is None:
                        member.session_id = old.session_id
                room.members = validated
            room.updated_at = time.time()
            self._persist()
        return room

    def delete(self, room_id: str) -> bool:
        with self._lock:
            removed = self._rooms.pop(room_id, None)
            if removed is None:
                return False
            self._persist()
            return True

    def append_message(self, room_id: str, message: RoomMessage) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomError("unknown room")
            room.messages.append(message)
            if len(room.messages) > MESSAGE_LIMIT:
                del room.messages[:-MESSAGE_LIMIT]
            room.updated_at = time.time()
            self._persist()
        return room

    def bind_session(self, room_id: str, member_id: str, session_id: str) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomError("unknown room")
            member = room.member(member_id)
            if member is None:
                raise RoomError("unknown member")
            member.session_id = session_id
            room.updated_at = time.time()
            self._persist()
        return room

    # ---- internals -----------------------------------------------------
    def _validate_members(self, members: Iterable[RoomMember]) -> list[RoomMember]:
        collected = list(members)
        if len(collected) > MEMBER_LIMIT:
            raise RoomError("member limit reached")
        if not collected:
            raise RoomError("room needs at least one member")
        seen: set[str] = set()
        result: list[RoomMember] = []
        for member in collected:
            member_id = member.member_id.strip()
            label = member.label.strip()
            if not member_id or not label:
                raise RoomError("member needs an id and a label")
            if member_id in seen:
                raise RoomError("duplicate member id")
            seen.add(member_id)
            engine = (member.engine or "claude").strip().lower()
            if engine not in _ENGINES:
                raise RoomError(f"unsupported member engine: {engine}")
            result.append(RoomMember(
                member_id=member_id,
                label=_bounded_text(label, NAME_LIMIT),
                engine=engine,
                profile_id=member.profile_id,
                session_id=member.session_id,
                is_default=member.is_default,
            ))
        return result

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            # A corrupt file must not take the wrapper down; start empty and let
            # the next write replace it.
            return
        rooms: dict[str, Room] = {}
        for item in (payload.get("rooms") or []):
            try:
                room = Room.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            rooms[room.room_id] = room
        self._rooms = rooms

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "rooms": [room.as_dict() for room in self._rooms.values()]}
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as stream:
                fd = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

# ---- dispatch -------------------------------------------------------------
#
# A room never executes anything itself: it resolves who was addressed and asks
# the host (the wrapper) to run that member's own session. Keeping the boundary
# at an async interface makes the routing testable without spawning engines.

MEMBER_IDLE = "idle"
MEMBER_RUNNING = "running"
MEMBER_QUEUED = "queued"
MEMBER_DONE = "done"
MEMBER_FAILED = "failed"

DEFAULT_MAX_PARALLEL = 2
CONTEXT_HEADER = "[房间 {name} · 目录 {cwd}]"


@dataclass
class MemberRuntime:
    """Live (non-persisted) dispatch state for one member of one room."""

    member_id: str
    session_id: Optional[str] = None
    state: str = MEMBER_IDLE
    queued: int = 0
    started_at: Optional[float] = None
    error: Optional[str] = None
    # True while this member holds a room-level parallelism permit.
    holding_permit: bool = False

    def as_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "session_id": self.session_id,
            "state": self.state,
            "queued": self.queued,
            "started_at": self.started_at,
            "error": self.error,
        }


class RoomHost(Protocol):
    """What a room needs from the wrapper. Implemented by the session pool."""

    async def ensure_session(self, room: Room, member: RoomMember) -> str:
        """Return the wire session id for this member, creating it if needed."""

    async def dispatch_query(
        self, session_id: str, text: str, *, msg_id: str,
        delivery: str,
    ) -> None:
        """Deliver one turn to a session (``queue`` keeps it behind a running turn)."""

    async def steer_session(self, session_id: str, text: str) -> None:
        """Add an instruction to that session's running turn."""

    async def cancel_session(self, session_id: str) -> None:
        """Interrupt that session's running turn."""


class RoomDispatcher:
    """Turn a room message into per-member turns, and their results back in."""

    def __init__(self, store: RoomStore, host: RoomHost, *,
                 max_parallel: int = DEFAULT_MAX_PARALLEL,
                 on_state: Optional[Callable[[str, "MemberRuntime"], Any]] = None):
        self._store = store
        self._host = host
        self._max_parallel = max(1, int(max_parallel))
        self._semaphore = asyncio.Semaphore(self._max_parallel)
        self._runtime: dict[str, dict[str, MemberRuntime]] = {}
        self._tasks: set[asyncio.Task] = set()
        # Called on every member state transition so the service layer can push
        # a RoomState frame without the dispatcher knowing about the transport.
        self._on_state = on_state

    # ---- inspection ----------------------------------------------------
    def runtime(self, room_id: str) -> list[MemberRuntime]:
        """Current per-member dispatch state, in the room's member order."""
        room = self._store.get(room_id)
        if room is None:
            return []
        states = self._runtime.setdefault(room_id, {})
        return [states.setdefault(member.member_id,
                                 MemberRuntime(member_id=member.member_id))
                for member in room.members]

    # ---- commands ------------------------------------------------------
    async def post(self, room_id: str, text: str, *,
                   delivery: str = "queue") -> RoomMessage:
        """Record a user message and start one turn per addressed member."""
        room = self._store.get(room_id)
        if room is None:
            raise RoomError("unknown room")
        body = _bounded_text(text.strip())
        if not body:
            raise RoomError("message must not be empty")
        return await self._record_and_dispatch(
            room_id, body, parse_mentions(body, room.members), delivery)

    async def post_to_member(self, room_id: str, member_id: str, text: str, *,
                             delivery: str = "queue") -> RoomMessage:
        """Dispatch to exactly one member, bypassing mention parsing.

        Needed by the "steer while idle" fallback: re-deriving a mention from the
        label would fan out to every member sharing that engine segment.
        """
        _state, member = self._require(room_id, member_id)
        body = _bounded_text(text.strip())
        if not body:
            raise RoomError("message must not be empty")
        return await self._record_and_dispatch(
            room_id, body, [member.member_id], delivery)

    async def _record_and_dispatch(self, room_id: str, body: str,
                                   mentions: list[str],
                                   delivery: str) -> RoomMessage:
        room = self._store.get(room_id)
        if room is None:
            raise RoomError("unknown room")
        message = RoomMessage(
            msg_id=_new_id("msg"), ts=time.time(), author_kind="user",
            author_label="你", text=body, mentions=list(mentions),
        )
        self._store.append_message(room_id, message)
        for member_id in mentions:
            member = room.member(member_id)
            if member is None:      # member removed between parse and dispatch
                continue
            state = self._state(room_id, member_id)
            state.queued += 1
            if state.state == MEMBER_IDLE:
                state.state = MEMBER_QUEUED
            await self._notify(room_id, state)
            self._spawn(self._dispatch(room, member, message, delivery))
        return message

    async def steer(self, room_id: str, member_id: str, text: str) -> None:
        state, member = self._require(room_id, member_id)
        body = text.strip()
        if not body:
            raise RoomError("steer text must not be empty")
        session_id = state.session_id or member.session_id
        if state.state not in {MEMBER_RUNNING, MEMBER_QUEUED} or not session_id:
            # Nothing is running (or nothing has run yet): steer degenerates to a
            # queued follow-up, which is what the UI's "引导" button means here.
            await self.post_to_member(room_id, member_id, body, delivery="queue")
            return
        await self._host.steer_session(session_id, body)

    async def cancel(self, room_id: str, member_id: str) -> None:
        state, member = self._require(room_id, member_id)
        session_id = state.session_id or member.session_id
        if session_id:
            await self._host.cancel_session(session_id)

    # ---- turn results --------------------------------------------------
    async def on_turn_terminal(self, room_id: str, member_id: str, text: str,
                               *, ok: bool = True) -> Optional[RoomMessage]:
        """Collect one member's finished turn into the room timeline."""
        room = self._store.get(room_id)
        if room is None:
            return None
        member = room.member(member_id)
        if member is None:
            return None
        state = self._state(room_id, member_id)
        state.state = MEMBER_DONE if ok else MEMBER_FAILED
        state.started_at = None
        state.error = None if ok else "turn failed"
        self._release(state)
        await self._notify(room_id, state)

        body = _bounded_text((text or "").strip())
        if not body and ok:
            return None
        message = RoomMessage(
            msg_id=_new_id("msg"), ts=time.time(), author_kind="member",
            author_label=member.label, text=body or "（本轮没有产出文本）",
            state=MEMBER_DONE if ok else MEMBER_FAILED,
        )
        self._store.append_message(room_id, message)
        return message

    # ---- waiting (tests / graceful shutdown) ---------------------------
    async def wait_idle(self, room_id: Optional[str] = None) -> None:
        """Await in-flight dispatch tasks (sends, not the turns themselves)."""
        if room_id is not None:
            for state in self._runtime.get(room_id, {}).values():
                state.queued = 0
        while self._tasks:
            pending = [task for task in self._tasks if not task.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

    # ---- internals -----------------------------------------------------
    def _state(self, room_id: str, member_id: str) -> MemberRuntime:
        states = self._runtime.setdefault(room_id, {})
        state = states.get(member_id)
        if state is None:
            state = MemberRuntime(member_id=member_id)
            states[member_id] = state
        return state

    def _require(self, room_id: str, member_id: str) -> tuple[MemberRuntime, RoomMember]:
        room = self._store.get(room_id)
        if room is None:
            raise RoomError("unknown room")
        member = room.member(member_id)
        if member is None:
            raise RoomError("unknown member")
        return self._state(room_id, member_id), member

    async def _notify(self, room_id: str, state: "MemberRuntime") -> None:
        callback = self._on_state
        if callback is None:
            return
        try:
            result = callback(room_id, state)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - observers must not break dispatch
            pass

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _release(self, state: MemberRuntime) -> None:
        if state.holding_permit:
            state.holding_permit = False
            self._semaphore.release()

    async def _dispatch(self, room: Room, member: RoomMember,
                        message: RoomMessage, delivery: str) -> None:
        state = self._state(room.room_id, member.member_id)
        try:
            # The permit bounds concurrently RUNNING turns (the real resource:
            # each member turn is another agent process), not just sends.
            await self._semaphore.acquire()
            state.holding_permit = True

            session_id = state.session_id or member.session_id
            if not session_id:
                session_id = await self._host.ensure_session(room, member)
                self._store.bind_session(room.room_id, member.member_id, session_id)
            state.session_id = session_id

            state.state = MEMBER_RUNNING
            state.started_at = time.time()
            state.queued = max(0, state.queued - 1)
            await self._notify(room.room_id, state)
            prompt = f"{CONTEXT_HEADER.format(name=room.name, cwd=room.cwd)}\n{message.text}"
            await self._host.dispatch_query(
                session_id, prompt, msg_id=message.msg_id, delivery=delivery)
        except asyncio.CancelledError:
            self._release(state)
            raise
        except Exception as exc:  # noqa: BLE001 - one member must not sink the room
            state.state = MEMBER_FAILED
            state.error = f"{type(exc).__name__}: {exc}"
            state.started_at = None
            state.queued = max(0, state.queued - 1)
            self._release(state)
            await self._notify(room.room_id, state)
            self._store.append_message(room.room_id, RoomMessage(
                msg_id=_new_id("msg"), ts=time.time(), author_kind="member",
                author_label=member.label,
                text=f"派发失败：{state.error}", state=MEMBER_FAILED,
            ))