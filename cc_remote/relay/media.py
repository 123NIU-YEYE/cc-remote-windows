"""Cookie-authenticated HTTP ranges over the existing binary resource socket.

Resource IDs are public correlation identifiers, never bearer credentials.
Every request rechecks the browser session, exact device scope and socket
generation. No file bytes are buffered in full, persisted, or sent over chat.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import re
import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from cc_remote.config import valid_machine_id
from cc_remote.media import MAX_MEDIA_BYTES
from cc_remote.relay.viewer import ViewerError

_ID = re.compile(r"^[a-f0-9]{32}$")
_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/wav",
    "audio/flac",
    "audio/ogg",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "application/octet-stream",
}


@dataclass
class _Entry:
    id: str
    machine: str
    peer: object
    owner: str
    jti: str
    name: str
    mime: str
    expires: float
    download: bool


class MediaRelay:
    def __init__(
        self,
        app,
        viewers,
        claims,
        session_active,
        allow_machine,
        origin_allowed,
        owner_id,
        read_json,
    ):
        self.viewers = viewers
        self.claims = claims
        self.session_active = session_active
        self.allow_machine = allow_machine
        self.origin_allowed = origin_allowed
        self.owner_id = owner_id
        self.read_json = read_json
        self.entries: OrderedDict[str, _Entry] = OrderedDict()
        self.slots = asyncio.Semaphore(16)
        app.add_api_route("/api/media/open", self.create, methods=["POST"])
        app.add_api_route("/api/media/{identity}", self.read, methods=["GET", "HEAD"])
        app.add_api_route("/api/media/{identity}", self.delete, methods=["DELETE"])

    def prune(self):
        for key, entry in list(self.entries.items()):
            if entry.expires <= time.monotonic() or entry.peer.closed:
                self.entries.pop(key)

    async def authorize(self, req, entry=None):
        claims = await self.claims(req)
        if claims is None:
            raise ViewerError(401, "unauthorized")
        if not self.origin_allowed(req) or req.headers.get("sec-fetch-site") not in {
            None,
            "same-origin",
            "none",
        }:
            raise ViewerError(403, "origin_rejected")
        if entry is not None and (
            claims.jti != entry.jti
            or entry.expires <= time.monotonic()
            or not await self.allow_machine(claims, entry.machine)
            or self.viewers.peers.get(entry.machine) is not entry.peer
        ):
            raise ViewerError(403, "media_expired")
        if entry is not None:
            entry.expires = time.monotonic() + 1800
        return claims

    @staticmethod
    def error(exc):
        return JSONResponse(
            {"error": exc.code},
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
        )

    async def create(self, req: Request):
        entry = None
        try:
            claims = await self.authorize(req)
            if not req.headers.get("origin") or self.slots.locked():
                raise ViewerError(
                    429 if self.slots.locked() else 403, "media_unavailable"
                )
            async with self.slots:
                body = await asyncio.wait_for(self.read_json(req, 8 * 1024), 10)
                if (
                    not isinstance(body, dict)
                    or set(body)
                    != {"machine_id", "sid", "path", "download", "engine", "space"}
                    or not isinstance(body["machine_id"], str)
                    or not valid_machine_id(body["machine_id"])
                    or not isinstance(body["sid"], str)
                    or not _SID.fullmatch(body["sid"])
                    or not isinstance(body["path"], str)
                    or not body["path"]
                    or "\0" in body["path"]
                    or len(body["path"].encode("utf-8")) > 4096
                    or type(body["download"]) is not bool
                    or (
                        body["engine"] is not None
                        and not isinstance(body["engine"], str)
                    )
                    or (
                        body["space"] is not None and not isinstance(body["space"], str)
                    )
                    or body["engine"] not in {None, "claude", "codex", "dsh"}
                    or body["space"] not in {None, "code", "work"}
                ):
                    raise ViewerError(400, "invalid_media_request")
                if not await self.allow_machine(claims, body["machine_id"]):
                    raise ViewerError(403, "machine_not_authorized")
                peer = self.viewers.peers.get(body["machine_id"])
                if peer is None or peer.closed or not peer.media:
                    raise ViewerError(503, "media_device_offline")
                self.prune()
                if (
                    len(self.entries) >= 256
                    or sum(e.jti == claims.jti for e in self.entries.values()) >= 32
                ):
                    raise ViewerError(429, "media_capacity")
                owner = self.owner_id(claims)
                result = await peer.metadata(
                    "media_open",
                    {
                        "sid": body["sid"],
                        "path": body["path"],
                        "download": body["download"],
                        "engine": body["engine"],
                        "space": body["space"],
                        "owner": owner,
                    },
                )
                if isinstance(result, dict) and isinstance(result.get("error"), str):
                    return JSONResponse(
                        {"error": result["error"][:512]}, status_code=422
                    )
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("id"), str)
                    or not _ID.fullmatch(result["id"])
                    or not isinstance(result.get("name"), str)
                    or len(result["name"]) > 1024
                    or type(result.get("size")) is not int
                    or not 0 <= result["size"] <= MAX_MEDIA_BYTES
                    or result.get("media_type") not in _TYPES
                ):
                    raise ViewerError(502, "invalid_media_metadata")
                entry = _Entry(
                    result["id"],
                    body["machine_id"],
                    peer,
                    owner,
                    claims.jti,
                    result["name"],
                    result["media_type"],
                    time.monotonic() + 1800,
                    body["download"],
                )
                # Logout/device revocation or a replacement Wrapper can race an
                # awaited OS open. Recheck before publishing the resource ID.
                await self.authorize(req, entry)
                if entry.id in self.entries:
                    raise ViewerError(502, "invalid_media_metadata")
                if (
                    len(self.entries) >= 256
                    or sum(e.jti == claims.jti for e in self.entries.values()) >= 32
                ):
                    await peer.metadata("media_close", {"id": entry.id, "owner": owner})
                    raise ViewerError(429, "media_capacity")
                self.entries[entry.id] = entry
                return JSONResponse(
                    {
                        "id": entry.id,
                        "url": "/api/media/" + entry.id,
                        "name": entry.name,
                        "size": result["size"],
                        "mediaType": entry.mime,
                    },
                    headers={"Cache-Control": "no-store"},
                )
        except ViewerError as exc:
            if entry is not None and entry.id not in self.entries:
                try:
                    await asyncio.wait_for(
                        entry.peer.metadata(
                            "media_close", {"id": entry.id, "owner": entry.owner}
                        ),
                        2,
                    )
                except Exception:
                    pass
            return self.error(exc)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return self.error(ViewerError(400, "invalid_media_request"))

    async def read(self, identity: str, req: Request):
        request_id = None
        entry = self.entries.get(identity)
        try:
            await self.authorize(req)
            if entry is None or not _ID.fullmatch(identity):
                raise ViewerError(404, "media_expired")
            await self.authorize(req, entry)
            headers = {
                k: req.headers[k]
                for k in ("range", "if-none-match", "if-range")
                if k in req.headers
            }
            if any(len(value) > 256 for value in headers.values()):
                raise ViewerError(400, "invalid_range")
            request_id, status, headers = await entry.peer.read(
                None, "", req.method, headers, media_id=entry.id, owner=entry.owner
            )
            await self.authorize(req, entry)
            headers.update(
                {
                    "cache-control": "no-store",
                    "x-content-type-options": "nosniff",
                    "cross-origin-resource-policy": "same-origin",
                    "content-security-policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
                }
            )
            headers["content-type"] = entry.mime
            if entry.download:
                headers["content-disposition"] = (
                    "attachment; filename*=UTF-8''" + quote(entry.name, safe="")
                )
                headers["content-type"] = "application/octet-stream"
            if req.method == "HEAD" or status in {304, 416}:
                await entry.peer.cancel(request_id)
                return Response(status_code=status, headers=headers)

            async def authorized():
                await self.authorize(req, entry)

            stream = entry.peer.stream(request_id, authorized)

            # StreamingResponse closes the upstream generator on disconnect;
            # its finally cancels the exact device read and returns the slot.
            async def body():
                try:
                    async for chunk in stream:
                        yield chunk
                finally:
                    await stream.aclose()

            return StreamingResponse(body(), status_code=status, headers=headers)
        except ViewerError as exc:
            if request_id is not None:
                await entry.peer.cancel(request_id)
            return self.error(exc)
        except BaseException:
            if request_id is not None:
                await entry.peer.cancel(request_id)
            raise

    async def delete(self, identity: str, req: Request):
        try:
            await self.authorize(req)
            if not req.headers.get("origin"):
                raise ViewerError(403, "origin_rejected")
            entry = self.entries.get(identity)
            if entry is None:
                return JSONResponse({"ok": True})
            await self.authorize(req, entry)
            self.entries.pop(identity, None)
            await entry.peer.metadata(
                "media_close", {"id": identity, "owner": entry.owner}
            )
            return JSONResponse({"ok": True})
        except ViewerError as exc:
            return self.error(exc)
