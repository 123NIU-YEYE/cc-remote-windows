"""Native descriptors must keep identity through directory replacement."""
import os
import stat
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native handles")


def test_private_descriptor_supports_long_paths(tmp_path):
    root = tmp_path / ("a" * 100) / ("b" * 100)
    root.mkdir(parents=True)
    path = root / ("c" * 100)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.write(descriptor, b"long path")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert os.read(descriptor, 100) == b"long path"
    finally:
        os.close(descriptor)


def test_relative_read_stays_on_renamed_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "child.txt").write_bytes(b"original")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        root.rename(tmp_path / "moved")
        root.mkdir()
        (root / "child.txt").write_bytes(b"substitute")
        child = os.open("child.txt", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            assert os.read(child, 100) == b"original"
        finally:
            os.close(child)
        for escape in ["..", "../child.txt", "child.txt:stream", str(root / "child.txt")]:
            with pytest.raises(ValueError):
                os.open(escape, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    finally:
        os.close(fd)


def test_no_follow_rejects_reparse_point_and_keeps_target_bytes(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"original")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Creating symlinks requires Windows developer mode or privilege")
        raise
    with pytest.raises(PermissionError):
        os.open(link, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    assert target.read_bytes() == b"original"


def test_private_descriptor_updates_real_acl_and_bad_fsync_is_not_hidden(tmp_path):
    from cc_remote.windows_privacy import owned_path
    path = tmp_path / "private"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, b"bytes")
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        assert owned_path(path, private=True)
    finally:
        os.close(fd)
    with pytest.raises(OSError):
        os.fsync(-1)
    assert stat.S_IMODE(0o100666) == 0o666, "Do not falsify POSIX permission results globally"


def test_private_descriptor_keeps_identity_for_crt_handle_after_rename(tmp_path):
    from cc_remote.windows_privacy import owned_path
    source, moved = tmp_path / "source", tmp_path / "moved"
    descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
    try:
        # A normal CRT descriptor does not carry WRITE_DAC. ReOpenFile must
        # elevate access to that same file object without resolving its path.
        os.fchmod(descriptor, 0o600)
        assert owned_path(source, private=True)
    finally:
        os.close(descriptor)
    source.rename(moved)
    assert owned_path(moved, private=True)


def test_relative_stat_list_and_mutations_stay_on_pinned_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "old.txt").write_bytes(b"old")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root.rename(tmp_path / "original")
        root.mkdir()
        (root / "old.txt").write_bytes(b"substitute")
        assert os.stat("old.txt", dir_fd=descriptor, follow_symlinks=False).st_size == 3
        with os.scandir(descriptor) as entries:
            assert [entry.name for entry in entries] == ["old.txt"]
        os.mkdir("uploads", dir_fd=descriptor)
        output = os.open("temp.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        os.write(output, b"updated")
        os.close(output)
        os.replace("temp.txt", "old.txt", src_dir_fd=descriptor, dst_dir_fd=descriptor)
        assert (tmp_path / "original" / "old.txt").read_bytes() == b"updated"
        assert (root / "old.txt").read_bytes() == b"substitute"
        os.unlink("old.txt", dir_fd=descriptor)
        assert not (tmp_path / "original" / "old.txt").exists()
        assert (root / "old.txt").exists()
    finally:
        os.close(descriptor)
