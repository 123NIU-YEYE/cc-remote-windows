"""Regression checks for the cross-platform advisory lock wrapper."""
from __future__ import annotations

import os

import pytest

from cc_remote.wrapper.file_lock_compat import LOCK_EX, LOCK_NB, LOCK_UN, flock
from cc_remote.wrapper.os_compat import open_with_share_delete


def test_flock_preserves_offset_and_unlocks_after_io(tmp_path):
    path = tmp_path / "journal.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"header")
        expected_offset = os.lseek(fd, 0, os.SEEK_CUR)

        flock(fd, LOCK_EX)
        assert os.lseek(fd, 0, os.SEEK_CUR) == expected_offset

        os.write(fd, b"-payload\n")
        unlock_offset = os.lseek(fd, 0, os.SEEK_CUR)
        flock(fd, LOCK_UN)
        assert os.lseek(fd, 0, os.SEEK_CUR) == unlock_offset
    finally:
        os.close(fd)


def test_flock_nonblocking_raises_on_contention_from_another_descriptor(
    tmp_path,
):
    path = tmp_path / "journal.lock"
    path.write_bytes(b"\0")
    holder = os.open(path, os.O_RDWR)
    contender = os.open(path, os.O_RDWR)
    try:
        flock(holder, LOCK_EX)
        with pytest.raises(BlockingIOError):
            flock(contender, LOCK_EX | LOCK_NB)
        flock(holder, LOCK_UN)
        flock(contender, LOCK_EX | LOCK_NB)
        flock(contender, LOCK_UN)
    finally:
        os.close(contender)
        os.close(holder)


def test_open_with_share_delete_allows_unlink_while_descriptor_is_open(
    tmp_path,
):
    path = tmp_path / "marker"
    descriptor = open_with_share_delete(
        path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"token")
        path.unlink()
        assert not path.exists()
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, 5) == b"token"
    finally:
        os.close(descriptor)


def test_open_with_share_delete_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_with_share_delete(tmp_path / "missing", os.O_RDWR)


def test_open_with_share_delete_respects_o_excl_collision(tmp_path):
    path = tmp_path / "marker"
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        open_with_share_delete(
            path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
