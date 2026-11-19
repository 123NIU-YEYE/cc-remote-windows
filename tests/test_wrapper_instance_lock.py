"""Duplicate launches must not evict each other's relay connection."""
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

from cc_remote.config import WrapperConfig
from cc_remote.wrapper import __main__ as wrapper_main
from cc_remote.wrapper.instance_lock import wrapper_instance


def _probe(state_dir):
    return subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; "
        "from cc_remote.wrapper.instance_lock import wrapper_instance; "
        "ctx=wrapper_instance(Path(sys.argv[1])); "
        "acquired=ctx.__enter__(); ctx.__exit__(None,None,None); "
        "sys.exit(0 if acquired else 17)", str(state_dir),
    ], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)


def test_instance_lock_excludes_other_process_and_releases_after_owner_exit(tmp_path):
    state = tmp_path / "state"
    with wrapper_instance(state) as owned:
        assert owned
        assert _probe(state).returncode == 17
        with wrapper_instance(tmp_path / "other-device") as independent:
            assert independent
    assert _probe(state).returncode == 0
    # The persistent file does not prevent a later normal launch.
    with wrapper_instance(state) as reacquired:
        assert reacquired


def test_duplicate_entrypoint_does_not_start_transports_or_engines(monkeypatch, tmp_path):
    cfg = WrapperConfig(state_dir=tmp_path / "state")
    monkeypatch.setattr(wrapper_main, "wrapper_config", lambda: cfg)
    monkeypatch.setattr(wrapper_main, "validate_wrapper_config", lambda _cfg: None)

    async def must_not_start(_cfg):
        raise AssertionError("duplicate launch started an engine or transport")

    monkeypatch.setattr(wrapper_main, "_run_configured_wrapper", must_not_start)
    with wrapper_instance(cfg.state_dir):
        asyncio.run(wrapper_main.run_wrapper())
