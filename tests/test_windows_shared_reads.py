"""Reads of engine-owned files must not stop the engine from moving them."""
import os
import sys

import pytest

from cc_remote.wrapper import codex_sessions
from cc_remote.wrapper.os_compat import open_shared

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")


def test_open_file_can_be_moved_and_deleted(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"first\nsecond\n")
    moved = tmp_path / "archived.jsonl"
    with open_shared(path) as stream:
        assert stream.readline() == b"first\n"
        os.rename(path, moved)
        os.unlink(moved)
        assert stream.readline() == b"second\n"
    assert not moved.exists()


def test_errors_and_text_mode_match_builtin_open(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_shared(tmp_path / "missing.toml")
    with pytest.raises(OSError):
        open_shared(tmp_path)
    path = tmp_path / "config.toml"
    path.write_bytes("model = \"模型\"\r\n".encode("utf-8"))
    with open_shared(path, "r", encoding="utf-8") as stream:
        assert stream.read() == "model = \"模型\"\n"


def test_codex_config_can_be_replaced_while_it_is_read(tmp_path):
    # Codex rewrites config.toml by renaming a new file over it.
    config = tmp_path / "config.toml"
    config.write_bytes(b'model = "old"\n')
    with open_shared(config) as stream:
        os.rename(config, tmp_path / "config.toml.old")
        (tmp_path / "config.toml.new").write_bytes(b'model = "new"\n')
        os.rename(tmp_path / "config.toml.new", config)
        assert stream.read() == b'model = "old"\n'
    assert codex_sessions._config_value("model", "", codex_home=tmp_path) == "new"
