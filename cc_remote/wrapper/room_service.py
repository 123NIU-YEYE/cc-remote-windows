"""Wire-level service for collaboration rooms.

Sits between the command router and :class:`RoomDispatcher`: it translates room
commands into store/dispatcher calls and turns their effects into protocol
frames. The machine only needs eight thin ``_handle_*`` methods plus a lazy
accessor, so the feature stays out of the session machinery.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

from cc_remote import protocol
from cc_remote.log import logger
from cc_remote.wrapper.room_host import MachineRoomHost
from cc_remote.wrapper.rooms import (
    MESSAGE_LIMIT,
    Room,
    RoomDispatcher,
    RoomError,
    RoomMember,
    RoomMessage,
    RoomStore,
)

log = logger("cc_remote.wrapper.rooms")


def _spec_to_member(spec: protocol.RoomMemberSpec) -> RoomMember:
    return RoomMember(
        member_id=spec.member_id,
        label=spec.label,
        engine=spec.engine,
        profile_id=spec.profile_id,
        is_default=spec.is_default,
    )


class RoomService:
    """Owns room state for one wrapper machine and emits its frames."""

    def __init__(self, machine: Any, state_dir: Path | str):
        self._machine = machine
        self.store = RoomStore(state_dir)
        self._dispatcher = RoomDispatcher(
            self.store, MachineRoomHost(machine), on_state=self._on_member_state)
        self._templates: dict[str, Room] = {}

    # ---- transport -----------------------------------------------------
    async def _send(self, frame) -> None:
        await self._machine.transport.send(frame)

    async def _error(self, cmd, message: str, code: str = protocol.ERR_BAD_PROMPT) -> None:
        await self._send(protocol.Error(
            code=code, message=message,
            request_id=getattr(cmd, "cmd_id", None),
            to=getattr(cmd, "client_id", None),
        ))

    async def _on_member_state(self, room_id: str, state) -> None:
        await self._send(protocol.RoomState(
            room_id=room_id,
            member_id=state.member_id,
            session_id=state.session_id,
            state=state.state,
            queued=state.queued,
            started_at=state.started_at,
            error=state.error,
        ))

    # ---- shapes --------------------------------------------------------
    @staticmethod
    def _brief(room: Room) -> dict:
        """Sidebar shape: members plus only the newest timeline entry."""
        payload = room.as_dict()
        payload["messages"] = payload["messages"][-1:]
        payload["message_count"] = len(room.messages)
        return payload

    @staticmethod
    def _window(room: Room, before: Optional[float], limit: int) -> list[dict]:
        messages = room.messages
        if before is not None:
            messages = [item for item in messages if item.ts < before]
        return [item.as_dict() for item in messages[-max(1, min(limit, MESSAGE_LIMIT)):]]

    # ---- reads ---------------------------------------------------------
    async def on_list_rooms(self, cmd) -> None:
        await self._send(protocol.RoomList(
            request_id=getattr(cmd, "cmd_id", None),
            rooms=[self._brief(room) for room in self.store.list_rooms()],
        ))

    async def on_get_room(self, cmd) -> None:
        room = self.store.get(cmd.room_id)
        if room is None:
            return await self._error(cmd, "房间不存在")
        await self._send(protocol.RoomSnapshot(
            request_id=getattr(cmd, "cmd_id", None),
            room={**room.as_dict(),
                  "messages": self._window(room, cmd.before, cmd.limit)},
            states=[state.as_dict()
                    for state in self._dispatcher.runtime(room.room_id)],
        ))

    # ---- mutations -----------------------------------------------------
    async def on_create_room(self, cmd) -> None:
        try:
            room = self.store.create(
                cmd.name, cmd.cwd, [_spec_to_member(spec) for spec in cmd.members])
        except RoomError as exc:
            return await self._error(cmd, str(exc))
        await self.on_list_rooms(cmd)
        await self._send(protocol.RoomSnapshot(
            request_id=getattr(cmd, "cmd_id", None),
            room={**room.as_dict(), "messages": self._window(room, None, MESSAGE_LIMIT)},
            states=[state.as_dict() for state in self._dispatcher.runtime(room.room_id)],
        ))

    async def on_update_room(self, cmd) -> None:
        members = (None if cmd.members is None
                   else [_spec_to_member(spec) for spec in cmd.members])
        try:
            room = self.store.update(cmd.room_id, name=cmd.name, members=members)
        except RoomError as exc:
            return await self._error(cmd, str(exc))
        await self.on_list_rooms(cmd)
        await self._send(protocol.RoomSnapshot(
            request_id=getattr(cmd, "cmd_id", None),
            room={**room.as_dict(), "messages": self._window(room, None, MESSAGE_LIMIT)},
            states=[state.as_dict() for state in self._dispatcher.runtime(room.room_id)],
        ))

    async def on_delete_room(self, cmd) -> None:
        if not self.store.delete(cmd.room_id):
            return await self._error(cmd, "房间不存在")
        await self._send(protocol.RoomInvalidated(room_id=cmd.room_id))
        await self.on_list_rooms(cmd)

    # ---- conversation --------------------------------------------------
    async def on_post(self, cmd) -> None:
        try:
            message = await self._dispatcher.post(
                cmd.room_id, cmd.text, delivery=cmd.delivery)
        except RoomError as exc:
            return await self._error(cmd, str(exc))
        await self._send(protocol.RoomMessageEvent(
            room_id=cmd.room_id, message=message.as_dict()))

    async def on_steer(self, cmd) -> None:
        try:
            await self._dispatcher.steer(cmd.room_id, cmd.member_id, cmd.text)
        except RoomError as exc:
            return await self._error(cmd, str(exc))

    async def on_cancel(self, cmd) -> None:
        try:
            await self._dispatcher.cancel(cmd.room_id, cmd.member_id)
        except RoomError as exc:
            return await self._error(cmd, str(exc))

    # ---- turn results --------------------------------------------------
    def member_for_session(self, session_id: str) -> Optional[tuple[str, str]]:
        """Map a live session back to (room_id, member_id), if a room owns it."""
        for room in self.store.list_rooms():
            for member in room.members:
                if member.session_id == session_id:
                    return room.room_id, member.member_id
        return None

    async def on_turn_terminal(self, session_id: str, text: str, *,
                               ok: bool = True) -> None:
        """Collect a finished member turn into its room timeline."""
        found = self.member_for_session(session_id)
        if found is None:
            return
        room_id, member_id = found
        message = await self._dispatcher.on_turn_terminal(
            room_id, member_id, text, ok=ok)
        if message is not None:
            await self._send(protocol.RoomMessageEvent(
                room_id=room_id, message=message.as_dict()))
