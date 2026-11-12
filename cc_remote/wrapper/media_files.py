"""Exact-file, session-bound leases for pull-driven media/download reads.

No native listener, directory publication, model call or file copy is involved.
Each lease pins an already-authorized descriptor; concurrent range readers have
independent positions, including on Windows where pread uses lseek internally.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import threading
import time
from uuid import uuid4
from types import SimpleNamespace

from cc_remote.viewer import CHUNK_SIZE
from cc_remote.media import MAX_MEDIA_BYTES
from cc_remote.wrapper.os_compat import open_with_share_delete, pread

MEDIA_TTL = 30 * 60
MAX_MEDIA_LEASES = 64
MEDIA_ID = re.compile(r"^[a-f0-9]{32}$")
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
}


def media_type(path: str, head: bytes) -> str:
    suffix = Path(path).suffix.lower()
    mime = MEDIA_TYPES.get(suffix)
    valid = {
        "video/mp4": head[4:8] == b"ftyp",
        "video/quicktime": head[4:8] in {b"ftyp", b"moov", b"mdat", b"wide"},
        "video/webm": head.startswith(b"\x1aE\xdf\xa3"),
        "video/ogg": head.startswith(b"OggS"),
        "audio/mpeg": head.startswith(b"ID3")
        or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"},
        "audio/mp4": head[4:8] == b"ftyp",
        "audio/aac": len(head) > 1 and head[0] == 255 and head[1] & 0xF6 == 0xF0,
        "audio/wav": head.startswith(b"RIFF") and head[8:12] == b"WAVE",
        "audio/flac": head.startswith(b"fLaC"),
        "audio/ogg": head.startswith(b"OggS"),
        "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": head.startswith(b"\xff\xd8\xff"),
        "image/gif": head.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        "image/avif": head[4:8] == b"ftyp" and b"avif" in head[8:64],
    }
    if not mime or not valid.get(mime):
        raise ValueError("浏览器暂不支持预览此文件，可以下载后在本机打开")
    return mime


@dataclass
class _Lease:
    fd: int
    info: os.stat_result
    path: str
    mime: str
    sid: str
    owner: str
    context_token: str
    expires: float
    cold: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)
    closed: bool = False

    def validate(self):
        if self.closed or self.expires <= time.monotonic():
            raise PermissionError("文件读取已过期，请重新打开")
        now = os.fstat(self.fd)
        if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (
            self.info.st_dev,
            self.info.st_ino,
            self.info.st_size,
            self.info.st_mtime_ns,
        ):
            raise PermissionError("文件已变化，请重新打开")

    def close(self):
        with self.lock:
            if not self.closed:
                os.close(self.fd)
                self.closed = True


class MediaStream:
    def __init__(self, lease: _Lease):
        self.lease = lease
        self.position = 0
        self.closed = False

    def fileno(self):
        return self.lease.fd

    def seek(self, position):
        if type(position) is not int or position < 0:
            raise ValueError("invalid position")
        self.position = position

    def read(self, size):
        if self.closed or not 0 <= size <= CHUNK_SIZE:
            raise ValueError("invalid media read")
        with self.lease.lock:
            self.lease.validate()
            data = pread(self.lease.fd, size, self.position)
            self.position += len(data)
            return data

    def close(self):
        self.closed = True


class MediaFiles:
    def __init__(self, machine):
        self.machine = machine
        self.leases: OrderedDict[str, _Lease] = OrderedDict()

    def prune(self):
        now = time.monotonic()
        for key, lease in list(self.leases.items()):
            if lease.expires <= now or lease.closed:
                self.leases.pop(key).close()

    async def create(self, payload):
        import asyncio

        if (
            not isinstance(payload, dict)
            or set(payload) != {"sid", "path", "owner", "download", "engine", "space"}
            or type(payload["download"]) is not bool
            or (
                payload["engine"] is not None and not isinstance(payload["engine"], str)
            )
            or (payload["space"] is not None and not isinstance(payload["space"], str))
            or any(
                not isinstance(payload[key], str) or not payload[key]
                for key in ("sid", "path", "owner")
            )
            or len(payload["path"].encode("utf-8")) > 4096
        ):
            raise ValueError("invalid media request")
        self.prune()
        if len(self.leases) >= MAX_MEDIA_LEASES:
            raise ValueError("打开的文件过多，请关闭部分预览后重试")
        ctx = self.machine._ctx_for(payload["sid"])
        cold = ctx is None
        if cold:
            if (
                payload["sid"].startswith("btw-")
                or payload["engine"] not in {"claude", "codex"}
                or payload["space"] not in {"code", "work"}
            ):
                raise PermissionError("会话不可用，请重新进入该会话后打开文件")
            from cc_remote.viewer_pages import PageScope

            scope, cwd = await self.machine._viewer_page_scope(
                PageScope(
                    sid=payload["sid"], engine=payload["engine"], space=payload["space"]
                )
            )
            ctx = SimpleNamespace(
                cwd=cwd,
                engine=scope.engine,
                space=scope.space,
                session_id=scope.sid,
                key=scope.sid,
                btw=False,
                preview_snapshot_token="cold:" + self.machine.instance_id,
            )
        if (
            (ctx.btw and ctx.owner_client_id != payload["owner"])
            or payload["engine"] not in {None, ctx.engine}
            or payload["space"] not in {None, ctx.space}
        ):
            raise PermissionError("会话不可用，请重新进入该会话后打开文件")
        requested = os.path.expanduser(payload["path"])
        if requested.startswith(("\\\\", "//")) or (
            os.name == "nt" and ":" in requested[2:]
        ):
            raise ValueError("只支持本机普通文件，不支持网络共享或数据流路径")
        path = os.path.realpath(os.path.join(ctx.cwd, requested))
        inside = self.machine._path_is_below(os.path.realpath(ctx.cwd), path)
        if ctx.space == "code" and not inside:
            await self.machine._run_preview_capability_mutation(
                self.machine._preview_capability_store.grant_path,
                ctx.engine,
                ctx.space,
                self.machine._ctx_wire_sid(ctx),
                path,
                mode="read",
                source="user_approved",
                persist=not ctx.btw,
            )
        capabilities = self.machine._preview_capabilities(ctx)

        def open_file():
            _, _, checked, _ = self.machine._read_session_file(
                ctx.cwd,
                requested,
                allowed_suffixes=None,
                max_bytes=0,
                allow_truncate=True,
                allowed_external_paths=capabilities,
            )
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            fd = open_with_share_delete(path, flags)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.setmode(fd, os.O_BINARY)
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > MAX_MEDIA_BYTES
                    or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    != (
                        checked.st_dev,
                        checked.st_ino,
                        checked.st_size,
                        checked.st_mtime_ns,
                    )
                ):
                    raise ValueError("文件已变化或超过 8 GiB 读取上限")
                mime = (
                    "application/octet-stream"
                    if payload["download"]
                    else media_type(path, pread(fd, 512, 0))
                )
                return _Lease(
                    fd,
                    info,
                    path,
                    mime,
                    ctx.key or ctx.session_id,
                    payload["owner"],
                    ctx.preview_snapshot_token,
                    time.monotonic() + MEDIA_TTL,
                    cold=cold,
                )
            except BaseException:
                os.close(fd)
                raise

        opening = asyncio.create_task(asyncio.to_thread(open_file))
        try:
            lease = await asyncio.shield(opening)
        except asyncio.CancelledError:
            try:
                (await opening).close()
            except Exception:
                pass
            raise
        if not cold and self.machine._ctx_for(payload["sid"]) is not ctx:
            lease.close()
            raise PermissionError("会话已变化，请重新打开文件")
        if len(self.leases) >= MAX_MEDIA_LEASES:
            lease.close()
            raise ValueError("打开的文件过多")
        identity = uuid4().hex
        self.leases[identity] = lease
        return {
            "id": identity,
            "name": os.path.basename(path),
            "size": lease.info.st_size,
            "media_type": lease.mime,
            "expires_in": MEDIA_TTL,
        }

    def open(self, identity: str, owner: str):
        if not isinstance(identity, str) or not MEDIA_ID.fullmatch(identity):
            raise PermissionError("invalid media identity")
        lease = self.leases.get(identity)
        if lease is None or lease.owner != owner:
            raise PermissionError("文件读取已过期，请重新打开")
        ctx = self.machine._ctx_for(lease.sid)
        if (
            not lease.cold
            and (ctx is None or ctx.preview_snapshot_token != lease.context_token)
        ) or (lease.cold and lease.context_token != "cold:" + self.machine.instance_id):
            raise PermissionError("会话已变化，请重新打开文件")
        with lease.lock:
            lease.validate()
            lease.expires = time.monotonic() + MEDIA_TTL
        return MediaStream(lease), lease.info, lease.mime

    def close(self):
        for lease in self.leases.values():
            lease.close()
        self.leases.clear()

    def release(self, identity, owner):
        lease = self.leases.get(identity)
        if lease is not None and lease.owner == owner:
            self.leases.pop(identity).close()
