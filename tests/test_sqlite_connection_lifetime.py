"""Store transactions commit/rollback and release native database handles."""
import sqlite3

import pytest

from cc_remote.wrapper.history_store import HistoryIndexStore
from cc_remote.wrapper.preview_capabilities import PreviewCapabilityStore
from cc_remote.wrapper.turn_changes import TurnChangeArchive


@pytest.mark.parametrize("store_type", [HistoryIndexStore, PreviewCapabilityStore, TurnChangeArchive])
def test_store_connections_close_after_commit_and_rollback(tmp_path, store_type):
    store = store_type(tmp_path)
    with store._connect() as committed:
        committed.execute("CREATE TABLE lifetime_test (value TEXT)")
        committed.execute("INSERT INTO lifetime_test VALUES ('committed')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        committed.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="abort"):
        with store._connect() as rolled_back:
            rolled_back.execute("INSERT INTO lifetime_test VALUES ('rolled back')")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        rolled_back.execute("SELECT 1")
    with store._connect() as fresh:
        assert fresh.execute("SELECT value FROM lifetime_test").fetchall()[0][0] == "committed"
        assert fresh.execute("SELECT COUNT(*) FROM lifetime_test").fetchone()[0] == 1
