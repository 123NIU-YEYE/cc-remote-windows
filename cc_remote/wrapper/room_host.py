"""Bind collaboration rooms to the wrapper's session pool.

The room layer never starts processes or talks to a model: it only needs four
capabilities, and each maps onto an entry point the wrapper already has. Keeping
this adapter thin also keeps ``rooms.py`` testable without Engines.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from cc_remote import protocol
from cc_remote.wrapper.rooms import Room, RoomMember

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
        # A member may already name the session it drives. That is the only way a
        # DSH member can work, and it lets any member adopt an existing
        # conversation instead of starting a blank one.
        if member.session_id:
            return member.session_id

        # DSH is not spawnable: its runtime only adopts an existing native session
        # (see DshRuntime._ctx), and _spawn has no dsh branch, so passing "dsh"
        # there fails an internal claude-profile assertion with an empty message
        # (which surfaced in the room as "失败 AssertionError:").
        engine = (member.engine or "claude").lower()
        if engine not in self.SPAWNABLE_ENGINES:
            raise RoomHostError(
                f"无法为「{member.label}」新建 {engine.upper()} 会话："
                f"{engine.upper()} 成员需要先绑定一个已存在的会话"
                "（房间只能自动创建 Claude / Codex 会话）"
            )

        ctx = await self._machine._spawn(
            None,
            cwd=room.cwd,
            engine=engine,
            claude_profile_id=(member.profile_id if engine == "claude" else None),
            codex_profile_id=(member.profile_id if engine == "codex" else None),
            space="code",
            raise_on_failure=True,
        )
        if ctx is None:
            raise RoomHostError(f"无法为「{member.label}」创建会话")
        session_id = self._machine._ctx_wire_sid(ctx)
        if not session_id:
            raise RoomHostError(f"「{member.label}」的会话没有可用的编号")
        return session_id

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
