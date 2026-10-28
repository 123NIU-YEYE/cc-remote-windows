"""Bind collaboration rooms to the wrapper's session pool.

The room layer never starts processes or talks to a model: it only needs four
capabilities, and each maps onto an entry point the wrapper already has. Keeping
this adapter thin also keeps ``rooms.py`` testable without Engines.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from cc_remote import protocol
from cc_remote.log import logger
from cc_remote.wrapper.rooms import Room, RoomMember

log = logger("cc_remote.wrapper.room_host")

# Rooms have no browser client behind them. Session events are broadcast to every
# client, so a fixed synthetic id keeps reliable-command bookkeeping happy
# without routing anything to a client that does not exist.
_ROOM_CLIENT_ID = "room"


class RoomHostError(RuntimeError):
    """A member cannot be given a session, with a message fit for the timeline."""


class MachineRoomHost:
    """``RoomHost`` implementation backed by ``WrapperMachine``."""

    # Engines whose sessions the wrapper can create from just a directory.
    SPAWNABLE_ENGINES = ("claude", "codex")

    def __init__(self, machine: Any):
        self._machine = machine

    async def ensure_session(self, room: Room, member: RoomMember) -> str:
        engine = (member.engine or "claude").lower()

        # A member may already name the session it drives. That is the only way a
        # DSH member can work, and it lets any member adopt an existing
        # conversation instead of starting a blank one.
        if member.session_id:
            if self._is_resident(member.session_id):
                return member.session_id
            # The binding outlived its session (wrapper restarted, session evicted,
            # or it belonged to an older process). Reusing the id blindly left the
            # member "处理中" forever, because a rejected query only emits an Error
            # frame and never reaches a turn terminal.
            if engine in self.SPAWNABLE_ENGINES:
                resumed = await self._spawn_for(room, member, engine,
                                                resume_id=member.session_id)
                if resumed:
                    return resumed
                # Resume failed: the session is genuinely gone. Falling back to a
                # fresh one keeps the member usable; the room timeline still holds
                # the earlier turns, so the loss is visible rather than silent.
                log.warning("room member session could not be resumed",
                            member_id=member.member_id,
                            session_id=member.session_id)
                created = await self._spawn_for(room, member, engine, resume_id=None)
                if created:
                    return created
            elif engine == "dsh":
                # DSH cannot spawn, but a native session the desktop app knows can
                # be adopted: _dsh._ctx() reads its history and registers a context.
                # Without this the residency check above rejects every DSH member,
                # which is exactly what it did on the first live run.
                return await self._adopt_dsh(member)
            raise RoomHostError(
                f"「{member.label}」绑定的会话已失效，请重新绑定或移除该成员")

        # DSH is not spawnable: its runtime only adopts an existing native session
        # (see DshRuntime._ctx), and _spawn has no dsh branch, so passing "dsh"
        # there fails an internal claude-profile assertion with an empty message
        # (which surfaced in the room as "失败 AssertionError:").
        if engine not in self.SPAWNABLE_ENGINES:
            raise RoomHostError(
                f"无法为「{member.label}」新建 {engine.upper()} 会话："
                f"{engine.upper()} 成员需要先绑定一个已存在的会话"
                "（房间只能自动创建 Claude / Codex 会话）"
            )

        created = await self._spawn_for(room, member, engine, resume_id=None)
        if not created:
            raise RoomHostError(f"无法为「{member.label}」创建会话")
        return created

    async def _adopt_dsh(self, member: RoomMember) -> str:
        """Take over an existing native DSH session so it can be dispatched.

        DSH has no spawn path: its runtime only adopts native sessions, and the
        context is what makes the session addressable at all.
        """
        try:
            ctx = await self._machine._dsh._ctx(member.session_id)
        except Exception as exc:  # noqa: BLE001 - report, never crash the room
            raise RoomHostError(
                f"「{member.label}」绑定的 DSH 会话无法接管：{exc}") from exc
        session_id = self._machine._ctx_wire_sid(ctx) or member.session_id
        if not self._is_resident(session_id):
            raise RoomHostError(f"「{member.label}」的 DSH 会话没有被接管")
        return session_id

    def _is_resident(self, session_id: str) -> bool:
        """True when the wrapper still holds a live context for this id."""
        try:
            return self._machine._ctx_for(session_id) is not None
        except Exception:  # noqa: BLE001 - an unknown id is simply not resident
            return False

    async def _spawn_for(self, room: Room, member: RoomMember, engine: str,
                         *, resume_id: Optional[str]) -> Optional[str]:
        ctx = await self._machine._spawn(
            resume_id,
            cwd=room.cwd,
            engine=engine,
            claude_profile_id=(member.profile_id if engine == "claude" else None),
            codex_profile_id=(member.profile_id if engine == "codex" else None),
            # Per-member controls: advice can run on a strong model while
            # execution runs on a cheap one. _spawn already accepts both.
            model=member.model,
            effort=member.effort,
            space="code",
            raise_on_failure=False,
        )
        if ctx is None:
            return None
        session_id = self._machine._ctx_wire_sid(ctx)
        return session_id or None

    async def dispatch_query(self, session_id: str, text: str, *,
                             msg_id: str, delivery: str) -> None:
        await self._machine._handle_query(protocol.Query(
            sid=session_id,
            prompt=text,
            msg_id=msg_id,
            # Deferred delivery lets the wrapper own the turn even if every
            # browser disconnects, which is what a room wants.
            delivery="queue" if delivery != "immediate" else "immediate",
            cmd_id=f"room-{msg_id}",
            client_id=_ROOM_CLIENT_ID,
        ))

    async def steer_session(self, session_id: str, text: str) -> None:
        await self._machine._handle_steer(protocol.Steer(
            sid=session_id,
            prompt=text,
            msg_id=f"room-steer-{uuid.uuid4().hex}",
            cmd_id=f"room-steer-{uuid.uuid4().hex}",
            client_id=_ROOM_CLIENT_ID,
        ))

    async def cancel_session(self, session_id: str) -> None:
        await self._machine._handle_interrupt(protocol.Interrupt(
            sid=session_id,
            cmd_id=f"room-cancel-{uuid.uuid4().hex}",
            client_id=_ROOM_CLIENT_ID,
        ))
