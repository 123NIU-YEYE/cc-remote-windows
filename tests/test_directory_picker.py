"""Workspace navigation crosses Windows volumes without selecting a virtual cwd."""
from __future__ import annotations

import asyncio
import os

import pytest

from cc_remote.protocol import ListDir, deserialize, serialize
from cc_remote.wrapper import directory_picker as picker
from cc_remote.wrapper.machine import WrapperMachine
from tests.test_multisession import _mk_machine


@pytest.mark.parametrize("path,parent", [
    ("C:\\", picker.WINDOWS_DRIVES_PATH),
    ("D:\\", picker.WINDOWS_DRIVES_PATH),
    ("D:\\Projects", "D:\\"),
    ("D:\\Projects\\demo", "D:\\Projects"),
])
def test_windows_drive_root_has_a_computer_parent(monkeypatch, path, parent):
    monkeypatch.setattr(picker, "IS_WINDOWS", True)
    assert picker.directory_parent(path) == parent


def test_drive_inventory_does_not_probe_offline_or_removable_roots(monkeypatch):
    monkeypatch.setattr(picker, "IS_WINDOWS", True)
    monkeypatch.setattr(picker.os, "listdrives", lambda: [
        "z:\\", "D:/", "C:\\", "d:\\", "invalid", "\\\\server\\share\\",
    ], raising=False)

    def no_probe(*args, **kwargs):
        raise AssertionError("Inventory must not read or stat a drive")

    monkeypatch.setattr(picker.os.path, "isdir", no_probe)
    monkeypatch.setattr(picker.os, "scandir", no_probe)
    path, parent, entries = WrapperMachine._scan_dir(picker.WINDOWS_DRIVES_PATH)
    assert path == picker.WINDOWS_DRIVES_PATH and parent is None
    assert entries == [{"name": root, "path": root} for root in ["C:\\", "D:\\", "Z:\\"]]


def test_unix_root_still_has_no_parent_and_rejects_drive_navigation(monkeypatch):
    monkeypatch.setattr(picker, "IS_WINDOWS", False)
    assert picker.directory_parent("/") is None
    assert picker.directory_parent("/tmp/demo") == "/tmp"
    with pytest.raises(FileNotFoundError):
        WrapperMachine._scan_dir(picker.WINDOWS_DRIVES_PATH)


def test_normal_directory_navigation_still_lists_one_level(tmp_path):
    (tmp_path / "zebra").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "alpha" / "nested").mkdir()
    (tmp_path / "file.txt").write_text("fixture")
    path, parent, entries = WrapperMachine._scan_dir(str(tmp_path))
    assert path == os.path.realpath(tmp_path)
    assert parent == str(tmp_path.parent)
    assert entries == [{"name": name, "path": str(tmp_path / name)} for name in ["alpha", "zebra"]]


def test_drive_listing_reply_stays_with_requesting_client(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        monkeypatch.setattr(picker, "IS_WINDOWS", True)
        monkeypatch.setattr(picker.os, "listdrives", lambda: ["C:\\", "D:\\"], raising=False)
        command = ListDir(path=picker.WINDOWS_DRIVES_PATH, cmd_id="pick-drives", client_id="browser-a")
        response = await machine._handle_list_dir(command)
        assert transport.sent == [response]
        assert response.to == "browser-a" and response.request_id == "pick-drives"
        assert response.parent is None and len(response.dirs) == 2
        assert deserialize(serialize(response)) == response
        assert not machine.sessions

    asyncio.run(run())


@pytest.mark.skipif(os.name != "nt", reason="Real Windows drive inventory")
def test_real_windows_root_returns_to_all_drive_letters():
    roots = os.listdrives()
    assert roots
    current_root = os.path.splitdrive(os.getcwd())[0] + "\\"
    path, parent, _ = WrapperMachine._scan_dir(current_root)
    assert os.path.normcase(path) == os.path.normcase(current_root)
    assert parent == picker.WINDOWS_DRIVES_PATH
    virtual, parent, entries = WrapperMachine._scan_dir(parent)
    assert virtual == picker.WINDOWS_DRIVES_PATH and parent is None
    assert {row["path"] for row in entries} == {root[0].upper() + ":\\" for root in roots}
    assert not os.path.isdir(virtual)
