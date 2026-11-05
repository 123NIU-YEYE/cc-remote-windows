"""Entry point: `python -m cc_remote.relay`."""
from __future__ import annotations

import os

import uvicorn

from cc_remote.config import relay_config, validate_relay_config
from cc_remote.log import logger
from cc_remote.relay.log_safety import uvicorn_log_config
from cc_remote.relay.server import create_app

log = logger("cc_remote.relay")


def _ws_ping(name: str, default: float) -> float:
    """Keepalive seconds for uvicorn's WebSocket server.

    Uvicorn defaults to a 20s ping with a 20s pong timeout. A wrapper behind a
    proxy and a tunnel can miss that window during a latency spike, and the
    teardown looks like a stalled turn in the UI. Allow a longer window; 0
    disables the ping entirely.
    """
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(0.0, value)


def main() -> None:
    cfg = relay_config()
    validate_relay_config(cfg)
    app = create_app(cfg)
    log.info("starting relay", host=cfg.host, port=cfg.port)
    # Uvicorn's access log includes the full WebSocket target (including query
    # strings). Keep it disabled even though browser credentials are cookie-only.
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
        log_config=uvicorn_log_config(),
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
        ws_max_size=cfg.ws_max_size_bytes,
        ws_max_queue=2,
        ws_ping_interval=_ws_ping("CC_REMOTE_WS_PING_INTERVAL", 30.0),
        ws_ping_timeout=_ws_ping("CC_REMOTE_WS_PING_TIMEOUT", 90.0),
    )


if __name__ == "__main__":
    main()
