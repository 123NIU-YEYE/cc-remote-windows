"""Explicit local browser diagnostic: real media channel, stub conversation.

No engine process/model request. Only the explicitly supplied files are readable.
The relay listens on loopback with an ephemeral password saved in private state.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import secrets
from types import SimpleNamespace
from uuid import uuid4

import uvicorn
from PIL import Image, ImageDraw

from cc_remote.config import RelayConfig
from cc_remote.protocol import (
    AskUserSync,
    BackgroundProcessSync,
    CommandAck,
    ContextReport,
    ConversationTurn,
    FilePreview,
    FilesListed,
    Hello,
    History,
    Models,
    PreviewAsset,
    SessionFocus,
    SessionInfo,
    SessionList,
    Snapshot,
)
from cc_remote.relay.server import create_app
from cc_remote.windows_privacy import create_private_file, private_directory
from cc_remote.wrapper.machine import WrapperMachine
from cc_remote.wrapper.media_files import MediaFiles
from cc_remote.wrapper.preview_capabilities import PreviewCapabilityStore
from cc_remote.wrapper.transport import WrapperTransport
from cc_remote.wrapper.viewer_transport import ViewerTransport
from cc_remote.wrapper.workspace_browser import browse_workspace


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=4187)
    args = parser.parse_args()
    state = args.state.resolve()
    private_directory(state)
    video = args.video.resolve(strict=True)
    image = state / "图片预览 sample.png"
    picture = Image.new("RGB", (960, 540), "#284b63")
    ImageDraw.Draw(picture).text(
        (70, 240), "Local image preview / 960 x 540", fill="white"
    )
    picture.save(image)
    allowed = {os.path.realpath(video), os.path.realpath(image)}
    sid = "media-fixture-session"
    ctx = SimpleNamespace(
        key=sid,
        session_id=sid,
        cwd=str(state),
        engine="claude",
        space="code",
        btw=False,
        preview_snapshot_token=uuid4().hex,
    )
    store = PreviewCapabilityStore(state / "capabilities")

    class Machine:
        instance_id = "media-fixture-wrapper"
        _preview_capability_store = store
        _path_is_below = staticmethod(WrapperMachine._path_is_below)

        def _ctx_for(self, key):
            return ctx if key == sid else None

        def _ctx_wire_sid(self, value):
            return value.key

        def _preview_capabilities(self, value):
            return store.snapshot(value.engine, value.space, value.key)

        async def _run_preview_capability_mutation(self, function, *args, **kwargs):
            return await asyncio.to_thread(function, *args, **kwargs)

        def _read_session_file(self, cwd, path, **kwargs):
            if os.path.realpath(os.path.join(cwd, path)) not in allowed:
                raise PermissionError(
                    "diagnostic fixture only serves its supplied files"
                )
            return WrapperMachine._read_session_file(cwd, path, **kwargs)

    origin = f"http://127.0.0.1:{args.port}"
    auth_path = state / "browser-auth.json"
    password = (
        json.loads(auth_path.read_text(encoding="utf-8"))["password"]
        if auth_path.exists()
        else secrets.token_urlsafe(24)
    )
    cfg = RelayConfig(
        login_password=password,
        login_users_json="",
        wrapper_token=secrets.token_urlsafe(32),
        wrapper_tokens_json="",
        session_secret=secrets.token_urlsafe(48),
        public_origin=origin,
        device_db_path=str(state / "devices.sqlite3"),
        push_db_path=str(state / "push.sqlite3"),
        static_dir=str(Path(__file__).resolve().parents[1] / "web/dist"),
        viewer_mode="bridge",
        viewer_origin_template="",
        harmony_push_service_account="",
    )
    app = create_app(cfg)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="error",
            access_log=False,
            ws_max_size=32 * 1024 * 1024,
        )
    )
    transport = WrapperTransport(
        origin.replace("http:", "ws:") + "/ws", cfg.wrapper_token
    )
    machine = Machine()
    media = MediaFiles(machine)
    resources = ViewerTransport(
        transport.url, cfg.wrapper_token, "default", state / "missing.json", media=media
    )
    resources.static_enabled = False
    row = SessionInfo(
        session_id=sid, summary="媒体与文件测试", cwd=str(state), state="idle"
    )
    text = (
        f"本地图片预览：\n\n![本地图片](<{image.as_posix()}>)\n\n"
        f"[打开图片](<{image.as_posix()}>)\n\n"
        f"本地视频：`{video.as_posix()}`\n\n[打开视频](<{video.as_posix()}>) · "
        f"[打开所在文件夹](<{video.parent.as_posix()}/>)"
    )

    async def announce():
        await transport.send(
            Hello(
                role="wrapper",
                machine_id="default",
                wrapper_generation=machine.instance_id,
                cc_session_id=sid,
                state="idle",
            )
        )

    async def conversation():
        transport.on_connected = announce
        await transport.start()
        async for cmd in transport.incoming():
            print(json.dumps({"fixture_command": cmd.type}), flush=True)
            target = dict(
                sid=sid,
                to=getattr(cmd, "client_id", None),
                route_id=getattr(cmd, "route_id", None),
            )
            if cmd.type in {"hello", "switch_session"}:
                await transport.send(
                    Snapshot(
                        cc_session_id=sid,
                        state="idle",
                        cwd=str(state),
                        generation=machine.instance_id,
                        **target,
                    )
                )
                await transport.send(AskUserSync(**target))
                await transport.send(BackgroundProcessSync(items=[], **target))
                if cmd.type == "switch_session":
                    await transport.send(
                        SessionFocus(session_id=sid, cwd=str(state), **target)
                    )
            if cmd.type in {"hello", "list_sessions"}:
                await transport.send(
                    SessionList(
                        engine="claude",
                        sessions=[row],
                        to=cmd.client_id,
                        request_id=getattr(cmd, "cmd_id", None),
                    )
                )
            elif cmd.type == "get_models":
                await transport.send(Models(engine=cmd.engine, models=[], **target))
            elif cmd.type == "get_history":
                await transport.send(
                    History(
                        session_id=sid,
                        revision="fixture-history",
                        generation=machine.instance_id,
                        authoritative=True,
                        detail="summary",
                        turns=[
                            ConversationTurn(
                                id="fixture-turn",
                                prompt="图片和本地视频在哪里？",
                                done=True,
                                blocks=[
                                    dict(
                                        kind="text",
                                        message_id="answer",
                                        text=text,
                                        done=True,
                                    )
                                ],
                            )
                        ],
                        **target,
                    )
                )
            elif cmd.type == "get_context":
                await transport.send(
                    ContextReport(
                        total_tokens=0,
                        max_tokens=0,
                        percentage=0,
                        available=False,
                        request_id=cmd.cmd_id,
                        **target,
                    )
                )
            elif cmd.type == "get_file_preview":
                path = os.path.realpath(os.path.join(ctx.cwd, cmd.path))
                if os.path.isdir(path) and path in {str(state), str(video.parent)}:
                    value = FilePreview(
                        path=path, request_id=cmd.request_id, directory=True, **target
                    )
                elif path in allowed:
                    preview = WrapperMachine._read_file_preview(
                        str(Path(path).parent), Path(path).name
                    )
                    value = FilePreview(
                        path=path,
                        request_id=cmd.request_id,
                        format=preview["format"],
                        data=base64.b64encode(preview["data"]).decode("ascii"),
                        media_type=preview["media_type"],
                        size=preview["size"],
                        **target,
                    )
                else:
                    value = FilePreview(
                        path=cmd.path,
                        request_id=cmd.request_id,
                        error="fixture scope",
                        **target,
                    )
                await transport.send(value)
            elif cmd.type == "get_preview_asset":
                await transport.send(
                    PreviewAsset(
                        path=cmd.path,
                        preview_id=cmd.preview_id,
                        request_id=cmd.request_id,
                        media_type="image/png",
                        data=base64.b64encode(image.read_bytes()).decode("ascii"),
                        **target,
                    )
                )
            elif cmd.type == "browse_files":
                value = browse_workspace(ctx.cwd, cmd.path, confine_to_cwd=False)
                await transport.send(
                    FilesListed(request_id=cmd.request_id, **value, **target)
                )
            if getattr(cmd, "cmd_id", None) and getattr(cmd, "client_id", None):
                await transport.send(
                    CommandAck(
                        cmd_id=cmd.cmd_id,
                        client_id=cmd.client_id,
                        to=cmd.client_id,
                        route_id=cmd.route_id,
                    )
                )

    if not auth_path.exists():
        fd = create_private_file(auth_path)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"origin": origin, "password": password, "video": str(video)}, stream
            )
    tasks = [asyncio.create_task(conversation()), asyncio.create_task(resources.run())]

    def observe(task):
        if not task.cancelled() and task.exception() is not None:
            import traceback

            traceback.print_exception(task.exception())

    for task in tasks:
        task.add_done_callback(observe)
    try:
        await server.serve()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await transport.stop()
        media.close()


if __name__ == "__main__":
    asyncio.run(main())
