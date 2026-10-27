"""Suite-wide isolation from a developer's real engine account registries."""

from __future__ import annotations

import pytest
import sys
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
) -> None:
    """Make every test opt in to account/profile configuration explicitly.

    ``WrapperConfig`` and account registries intentionally inherit the
    launching shell in production.  Unit tests must not inherit those values:
    otherwise a developer running pytest from a managed Wrapper/``CODEX_HOME`` gets
    a different registry and dozens of unrelated mock-signature failures.
    """
    for key in _ACCOUNT_PROFILE_ENV:
        monkeypatch.delenv(key, raising=False)
