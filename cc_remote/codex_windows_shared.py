"""Windows Desktop bridge to the official account-scoped Codex daemon.

The bridge forwards raw bytes through `codex app-server proxy`; it never owns a
thread, model request, credential, or second app-server. Existing Apps are never
stopped or replaced. The launch override remains version-dependent App behavior.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from cc_remote.wrapper.codex_daemon import CodexDaemonManager, _managed_daemon_process_identity
from cc_remote.wrapper.codex_runtime import codex_env, resolve_codex_bin
from cc_remote.wrapper.process_scan import (
    process_command_environment_value, process_identity, process_owner_uid,
)


def same_user_peer(peer_port: int, port: int) -> bool:
    import psutil

    try:
        owner = psutil.Process().username().casefold()
        for connection in psutil.net_connections(kind="tcp"):
            if (connection.pid and connection.laddr and connection.raddr
                    and connection.laddr.ip == "127.0.0.1"
                    and connection.laddr.port == peer_port
                    and connection.raddr.ip == "127.0.0.1"
                    and connection.raddr.port == port):
                return psutil.Process(connection.pid).username().casefold() == owner
    except (psutil.Error, OSError):
        pass
    return False


def websocket_upgrade(header: bytes, port: int) -> bool:
    try:
        lines = header.decode("ascii").split("\r\n")
        if lines[0] != "GET / HTTP/1.1":
            return False
        fields: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            if key in fields:
                return False
            fields[key] = value.strip()
        return bool(
            "origin" not in fields
            and fields.get("host") == f"127.0.0.1:{port}"
            and fields.get("upgrade", "").lower() == "websocket"
            and "upgrade" in {value.strip() for value in fields.get("connection", "").lower().split(",")}
            and fields.get("sec-websocket-version") == "13"
            and fields.get("sec-websocket-key")
        )
    except (UnicodeError, ValueError):
        return False


class Bridge:
    def __init__(self, binary: str, home: Path, port: int):
        self.binary, self.home, self.port = binary, home.resolve(), port
        self.environment = codex_env(binary, str(self.home))
        self.manager = CodexDaemonManager("auto", allow_restart=False, require_shared=True)
        self.active = 0

    async def handle(self, reader, writer):
        process = None
        pumps = []
        admitted = False
        try:
            peer = writer.get_extra_info("peername")
            if (self.active >= 12 or not peer or peer[0] != "127.0.0.1"
                    or not await asyncio.to_thread(same_user_peer, peer[1], self.port)):
                return
            self.active += 1
            admitted = True
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(header) > 16384:
                return
            if header == f"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode():
                identity = self.manager.current_process_identity()
                payload = json.dumps({"schema": 1, "profile": str(self.home), "pid": os.getpid(),
                                      "daemon_pid": identity.pid if identity else None}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: "
                             + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                await writer.drain()
                return
            if not websocket_upgrade(header, self.port):
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            argv = await self.manager.proxy_args(self.binary, self.environment)
            before = self.manager.current_process_identity()
            if argv is None or before is None:
                return
            process = await asyncio.create_subprocess_exec(
                *argv, env=self.environment, cwd=str(self.home),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if self.manager.current_process_identity() != before:
                return
            process.stdin.write(header)
            await process.stdin.drain()

            async def pump(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()

            pumps = [asyncio.create_task(pump(reader, process.stdin)),
                     asyncio.create_task(pump(process.stdout, writer))]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, TimeoutError, RuntimeError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            pass
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if process is not None:
                if process.stdin:
                    process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except TimeoutError:
                    process.kill()  # only this connection's short-lived native proxy
                    await process.wait()
            if admitted:
                self.active -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def serve(home: Path, port: int) -> None:
    bridge = Bridge(resolve_codex_bin(), home, port)
    await bridge.manager.ensure_started(bridge.binary, bridge.environment)
    async with await asyncio.start_server(bridge.handle, "127.0.0.1", port, limit=16384) as server:
        await server.serve_forever()


def bridge_health(port: int) -> dict | None:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.settimeout(4)
            connection.sendall(f"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            data = b""
            while chunk := connection.recv(4096):
                data += chunk
                if len(data) > 16384:
                    return None
        value = json.loads(data.split(b"\r\n\r\n", 1)[1])
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, IndexError):
        return None


def bridge_matches(value: dict, home: Path, port: int) -> bool:
    pid = value.get("pid")
    if (value.get("schema") != 1 or type(pid) is not int or pid <= 1
            or not isinstance(value.get("profile"), str)
            or Path(value["profile"]).resolve() != home):
        return False
    identity = process_identity(pid)
    if identity is None or process_owner_uid(pid) != os.getuid():
        return False
    complete, raw_args, selected = process_command_environment_value(identity, "CODEX_HOME")
    if not complete or not raw_args or not selected or Path(selected).resolve() != home:
        return False
    args = tuple(os.fsdecode(arg) for arg in raw_args)
    expected = ("-m", "cc_remote.codex_windows_shared", "serve",
                "--home", str(home), "--port", str(port))
    native = _managed_daemon_process_identity(home)
    return bool(args[1:] == expected and Path(args[0]).resolve() == Path(sys.executable).resolve()
                and process_identity(pid) == identity and native
                and value.get("daemon_pid") == native.pid)


def ensure_bridge(home: Path, port: int) -> None:
    existing = bridge_health(port)
    if existing is not None:
        if not bridge_matches(existing, home, port):
            raise RuntimeError("Existing bridge is not bound to the selected account")
        return
    import socket

    # Refuse an unrelated listener instead of killing it or starting duplicates.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    environment = codex_env(resolve_codex_bin(), str(home))
    subprocess.Popen([sys.executable, "-m", "cc_remote.codex_windows_shared", "serve",
                      "--home", str(home), "--port", str(port)],
                     env=environment, cwd=Path(__file__).resolve().parents[1],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = bridge_health(port)
        if value and bridge_matches(value, home, port):
            return
        time.sleep(0.1)
    raise RuntimeError("Desktop bridge did not become ready")


def launch_app(home: Path, port: int, executable: Path) -> None:
    import psutil

    for process in psutil.process_iter(["exe"]):
        if process.info["exe"] and Path(process.info["exe"]).resolve() == executable:
            raise RuntimeError("Codex App is already running. Close it normally, then use the shared launcher; active work was preserved.")
    ensure_bridge(home, port)
    environment = codex_env(resolve_codex_bin(), str(home))
    environment.update(CODEX_APP_SERVER_WS_URL=f"ws://127.0.0.1:{port}",
                       CODEX_APP_SERVER_FORCE_CLI="0")
    subprocess.Popen([str(executable)], env=environment, cwd=home)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "prepare", "app"])
    parser.add_argument("--home", type=Path)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or not 1 <= args.port <= 65535:
        parser.error("Windows and a valid loopback port are required")
    from cc_remote.config import WrapperConfig
    from cc_remote.codex_profiles import CodexProfileRegistry

    cfg = WrapperConfig()
    selected = args.home or CodexProfileRegistry.from_json(cfg.codex_profiles_json).default.home
    home = selected.expanduser().resolve()
    try:
        if args.action == "serve":
            asyncio.run(serve(home, args.port))
        elif args.action == "prepare":
            ensure_bridge(home, args.port)
            print("Official native daemon bridge is ready; no App or model turn was started.")
        elif args.app and args.app.is_file():
            launch_app(home, args.port, args.app.resolve())
        else:
            parser.error("--app must select the installed official App executable")
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
