"""Process-local launch exclusion, acknowledged by the running systemd Wrapper.

Quiescence is sticky across installer disconnects. Only a pre-stop abort resumes
launches; after an uncertain stop the next installer can reacquire the barrier.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import socket
import struct
import threading

from cc_remote.wrapper.codex_service import DaemonIsolationError, needs_isolation
from cc_remote.wrapper.process_scan import ProcessIdentity, process_identity


READ_ONLY_DAEMON_COMMANDS = frozenset({b"version", b"status", b"help", b"--help", b"-h"})


def mutates_daemon(argv: tuple[str, ...]) -> bool:
    return any(
        argv[i:i + 2] == ("app-server", "daemon")
        and os.fsencode(argv[i + 2]) not in READ_ONLY_DAEMON_COMMANDS
        for i in range(max(0, len(argv) - 2))
    )


def address(identity: ProcessIdentity) -> str:
    return f"\0cc-remote-codex-launch-v1-{identity.pid}-{identity.start_ticks}"


def peer(connection: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


class LaunchBarrier:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._quiesced = False
        self._stop_committed = False

    @contextmanager
    def launch(self):
        with self._lock:
            if self._quiesced:
                raise DaemonIsolationError("Codex launches are paused for a Wrapper upgrade; retry later")
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1

    def request(self, command: bytes) -> bytes:
        with self._lock:
            if command == b"quiesce":
                if self._active:
                    return b"busy"
                self._quiesced = True
                return b"ready"
            if command == b"resume":
                if self._stop_committed:
                    return b"committed"
                self._quiesced = False
                return b"resumed"
            if command == b"commit-stop" and self._quiesced:
                self._stop_committed = True
                return b"committed"
            return b"invalid"


barrier = LaunchBarrier()


@contextmanager
def serve():
    if not needs_isolation():
        yield
        return
    identity = process_identity(os.getpid())
    if identity is None:
        raise DaemonIsolationError("Cannot identify Wrapper launch barrier")
    done = threading.Event()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        listener.bind(address(identity))
        listener.listen(4)
        listener.settimeout(0.2)

        def receive():
            while not done.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                with connection:
                    try:
                        connection.settimeout(2)
                        if peer(connection)[1] not in {0, os.getuid()}:
                            continue
                        command = connection.recv(32)
                        connection.sendall(barrier.request(command))
                    except OSError:
                        # A dropped reply must not silently reopen launches.
                        pass

        thread = threading.Thread(target=receive, name="codex-launch-barrier", daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join(timeout=3)
    finally:
        listener.close()


def request(identity: ProcessIdentity, uid: int, command: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(3)
        connection.connect(address(identity))
        pid, owner, _ = peer(connection)
        if (pid != identity.pid or owner != uid
                or process_identity(pid) != identity):
            raise RuntimeError("Wrapper launch barrier identity changed")
        connection.sendall(command)
        return connection.recv(32)
