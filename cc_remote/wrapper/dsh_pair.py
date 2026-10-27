"""Pair a Wrapper with an already-running DSH using its official login URL.

Run locally: python -m cc_remote.wrapper.dsh_pair --file <private-file>
The URL is read without echo, never placed in argv or sent to the relay.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from cc_remote.wrapper.dsh_client import (
    DshClient, DshConnection, DshError,
)
from cc_remote.wrapper.dsh_desktop import load_connection
from cc_remote.wrapper.os_compat import harden_private_file


def save_connection(connection: DshConnection, path: Path) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # POSIX directory ownership/mode is meaningless on Windows (st_mode is
    # synthesised as 0o666), so the DACL is hardened after writing instead.
    if sys.platform != "win32":
        info = path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise DshError("invalid_connection", "配对文件所在目录必须由当前用户拥有且其他用户不可写。")
    fd, staging = tempfile.mkstemp(prefix=".dsh-pair-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "origin": connection.origin, "token": connection.token}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
        # Windows has no POSIX mode bits; restrict the DACL instead (best effort).
        harden_private_file(path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


async def validate(connection: DshConnection) -> None:
    """Read-only contract validation. Pairing must not create/resume an Agent."""
    client = DshClient(connection)
    try:
        roster = await client.rpc("agentPresets/list")
        catalog = await client.rpc("session/modelCatalog")
        await client.list_sessions()
        await client.draft_catalog(str(Path.cwd()))
        if (not isinstance(roster, dict) or not isinstance(roster.get("presets"), list)
                or not isinstance(catalog, dict) or not isinstance(catalog.get("groups"), list)):
            raise DshError("unsupported", "DSH 未提供 0.2.1-rc.2 的模型与 Preset 接口。")
    finally:
        await client.close()


async def pair(launch_url: str, path: Path) -> None:
    connection = DshConnection.from_launch_url(launch_url)
    await validate(connection)
    save_connection(connection, path)


async def pair_desktop(path: Path, state: Path | None = None) -> None:
    """Pair with the already-running desktop app via its published state file."""
    connection = load_connection(state)
    await validate(connection)
    save_connection(connection, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pair cc-remote with local DSH 0.2.1-rc.2",
        epilog="使用 --desktop 可直接接管正在运行的 DeepSeek Harness 桌面版。",
    )
    parser.add_argument("--file", type=Path, required=True, help="Private Wrapper connection file")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Attach to the running DeepSeek Harness desktop app instead of pasting a dsh web URL",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Path to the desktop app state file (default: $DSH_HOME/cc-remote-desktop.json)",
    )
    args = parser.parse_args()
    try:
        if args.desktop:
            asyncio.run(pair_desktop(args.file, args.state))
        else:
            url = getpass.getpass("DSH 本机登录地址（输入不回显）：")
            asyncio.run(pair(url, args.file))
    except (EOFError, KeyboardInterrupt):
        return 130
    except DshError as exc:
        print(str(exc))
        return 1
    print("DSH 本机配对完成；配对文件已保存。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
