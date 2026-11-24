"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback

# Tests never discover a parent checkout\'s production credentials.
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

import pytest

_REPO = Path(__file__).resolve().parents[1]
_ERROR_PRIVILEGE_NOT_HELD = 1314


def pytest_configure(config):
    # Windows text I/O follows the ANSI code page (cp936, cp1252, ...) unless
    # UTF-8 mode is on; test fixtures write UTF-8 text without naming it.
    if sys.platform == "win32" and not sys.flags.utf8_mode:
        raise pytest.UsageError(
            "Run the test suite in Python UTF-8 mode on Windows: set PYTHONUTF8=1")


def _test_symlink_refused(exc: BaseException | None) -> bool:
    """A symlink created by test code was refused for lack of privilege.

    Windows grants SeCreateSymbolicLinkPrivilege only to administrators and in
    Developer Mode. A refusal raised from product code is a real failure.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if (isinstance(exc, OSError)
                and getattr(exc, "winerror", None) == _ERROR_PRIVILEGE_NOT_HELD):
            frames = [Path(frame.filename).resolve()
                      for frame in traceback.extract_tb(exc.__traceback__)]
            own = [frame for frame in frames if any(
                frame.is_relative_to(_REPO / part) for part in ("tests", "cc_remote", "deploy"))]
            return bool(own) and own[-1].is_relative_to(_REPO / "tests")
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if (sys.platform == "win32" and call.excinfo is not None
            and _test_symlink_refused(call.excinfo.value)):
        report = outcome.get_result()
        path, line = item.reportinfo()[:2]
        report.outcome = "skipped"
        report.longrepr = (os.fspath(path), (line or 0) + 1,
                           "Skipped: creating symlinks needs Windows Developer Mode or administrator rights")


_foreign_tree_kills: list[int] = []

if sys.platform == "win32":
    import psutil

    from cc_remote.wrapper import os_compat

    _kill_process_tree = os_compat.kill_process_tree

    def _kill_own_process_tree(pid: int) -> None:
        """Stop only a process tree this test process started.

        Windows cleanup runs ``taskkill /F /T``, which POSIX tests do not stub
        the way they stub ``os.killpg``. A test double's made-up pid can name
        a live process of the developer; it would die with its descendants.
        """
        try:
            parents = psutil.Process(pid).parents()
        except psutil.NoSuchProcess:
            return
        except psutil.Error:
            parents = []
        if all(parent.pid != os.getpid() for parent in parents):
            _foreign_tree_kills.append(pid)
            return
        _kill_process_tree(pid)

    # Product modules bind the name when imported, which is after conftest.
    os_compat.kill_process_tree = _kill_own_process_tree


@pytest.fixture(autouse=True)
def _refuse_foreign_process_tree_kills():
    yield
    if _foreign_tree_kills:
        pids = _foreign_tree_kills[:]
        _foreign_tree_kills.clear()
        pytest.fail(f"refused to stop process trees this test did not start: {pids}")


_ACCOUNT_PROFILE_ENV = (
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
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
) -> None:
    """Make every test opt in to account/profile configuration explicitly.

    ``WrapperConfig`` and account registries intentionally inherit the
    launching shell in production.  Unit tests must not inherit those values:
    otherwise a developer running pytest from a managed Wrapper/``CODEX_HOME`` gets
    a different registry and dozens of unrelated mock-signature failures.
    """
    for key in _ACCOUNT_PROFILE_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _isolate_native_picker_writes(monkeypatch, tmp_path_factory):
    """Legacy runtime mocks have no private account. Never write the real one.

    Settings integration tests opt in by giving their handle a tmp_path config
    root; those exercise the real writer, including errors and preservation.
    """
    from pathlib import Path
    from cc_remote.wrapper import sdk
    from cc_remote.wrapper.claude_settings import save_user_defaults

    temporary = tmp_path_factory.getbasetemp().resolve()

    def isolated_save(root, **choices):
        if Path(root).resolve().is_relative_to(temporary):
            return save_user_defaults(root, **choices)
        return None

    monkeypatch.setattr(sdk, "save_user_defaults", isolated_save)


@pytest.fixture(autouse=True)
def _isolate_default_codex_home(monkeypatch, tmp_path_factory):
    """codex_sessions resolves the default ~/.codex once, at import.

    Redirecting the home directory in a test cannot move it, so point it at
    an empty directory: no test reads the developer's real sessions.
    """
    from cc_remote.wrapper import codex_sessions

    home = tmp_path_factory.getbasetemp() / "default-codex-home"
    monkeypatch.setattr(codex_sessions, "_CONFIG", str(home / "config.toml"))
    monkeypatch.setattr(codex_sessions, "_ROOT", str(home / "sessions"))
    monkeypatch.setattr(codex_sessions, "_ARCHIVE_ROOT", str(home / "archived_sessions"))


@pytest.fixture
def browser_singletons():
    """Real Chromium-style IPC markers; keep Unix socket paths short on macOS."""
    from pathlib import Path
    import socket
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ccr-ipc-") as directory:
        temporary = Path(directory).resolve()
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(temporary / "SingletonSocket"))
            (temporary / "SingletonCookie").symlink_to("12345678901234567890")

            def install(root):
                (root / "SingletonCookie").symlink_to("12345678901234567890")
                (root / "SingletonLock").symlink_to("example.local-12345")
                (root / "SingletonSocket").symlink_to(temporary / "SingletonSocket")
                return temporary

            yield install
