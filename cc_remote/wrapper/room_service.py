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
from cc_remote.wrapper.room_flow import PlanStateError, discard
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
        # A pre-bound session is how a DSH member can work at all.
        session_id=spec.session_id,
        model=spec.model,
        effort=spec.effort,
        is_default=spec.is_default,
    )


class RoomTurnCollector:
    """Reassemble a member turn's assistant text from streaming deltas.

    The wire has no "assistant message" frame to read back later: text arrives as
    ``Delta`` chunks, and ``Delta.replace`` rewrites the message prefix instead of
    appending. The room only needs the final text, so keep the chunks per message
    and hand back the last non-empty one at the turn terminal.
    """

    def __init__(self, max_messages: int = 32) -> None:
        self._max_messages = max(1, max_messages)
        # session_id -> {message_id -> text} in first-seen order
        self._text: dict[str, dict[str, str]] = {}

    def observe(self, session_id: str, frame: Any) -> Optional[str]:
        """Consume one frame; return the final text when the turn ends."""
        kind = getattr(frame, "type", None)
        if kind == "assistant_msg_start":
            bucket = self._text.setdefault(session_id, {})
            bucket[str(frame.message_id)] = ""
            self._trim(bucket)
            return None
        if kind == "delta":
            bucket = self._text.setdefault(session_id, {})
            key = str(frame.message_id)
            chunk = frame.text or ""
            if getattr(frame, "replace", False):
                bucket[key] = chunk
            else:
                bucket[key] = bucket.get(key, "") + chunk
            self._trim(bucket)
            return None
        if kind == "turn_end":
            return self.pop_final(session_id)
        return None

    def pop_final(self, session_id: str) -> str:
        bucket = self._text.pop(session_id, {})
        for text in reversed(list(bucket.values())):
            if text.strip():
                return text.strip()
        return ""

    def rebind(self, old_session: str, session_id: str) -> None:
        """Move buffered text when a session gets its real id.

        Deltas arrive under the temporary key and the terminal arrives under the
        real one, so without this the room would collect nothing at all.
        """
        if not old_session or not session_id or old_session == session_id:
            return
        moved = self._text.pop(old_session, None)
        if not moved:
            return
        bucket = self._text.setdefault(session_id, {})
        for message_id, text in moved.items():
            bucket[message_id] = bucket.get(message_id, "") + text
        self._trim(bucket)

    def forget(self, session_id: str) -> None:
        self._text.pop(session_id, None)

    def _trim(self, bucket: dict[str, str]) -> None:
        while len(bucket) > self._max_messages:
            bucket.pop(next(iter(bucket)), None)


def _cwd_exists(cwd: str) -> bool:
    """A room whose directory is missing can never dispatch, so refuse it early."""
    try:
        return Path(cwd).expanduser().is_dir()
    except OSError:
        return False

class RoomService:
    """Owns room state for one wrapper machine and emits its frames."""

    def __init__(self, machine: Any, state_dir: Path | str):
        self._machine = machine
        self.store = RoomStore(state_dir)
        self._dispatcher = RoomDispatcher(
            self.store, MachineRoomHost(machine),
            on_state=self._on_member_state,
            on_message=self._on_member_message,
            on_plan=self._on_plan_state)
        self._templates: dict[str, Room] = {}
        self._turns = RoomTurnCollector()

    # ---- transport -----------------------------------------------------
    async def _send(self, frame) -> None:
        await self._machine.transport.send(frame)

    async def _error(self, cmd, message: str, code: str = protocol.ERR_BAD_PROMPT) -> None:
        await self._send(protocol.Error(
            code=code, message=message,
            request_id=getattr(cmd, "cmd_id", None),
            to=getattr(cmd, "client_id", None),
        ))

    async def _on_member_message(self, room_id: str, message) -> None:
        """A dispatcher-written entry (a failed dispatch) must reach the client."""
        await self._send(protocol.RoomMessageEvent(
            room_id=room_id, message=message.as_dict()))

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
        if not _cwd_exists(cmd.cwd):
            return await self._error(cmd, f"目录不存在：{cmd.cwd}")
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
            if cmd.cwd is not None and not _cwd_exists(cmd.cwd):
                return await self._error(cmd, f"目录不存在：{cmd.cwd}")
            room = self.store.update(
                cmd.room_id, name=cmd.name, cwd=cmd.cwd, members=members)
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

    # ---- session identity -----------------------------------------------
    def rebind_session(self, old_key: str, session_id: str) -> None:
        """Follow a spawned session once the engine reveals its real id.

        Synchronous because the wrapper records aliases from a sync method;
        the room_state refresh is scheduled separately.
        """
        if not old_key or not session_id or old_key == session_id:
            return
        self._turns.rebind(old_key, session_id)
        moved = self.store.rebind_session(old_key, session_id)
        for room_id in moved:
            for state in self._dispatcher.runtime(room_id):
                if state.session_id == old_key:
                    state.session_id = session_id
        if moved:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # Called from a sync context (alias bookkeeping during tests or
                # shutdown); the next state frame will carry the new id anyway.
                return
            loop.create_task(self._refresh_states(moved))

    async def _refresh_states(self, room_ids: list[str]) -> None:
        for room_id in room_ids:
            for state in self._dispatcher.runtime(room_id):
                try:
                    await self._on_member_state(room_id, state)
                except Exception:  # noqa: BLE001 - presentation only
                    pass

    # ---- plan / execute -------------------------------------------------
    async def _on_plan_state(self, room_id: str, plan) -> None:
        await self._send(self._plan_frame(room_id, plan))

    @staticmethod
    def _plan_frame(room_id: str, plan) -> protocol.RoomPlanState:
        return protocol.RoomPlanState(
            room_id=room_id,
            phase=plan.phase,
            author_member=plan.author_member,
            source_msg_id=plan.source_msg_id,
            summary=plan.summary,
            steps=[step.as_dict() for step in plan.steps],
            executor_member=plan.executor_member,
        )

    async def on_approve_plan(self, cmd) -> None:
        try:
            room = await self._dispatcher.approve_plan(
                cmd.room_id, cmd.executor_member)
        except RoomError as exc:
            return await self._error(cmd, str(exc))
        await self.on_get_room(protocol.GetRoom(
            cmd_id=getattr(cmd, "cmd_id", None), room_id=room.room_id))

    async def on_discard_plan(self, cmd) -> None:
        try:
            room = await self._dispatcher.discard_plan(cmd.room_id)
        except RoomError as exc:
            return await self._error(cmd, str(exc))
        await self.on_get_room(protocol.GetRoom(
            cmd_id=getattr(cmd, "cmd_id", None), room_id=room.room_id))

    async def on_set_flow(self, cmd) -> None:
        room = self.store.get(cmd.room_id)
        if room is None:
            return await self._error(cmd, "房间不存在")
        room.advise_execute = bool(cmd.advise_execute)
        if not room.advise_execute:
            # Leaving advise mode must not strand a pending approval.
            try:
                room.plan_state = discard(room.plan_state)
            except PlanStateError:
                pass
        self.store.save(room.room_id)
        await self._send(self._plan_frame(room.room_id, room.plan_state))
        await self.on_get_room(protocol.GetRoom(
            cmd_id=getattr(cmd, "cmd_id", None), room_id=room.room_id))

    # ---- turn results --------------------------------------------------    # ---- turn results --------------------------------------------------
    def observe_frame(self, session_id: str, frame: Any) -> Optional[str]:
        """Feed one session frame; return the final text at a turn terminal."""
        return self._turns.observe(session_id, frame)

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
