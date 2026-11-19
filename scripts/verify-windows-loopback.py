"""Start isolated Relay/Wrapper processes and verify control only; no model turns."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil
from websockets.asyncio.client import connect


ROOT = Path(__file__).resolve().parents[1]


def stop_owned_process(process):
    try:
        owner = psutil.Process(process.pid)
        children = owner.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _gone, alive = psutil.wait_procs(children, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass


async def verify(origin, password):
    with httpx.Client(timeout=3) as client:
        login = client.post(origin + "/api/login", headers={"Origin": origin},
                            json={"password": password})
        login.raise_for_status()
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        build = client.get(origin + "/cc-remote-build.json").json()
        health = client.get(origin + "/healthz").json()
        assert build["protocol"] == health["protocol"]
        assert health["wrapper_connected"]
        assert client.get(origin + "/").status_code == 200
    async with connect(origin.replace("http:", "ws:") + "/ws?machine=windows-smoke",
                       origin=origin, additional_headers={"Cookie": cookie},
                       max_size=16 * 1024 * 1024) as websocket:
        await websocket.send(json.dumps({
            "v": health["protocol"], "type": "hello", "role": "client",
            "client_id": "windows-smoke", "machine_id": "windows-smoke",
        }))
        await websocket.send(json.dumps({
            "v": health["protocol"], "type": "list_sessions", "engine": "claude",
            "space": "code", "client_id": "windows-smoke", "cmd_id": "smoke-read",
        }))
        for _ in range(50):
            message = json.loads(await asyncio.wait_for(websocket.recv(), 10))
            if message.get("type") == "session_list" and message.get("engine") == "claude":
                assert message["sessions"] == []
                return {"version": build["version"], "protocol": build["protocol"],
                        "wrapper_connected": True, "authenticated_websocket": True,
                        "empty_isolated_catalog": True, "model_turns_sent": 0}
        raise RuntimeError("isolated session catalog did not arrive")


def main():
    if sys.platform != "win32":
        raise SystemExit("This smoke test verifies native Windows processes.")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="cc-v5-smoke-") as temporary:
        home = Path(temporary)
        environment = os.environ.copy()
        for key in list(environment):
            if key.startswith(("CC_REMOTE_", "CODEX_", "CLAUDE_", "PUSH_", "LOGIN_", "SESSION_")):
                environment.pop(key)
        password = secrets.token_urlsafe(32)
        environment.update({
            "PYTHON_DOTENV_DISABLED": "1", "HOME": temporary, "USERPROFILE": temporary,
            "CODEX_HOME": str(home / ".codex"), "CC_REMOTE_STATE_DIR": str(home / "state"),
            "CC_REMOTE_DEVICE_CONFIG": str(home / "unused-device.json"),
            "CC_REMOTE_CODEX_DAEMON": "off", "CC_REMOTE_MACHINE_ID": "windows-smoke",
            "CC_REMOTE_DSH_CONNECTION_FILE": str(home / "unused-dsh.json"),
            "CC_CWD": temporary, "RELAY_URL": origin.replace("http:", "ws:") + "/ws",
            "RELAY_HOST": "127.0.0.1", "RELAY_PORT": str(port), "PUBLIC_ORIGIN": origin,
            "LOGIN_PASSWORD": password, "WRAPPER_TOKEN": secrets.token_urlsafe(32),
            "SESSION_SECRET": secrets.token_urlsafe(32), "WEB_STATIC_DIR": str(ROOT / "web/dist"),
            "DEVICE_DB_PATH": str(home / "devices.sqlite3"), "PUSH_DB_PATH": str(home / "push.sqlite3"),
        })
        processes = []
        with (home / "relay.log").open("wb") as relay_log, (home / "wrapper.log").open("wb") as wrapper_log:
            try:
                for module, output in [("cc_remote.relay", relay_log), ("cc_remote.wrapper", wrapper_log)]:
                    processes.append(subprocess.Popen(
                        [sys.executable, "-X", "utf8", "-m", module], cwd=ROOT, env=environment,
                        stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW))
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if any(process.poll() is not None for process in processes):
                        raise RuntimeError("an isolated service exited before readiness")
                    try:
                        with httpx.Client(timeout=1) as client:
                            health = client.get(origin + "/healthz")
                        if health.status_code == 200 and health.json().get("wrapper_connected"):
                            print(json.dumps(asyncio.run(verify(origin, password))))
                            return
                    except (httpx.HTTPError, OSError):
                        pass
                    time.sleep(0.2)
                raise RuntimeError("isolated services did not become ready")
            except Exception:
                for label in ("relay", "wrapper"):
                    print(f"{label} diagnostic tail:", file=sys.stderr)
                    print((home / f"{label}.log").read_text(encoding="utf-8", errors="replace")[-5000:],
                          file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    stop_owned_process(process)


if __name__ == "__main__":
    main()
