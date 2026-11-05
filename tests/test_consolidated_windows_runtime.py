"""Regressions for control-plane boundaries retained by the consolidation."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from cc_remote.config import WrapperConfig
from cc_remote.wrapper.machine import WrapperMachine
from cc_remote.wrapper.process_scan import process_identity


def test_explicit_windows_endpoint_belongs_only_to_default_account(tmp_path):
    cfg = WrapperConfig(
        state_dir=tmp_path / "state",
        claude_work_root=tmp_path / "claude-work",
        codex_work_root=tmp_path / "codex-work",
        codex_app_server_ws_url="ws://127.0.0.1:8766",
        codex_profiles_json=json.dumps({
            "primary": {"label": "Primary", "home": str(tmp_path / "primary"), "default": True},
            "secondary": {"label": "Secondary", "home": str(tmp_path / "secondary")},
        }),
    )
    machine = WrapperMachine(cfg, SimpleNamespace(on_connected=None))
    assert machine._codex_daemons["primary"].websocket_url == cfg.codex_app_server_ws_url
    assert machine._codex_daemons["secondary"].websocket_url is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process creation identity")
def test_windows_current_process_identity_is_stable_and_checks_parent():
    identity = process_identity(os.getpid(), parent_pid=os.getppid())
    assert identity is not None
    assert process_identity(os.getpid()) == identity
    assert process_identity(os.getpid(), parent_pid=-1) is None
