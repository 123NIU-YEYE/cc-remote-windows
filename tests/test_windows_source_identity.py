"""SQLite's signed representation must retain exact native file identity."""
import pytest

from cc_remote.wrapper.codex_client_messages import CodexClientMessageAliases
from cc_remote.wrapper.codex_process_clocks import CodexProcessClocks
from cc_remote.wrapper.codex_turn_leases import CodexTurnLeaseStore


@pytest.mark.parametrize("device", [(1 << 63) + 17, (1 << 64) - 1])
def test_native_aliases_accept_same_signed_device_but_reject_other_source(device):
    aliases = CodexClientMessageAliases({}, {}, source_path="rollout", source_device=device, source_inode=42)
    assert aliases.matches_source("rollout", device - (1 << 64), 42)
    assert not aliases.matches_source("rollout", device - (1 << 64), 43)
    assert not aliases.matches_source("other-rollout", device - (1 << 64), 42)
    clocks = CodexProcessClocks({}, source_path="rollout", source_device=device, source_inode=42)
    assert clocks.matches_source("rollout", device - (1 << 64), 42)
    assert not clocks.matches_source("rollout", device - (1 << 64), 43)


@pytest.mark.parametrize("device", [(1 << 63) + 17, (1 << 64) - 1])
def test_native_turn_binding_accepts_same_signed_index_identity(tmp_path, device):
    store = CodexTurnLeaseStore(tmp_path)
    store.claim("session", "turn", "message")
    assert store.bind_stream("session", "turn", "task", "native-message",
                             source_device=device, source_inode=42)
    lease = store.get("session")
    assert lease.stream_task_ids(source_device=device - (1 << 64), source_inode=42) == {"task"}
    assert not lease.stream_task_ids(source_device=device - (1 << 64), source_inode=43)
