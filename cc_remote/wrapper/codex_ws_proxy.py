"""Raw stdio <-> TCP tunnel for a loopback Codex WebSocket app-server.

``CodexHandle`` already owns the WebSocket handshake, framing, validation and
JSON-RPC lifecycle. Unix can reach the shared daemon through the official
``codex app-server proxy`` command, which current Windows CLIs support too.
This compatibility process supplies a byte pipe only when an explicit legacy
loopback ``ws://`` listener is selected.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
from urllib.parse import urlsplit


def loopback_ws_target(value: str) -> tuple[str, int]:
    """Validate one uncredentialed root loopback WebSocket URL."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "ws"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Codex shared WebSocket must be a root loopback ws:// URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Codex shared WebSocket port is invalid") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("Codex shared WebSocket URL must include a valid port")
    return parsed.hostname, port


def _stdin_to_socket(stream, connection: socket.socket) -> None:
    try:
        while True:
            # ``BufferedReader.read(n)`` may wait for n bytes on a pipe. The
            # app-server handshake is intentionally small, so use the OS pipe
            # primitive to forward whatever is available immediately.
            chunk = os.read(stream.fileno(), 64 * 1024)
            if not chunk:
                break
            connection.sendall(chunk)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def proxy(url: str) -> int:
    host, port = loopback_ws_target(url)
    try:
        connection = socket.create_connection((host, port), timeout=10.0)
    except OSError:
        return 2
    connection.settimeout(None)
    upstream = threading.Thread(
        target=_stdin_to_socket,
        args=(sys.stdin.buffer, connection),
        daemon=True,
    )
    upstream.start()
    try:
        while True:
            chunk = connection.recv(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try:
            connection.close()
        except OSError:
            pass
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 64
    try:
        return proxy(sys.argv[1])
    except ValueError:
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
