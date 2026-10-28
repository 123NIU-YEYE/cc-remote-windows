"""Failure classification: retry only what can heal itself."""
from __future__ import annotations

from cc_remote.wrapper.room_retry import RETRY_LIMIT, is_transient, should_retry


def test_missing_directory_is_permanent():
    message = '无法为「Claude · iris」创建会话: _SpawnFailure: 目录不存在: "D:\\gone"'
    assert is_transient(message) is False
    assert should_retry(message, 1) is False


def test_bound_session_requirement_is_permanent():
    assert is_transient("DSH 成员需要先绑定一个已存在的会话") is False


def test_connection_problems_are_transient():
    for message in (
        "ConnectionResetError: reset by peer",
        "timed out during opening handshake",
        "relay connection lost",
        "503 Service Unavailable",
        "socket is temporarily unavailable",
    ):
        assert is_transient(message) is True, message


def test_empty_message_is_not_retried():
    """An empty message is a programming error, not a blip."""
    assert is_transient("") is False
    assert is_transient(None) is False  # type: ignore[arg-type]


def test_permanent_wins_over_transient():
    """A message mentioning both must not be retried."""
    assert is_transient("目录不存在: connection refused") is False


def test_retry_is_bounded():
    transient = "connection reset"
    assert should_retry(transient, 1) is True
    assert should_retry(transient, RETRY_LIMIT + 1) is False
    assert should_retry(transient, RETRY_LIMIT + 2) is False
