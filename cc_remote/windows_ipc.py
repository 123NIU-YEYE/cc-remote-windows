"""Same-user loopback endpoints where Windows has no private Unix sockets.

Upstream serves device-local requests on Unix sockets inside a private
directory. Windows uses the Codex desktop bridge boundary instead: a 127.0.0.1
listener on an OS-selected port, published in a private endpoint file at the
path the socket would use. Each side checks the TCP table that the process at
the other end of its connection belongs to the same Windows user.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat

MAX_ENDPOINT_BYTES = 256
MAX_PENDING_CHECKS = 4
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


def same_user_peer(peer_port: int, port: int) -> bool:
    """Whether the loopback socket bound to ``peer_port`` and connected to
    ``port`` belongs to a process of this user."""
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


def _os_error(exc, path: Path) -> OSError:
    # pywin32 errors are not OSError; keep callers' OSError handling intact.
    return OSError(0, exc.strerror, str(path), exc.winerror)


def publish(endpoint: Path, port: int) -> None:
    """Replace this user's stale endpoint, then publish ``port`` privately.

    Callers hold the server's lifetime lock, so a stale file cannot belong to a
    live server.
    """
    import pywintypes
    from cc_remote.windows_privacy import create_private_file, owned_path

    try:
        if os.path.lexists(endpoint):
            info = endpoint.lstat()
            if (not stat.S_ISREG(info.st_mode)
                    or info.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or not owned_path(endpoint)):
                raise ValueError("Refusing to replace an unrelated local endpoint")
            endpoint.unlink()
        fd = create_private_file(endpoint)
    except pywintypes.error as exc:
        raise _os_error(exc, endpoint) from exc
    try:
        os.write(fd, json.dumps({"port": port, "pid": os.getpid()}).encode())
    finally:
        os.close(fd)


def endpoint_port(endpoint: Path) -> int:
    """Read the published port only from a private file owned by this user."""
    import msvcrt
    import pywintypes
    import win32con
    import win32file
    from cc_remote.windows_privacy import owned_descriptor, owned_path

    try:
        if not owned_path(endpoint.parent, private=True):
            raise ValueError("Local endpoint must be private and owned by this user")
        # Never follow a link, and let the server remove its endpoint while a
        # client is reading it.
        handle = win32file.CreateFile(
            str(endpoint), win32con.GENERIC_READ,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None, win32con.OPEN_EXISTING, _FILE_FLAG_OPEN_REPARSE_POINT, None)
    except pywintypes.error as exc:
        raise _os_error(exc, endpoint) from exc
    raw = handle.Detach()
    try:
        fd = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
    except Exception:
        win32file.CloseHandle(raw)
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode)
                or info.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                or info.st_size > MAX_ENDPOINT_BYTES
                or not owned_descriptor(stream.fileno(), private=True)):
            raise ValueError("Local endpoint must be private and owned by this user")
        data = stream.read(MAX_ENDPOINT_BYTES + 1)
    try:
        port = json.loads(data)["port"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid local endpoint") from exc
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError("Invalid local endpoint")
    return port


async def start_server(client_connected_cb, endpoint: Path, *, limit: int) -> asyncio.Server:
    """Serve only this user's processes and publish the port at ``endpoint``."""
    checking = 0

    async def verified(reader, writer):
        nonlocal checking
        peer = writer.get_extra_info("peername")
        local = writer.get_extra_info("sockname")
        # Bound TCP-table scans before admission: unverified local connections
        # must not occupy the Wrapper's shared worker threads.
        if checking >= MAX_PENDING_CHECKS or not peer or not local or peer[0] != "127.0.0.1":
            writer.close()
            return
        checking += 1
        try:
            same_user = await asyncio.to_thread(same_user_peer, peer[1], local[1])
        finally:
            checking -= 1
        if not same_user:
            writer.close()
            return
        await client_connected_cb(reader, writer)

    server = await asyncio.start_server(verified, "127.0.0.1", 0, limit=limit)
    try:
        publish(endpoint, server.sockets[0].getsockname()[1])
    except BaseException:
        server.close()
        await server.wait_closed()
        raise
    return server


async def open_connection(endpoint: Path, *, limit: int):
    """Connect only when the published endpoint is served by this user."""
    port = endpoint_port(endpoint)
    reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=limit)
    try:
        local = writer.get_extra_info("sockname")
        if not local or not await asyncio.to_thread(same_user_peer, port, local[1]):
            raise ValueError("Local endpoint is not served by this user")
    except BaseException:
        writer.close()
        raise
    return reader, writer
