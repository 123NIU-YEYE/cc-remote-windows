"""Private native PTY attachments, scoped to one browser and socket lifetime.

Reading catalogs never creates a shell. A UI attachment expires if its reader
leaves. Closing this adapter detaches streams, never closes native processes.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import time

from cc_remote.protocol import DshItem
from cc_remote.wrapper.dsh_client import DshError, native_session_id


@dataclass
class Attachment:
    sid: str
    client: str
    terminal: str
    task: asyncio.Task | None = None
    send: object = None
    frames: deque = field(default_factory=deque)
    cursor: int = 0
    size: int = 0
    touched: float = field(default_factory=time.monotonic)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    writable: bool = False
    error: str = ""
    sequence: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ack: asyncio.Future | None = None


class DshTerminals:
    def __init__(self, runtime):
        self.runtime = runtime
        self.attachments: dict[str, Attachment] = {}
        self.cleanup = None

    @property
    def client(self):
        return self.runtime.client

    async def read(self, cmd):
        if cmd.kind == "terminals":
            rows = await self.client.rpc("terminal/list", {"sessionId": native_session_id(cmd.sid)})
            return [DshItem(id=r["id"], title=r["title"], state=r["state"], detail=r["cwd"],
                data={"cols": r["cols"], "rows": r["rows"]}) for r in rows[:32]], None
        row = self.get(cmd, cmd.query)
        if row.error:
            raise DshError("terminal_disconnected", row.error)
        after = cmd.before_seq or 0
        if after > row.cursor or row.frames and after < row.frames[0][0] - 1:
            raise DshError("terminal_gap", "终端输出已更新，请重新连接以恢复屏幕。")
        # A cursor acknowledges a previously rendered page. Never remove the
        # newly returned page until the next successful read acknowledges it.
        while row.frames and row.frames[0][0] <= after:
            _, _, size = row.frames.popleft()
            row.size -= size
        frames = list(row.frames)[:16]
        return [DshItem(id=str(seq), title="终端帧", data=value) for seq, value, _ in frames], frames[-1][0] if frames else after

    def get(self, cmd, key):
        row = self.attachments.get(key) if isinstance(key, str) else None
        if row is None or (row.sid, row.client) != (cmd.sid, cmd.client_id):
            raise DshError("terminal_expired", "终端连接已过期，请重新连接。")
        row.touched = time.monotonic()
        return row

    async def control(self, cmd):
        try:
            body = json.loads(cmd.value)
            action = body["action"]
        except (ValueError, KeyError, TypeError):
            raise DshError("invalid_terminal", "终端操作无效。") from None
        native = native_session_id(cmd.sid)
        if action in {"create", "attach", "close", "rename"}:
            terminal = body.get("id")
            if not isinstance(terminal, str) or not terminal or len(terminal) > 128:
                raise DshError("invalid_terminal", "终端编号无效。")
            if action == "create":
                # Stable caller identity: a lost response must not create a
                # second process. User can recover through the cold catalog.
                await self.client.rpc("terminal/create", {"agentId": native, "request": {
                    "id": terminal, "cols": 80, "rows": 24}})
                return "终端已创建"
            if action == "close":
                await self.client.rpc("terminal/close", {"agentId": native, "id": terminal})
                for key, row in list(self.attachments.items()):
                    if (row.sid, row.terminal) == (cmd.sid, terminal):
                        await self.detach(key)
                return "终端已停止"
            if action == "rename":
                title = body.get("title")
                if not isinstance(title, str) or not title.strip() or len(title) > 120:
                    raise DshError("invalid_terminal", "请输入 1–120 字的终端名称。")
                await self.client.rpc("terminal/rename", {"agentId": native, "id": terminal, "title": title})
                return "终端已重命名"
            for key, row in list(self.attachments.items()):
                if (row.sid, row.client) == (cmd.sid, cmd.client_id):
                    await self.detach(key)
            if len(self.attachments) >= 16:
                raise DshError("terminal_limit", "终端查看数量已达上限，请关闭其他面板。")
            row = Attachment(cmd.sid, cmd.client_id, terminal)
            self.attachments[cmd.cmd_id] = row
            row.task = asyncio.create_task(self.follow(row))
            if self.cleanup is None or self.cleanup.done():
                self.cleanup = asyncio.create_task(self.expire())
            try:
                await asyncio.wait_for(row.ready.wait(), 10)
                if row.error:
                    raise DshError("terminal_attach", row.error)
            except (TimeoutError, DshError):
                await self.detach(cmd.cmd_id)
                raise DshError("terminal_attach", row.error or "连接终端超时，请刷新终端列表。") from None
            return "终端已连接"
        row = self.get(cmd, body.get("lease"))
        if action == "detach":
            await self.detach(body["lease"])
            return "已收起终端，进程继续保留"
        if action != "input":
            raise DshError("invalid_terminal", "终端操作无效。")
        async with row.lock:
            if row.error or not row.writable or row.send is None:
                raise DshError("terminal_read_only", "终端当前不可输入，请刷新连接状态。")
            item = body.get("value")
            if not isinstance(item, dict):
                raise DshError("invalid_terminal", "终端输入无效。")
            if item.get("type") == "write":
                data = item.get("data")
                if not isinstance(data, str) or len(data.encode()) > 4096:
                    raise DshError("terminal_input_size", "单次终端输入过大。")
                value = {"type": "write", "data": data}
            elif item.get("type") == "resize" and type(item.get("cols")) is int and type(item.get("rows")) is int:
                if not 2 <= item["cols"] <= 500 or not 1 <= item["rows"] <= 200:
                    raise DshError("terminal_dimensions", "终端尺寸无效。")
                value = {"type": "resize", "cols": item["cols"], "rows": item["rows"]}
            else:
                raise DshError("invalid_terminal", "终端输入无效。")
            row.sequence += 1
            value["sequence"] = row.sequence
            row.ack = asyncio.get_running_loop().create_future()
            try:
                await row.send(value)
                refusal = await asyncio.wait_for(row.ack, 10)
                if refusal:
                    raise DshError("terminal_refused", "DSH 拒绝了这次输入；输入不会重发。")
            except (TimeoutError, OSError):
                row.error = "终端输入结果未确认，请重新连接；输入不会重发。"
                row.writable = False
                raise DshError("terminal_input_unknown", row.error, outcome_unknown=True) from None
            finally:
                row.ack = None
        return ""

    async def follow(self, row):
        try:
            async for value in self.client.stream("terminal/attach", {"agentId": native_session_id(row.sid), "id": row.terminal},
                    on_open=lambda send: setattr(row, "send", send)):
                kind = value.get("type")
                if kind == "ack":
                    if value.get("sequence") == row.sequence and row.ack and not row.ack.done():
                        row.ack.set_result(value.get("refusal"))
                    continue
                if kind not in {"snapshot", "output", "state"}:
                    raise DshError("terminal_frame", "终端数据格式不兼容。")
                if kind in {"snapshot", "state"}:
                    row.writable = value["writable"]
                # Split only text payloads, preserving complete order and ANSI
                # sequences across chunks; xterm consumes them as one stream.
                field = "screen" if kind == "snapshot" else "data"
                text = value.get(field, "")
                parts = [text[i:i + 8192] for i in range(0, len(text), 8192)] or [""]
                for index, part in enumerate(parts):
                    frame = {**value, field: part} if index == 0 else {"type": "output", "data": part}
                    size = len(json.dumps(frame).encode())
                    if row.size + size > 2 * 1024 * 1024:
                        raise DshError("terminal_overflow", "终端输出积压，请重新连接以恢复屏幕。")
                    row.cursor += 1
                    row.frames.append((row.cursor, frame, size))
                    row.size += size
                if kind == "snapshot":
                    row.ready.set()
            row.error = "终端连接已结束，可重新连接查看保留的屏幕。"
        except DshError as exc:
            row.error = str(exc)
        except (ValueError, TypeError, KeyError):
            row.error = "终端数据格式不兼容。"
        finally:
            row.writable = False
            row.ready.set()
            if row.ack and not row.ack.done():
                row.ack.set_result("disconnected")

    async def detach(self, key):
        row = self.attachments.pop(key, None)
        if row and row.task:
            row.task.cancel()
            await asyncio.gather(row.task, return_exceptions=True)

    async def expire(self):
        while self.attachments:
            await asyncio.sleep(10)
            for key, row in list(self.attachments.items()):
                if time.monotonic() - row.touched > 30:
                    await self.detach(key)

    async def close(self):
        if self.cleanup:
            self.cleanup.cancel()
            await asyncio.gather(self.cleanup, return_exceptions=True)
        await asyncio.gather(*(self.detach(key) for key in list(self.attachments)))
