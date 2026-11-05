"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import pytest
import sys
import tempfile
import os
from uuid import uuid4
from pathlib import Path

# The Windows port installs dependencies into a repo-local ``.pydeps`` tree
# because the portable interpreter ships a ``python312._pth`` that disables both
# PYTHONPATH and site discovery. Put that tree on sys.path for the suite; on a
# normal virtualenv the directory simply does not exist and nothing changes.
_ROOT = Path(__file__).resolve().parents[1]
for _candidate in (_ROOT, _ROOT / ".pydeps"):
    if _candidate.is_dir():
        _entry = str(_candidate)
        if _entry not in sys.path:
            sys.path.insert(0, _entry)


def pytest_configure(config):
    if sys.platform == "win32" and config.option.basetemp is None:
        # The default user/temp/pytest-of-user tree consumes much of MAX_PATH.
        # Use a unique workspace-owned root; explicit --basetemp still wins.
        root = _ROOT / ".runtime" / "pytest"
        root.mkdir(parents=True, exist_ok=True)
        from cc_remote.windows_privacy import private_directory
        parent = root / ("run-" + uuid4().hex[:10])
        private_directory(parent)
        # pytest recreates basetemp; its parent must carry the inheritable ACL.
        config.option.basetemp = str(parent / "t")


def pytest_make_parametrize_id(config, val, argname):
    # pytest exports the node ID as PYTEST_CURRENT_TEST. An oversized payload
    # must not exceed Windows' environment limit or flood failure reports.
    if isinstance(val, (str, bytes)) and len(val) > 128:
        return f"{argname}-{len(val)}-units"
    return None


@pytest.fixture(autouse=True)
def _stable_posix_umask():
    if os.name == "nt":
        yield
        return
    native_umask = os.umask
    previous = native_umask(0o022)
    try:
        yield
    finally:
        native_umask(previous)


_ACCOUNT_PROFILE_ENV = (
    "CODEX_HOME",
    "CC_REMOTE_CODEX_PROFILES_JSON",
    "CC_REMOTE_CODEX_PROFILES_FILE",
    "CC_REMOTE_CLAUDE_PROFILES_JSON",
    "CC_REMOTE_CLAUDE_PROFILES_FILE",
    # A test launched from an installed Wrapper inherits launchd's identity.
    # That enables legacy Claude profile discovery from the real user's home.
    "XPC_SERVICE_NAME",
)


@pytest.fixture(autouse=True)
def _isolate_account_profile_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory,
) -> None:
    """Make every test opt in to account/profile configuration explicitly.

    ``WrapperConfig`` and account registries intentionally inherit the
    launching shell in production.  Unit tests must not inherit those values:
    otherwise a developer running pytest from a managed Wrapper/``CODEX_HOME`` gets
    a different registry and dozens of unrelated mock-signature failures.
    """
    for key in _ACCOUNT_PROFILE_ENV:
        monkeypatch.delenv(key, raising=False)
    # Native tests must opt into daemon management; transport fakes must never
    # discover the developer's real daemon through the repository's .env.
    monkeypatch.setenv("CC_REMOTE_CODEX_DAEMON", "off")
    monkeypatch.delenv("CODEX_APP_SERVER_WS_URL", raising=False)
    # Resource-channel tests use their own registries and transports. A local
    # launcher's opt-out must not disable those isolated fixtures.
    monkeypatch.setenv("CC_REMOTE_VIEWER_CHANNEL", "auto")
    if sys.platform == "win32":
        # Scratch directories live below the checkout to keep paths short.
        # A fixture without .git must never discover the real parent repository.
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
        original = pytest.MonkeyPatch.setenv

        def setenv_with_native_home(self, name, value, prepend=None):
            original(self, name, value, prepend)
            if name == "HOME":
                original(self, "USERPROFILE", value, prepend)

        monkeypatch.setattr(pytest.MonkeyPatch, "setenv", setenv_with_native_home)
    # A sibling temporary root does not pollute tests asserting that tmp_path
    # stayed empty, and keeps Windows paths below native path-length limits.
    with tempfile.TemporaryDirectory(prefix="cc-test-home-") as directory:
        monkeypatch.setenv("HOME", directory)
        yield
        # Test-level patches (notably os.scandir failures) must not intercept
        # TemporaryDirectory cleanup. All dependent fixtures have drained here.
        monkeypatch.undo()


@pytest.fixture(autouse=True)
def _native_symlink_capability(monkeypatch, tmp_path_factory):
    if sys.platform != "win32":
        return
    from _pytest import pathlib as pytest_pathlib

    # pytest makes a cosmetic `*-current` symlink for *every* tmp_path, not
    # just the session root. Disable only that optional bookkeeping on Windows
    # before converting an actual test fixture's privilege error to a skip.
    monkeypatch.setattr(pytest_pathlib, "_force_symlink", lambda *args, **kwargs: None)
    tmp_path_factory.getbasetemp()
    symlink = os.symlink

    def create(*args, **kwargs):
        try:
            return symlink(*args, **kwargs)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("native symlink fixture requires Windows Developer Mode or symlink privilege")
            raise

    monkeypatch.setattr(os, "symlink", create)
