"""Git patch headers on Windows, where os.path.normpath rewrites '/dev/null'."""
import ntpath
import sys

import pytest

from cc_remote.wrapper import turn_changes

CREATE = ("diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"
          "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+x\n")
DELETE = ("diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n"
          "--- a/src/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-y\n")


@pytest.fixture(params=["native", "ntpath"])
def path_flavour(request, monkeypatch):
    # Exercise Windows path normalization on every platform.
    if request.param == "ntpath":
        monkeypatch.setattr(turn_changes.os, "path", ntpath)
    return request.param


def test_dev_null_header_survives_normalization(path_flavour):
    created, = turn_changes.parse_patches(CREATE)
    deleted, = turn_changes.parse_patches(DELETE)
    assert created["old"] == "/dev/null"
    assert deleted["new"] == "/dev/null"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows paths")
@pytest.mark.parametrize("patch, name", [(CREATE, "new.py"), (DELETE, "old.py")],
                         ids=["create", "delete"])
def test_created_and_deleted_files_keep_their_real_path(patch, name):
    cwd = "C:\\repo"
    files = turn_changes.project_turn_changes([{"type": "turn_diff", "diff": patch}], cwd)["files"]
    assert [item["path"] for item in files] == [f"C:\\repo\\src\\{name}"]
