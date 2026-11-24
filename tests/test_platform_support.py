"""The test suite's platform helpers and its symlink-privilege skip."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests import conftest
from tests.platform_support import WINDOWS, fake_executable, has_mode, is_private, native_path, set_home


def test_native_path_is_absolute_and_unchanged_on_posix():
    assert os.path.isabs(native_path("/repo/component"))
    if not WINDOWS:
        assert native_path("/repo/component") == "/repo/component"


def test_set_home_redirects_the_user_home(monkeypatch, tmp_path):
    set_home(monkeypatch, tmp_path)
    assert Path.home() == tmp_path
    assert os.path.expanduser("~") == str(tmp_path)


def test_privacy_helpers_follow_the_platform(tmp_path):
    path = tmp_path / "secret.json"
    path.write_text("{}", encoding="utf-8")
    if WINDOWS:
        # Test temp directories inherit an owner-only ACL from the profile.
        assert is_private(path) and has_mode(path, 0o600) and has_mode(path, 0o644)
        return
    path.chmod(0o600)
    assert is_private(path) and has_mode(path, 0o600) and not has_mode(path, 0o400)
    path.chmod(0o644)
    assert not is_private(path) and has_mode(path, 0o644)


def test_fake_executable_runs_python_source_with_arguments(tmp_path):
    binary = fake_executable(tmp_path / "codex", "import sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(3)\n")
    result = subprocess.run([str(binary), "app-server", "--version"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert (result.returncode, result.stdout.strip()) == (3, "app-server --version")


def _refusal_from(filename):
    code = compile("raise OSError(None, 'privilege not held', None, 1314)", filename, "exec")
    try:
        exec(code, {})
    except OSError as exc:
        return exc


@pytest.mark.skipif(sys.platform != "win32", reason="Windows symlink privilege")
def test_only_symlinks_refused_in_test_code_become_skips():
    in_test = _refusal_from(str(conftest._REPO / "tests" / "test_fixture.py"))
    in_product = _refusal_from(str(conftest._REPO / "cc_remote" / "wrapper" / "module.py"))
    assert conftest._test_symlink_refused(in_test)
    assert not conftest._test_symlink_refused(in_product)
    # A product refusal stays a failure even when test code re-raises it.
    try:
        try:
            raise in_product
        except OSError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as wrapped:
        assert not conftest._test_symlink_refused(wrapped)
    assert not conftest._test_symlink_refused(OSError(None, "other", None, 5))
