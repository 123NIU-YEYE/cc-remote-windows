"""Room store and @mention routing."""
from __future__ import annotations

import json

import pytest

from cc_remote.wrapper.rooms import (
    MESSAGE_LIMIT,
    MEMBER_LIMIT,
    Room,
    RoomError,
    RoomMember,
    RoomMessage,
    RoomStore,
    parse_mentions,
)


def _members():
    return [
        RoomMember(member_id="m-claude", label="Claude · iris", engine="claude"),
        RoomMember(member_id="m-codex", label="Codex · nyx", engine="codex"),
    ]


@pytest.fixture()
def store(tmp_path):
    return RoomStore(tmp_path)


def test_create_list_get_delete_roundtrip(store, tmp_path):
    room = store.create("cc-remote 协作", "~/cooperation", _members())
    assert store.get(room.room_id) is room
    assert [item.room_id for item in store.list_rooms()] == [room.room_id]

    # A fresh store over the same directory must see the same room.
    again = RoomStore(tmp_path)
    loaded = again.get(room.room_id)
    assert loaded is not None
    assert loaded.name == "cc-remote 协作"
    assert [m.member_id for m in loaded.members] == ["m-claude", "m-codex"]

    assert again.delete(room.room_id) is True
    assert RoomStore(tmp_path).get(room.room_id) is None


def test_rejects_bad_rooms(store):
    with pytest.raises(RoomError):
        store.create("  ", "~/x", _members())
    with pytest.raises(RoomError):
        store.create("room", "", _members())
    with pytest.raises(RoomError):
        store.create("room", "~/x", [])
    with pytest.raises(RoomError):
        store.create("room", "~/x", _members() + _members())


def test_rejects_bad_members(store):
    with pytest.raises(RoomError):
        store.create("room", "~/x", [
            RoomMember(member_id="dup", label="A"),
            RoomMember(member_id="dup", label="B"),
        ])
    with pytest.raises(RoomError):
        store.create("room", "~/x", [
            RoomMember(member_id="m", label="A", engine="gpt5"),
        ])
    with pytest.raises(RoomError):
        store.create("room", "~/x", [
            RoomMember(member_id="m", label=""),
        ])
    many = [RoomMember(member_id=f"m{i}", label=f"A{i}")
            for i in range(MEMBER_LIMIT + 1)]
    with pytest.raises(RoomError):
        store.create("room", "~/x", many)


def test_update_keeps_session_binding(store):
    room = store.create("room", "~/x", _members())
    store.bind_session(room.room_id, "m-claude", "sess-1")

    # Renaming and re-sending the same member list must not drop the session.
    updated = store.update(
        room.room_id, name="renamed",
        members=[RoomMember(member_id="m-claude", label="Claude · iris"),
                 RoomMember(member_id="m-codex", label="Codex · nyx")])
    assert updated.name == "renamed"
    assert updated.member("m-claude").session_id == "sess-1"


def test_timeline_is_bounded(store):
    room = store.create("room", "~/x", _members())
    for index in range(MESSAGE_LIMIT + 25):
        store.append_message(room.room_id, RoomMessage(
            msg_id=f"m{index}", ts=float(index), author_kind="user",
            author_label="你", text=f"msg {index}"))
    timeline = store.get(room.room_id).messages
    assert len(timeline) == MESSAGE_LIMIT
    assert timeline[-1].msg_id == f"m{MESSAGE_LIMIT + 24}"
    assert timeline[0].msg_id == "m25"


def test_message_text_is_truncated(store):
    room = store.create("room", "~/x", _members())
    huge = "x" * 100_000
    store.append_message(room.room_id, RoomMessage(
        msg_id="m1", ts=1.0, author_kind="user", author_label="你", text=huge))
    assert len(store.get(room.room_id).messages[0].text) <= len(huge)


def test_corrupt_state_is_tolerated(tmp_path):
    (tmp_path / "rooms.json").write_text("{not json", encoding="utf-8")
    store = RoomStore(tmp_path)
    assert store.list_rooms() == []
    # The next write repairs the file.
    store.create("room", "~/x", _members())
    payload = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["rooms"]) == 1


# ---- mention routing ---------------------------------------------------

def test_mention_exact_label():
    assert parse_mentions("@Claude · iris 看下架构", _members()) == ["m-claude"]


def test_mention_tail_alias_is_case_insensitive():
    assert parse_mentions("@nyx 整理计划", _members()) == ["m-codex"]
    assert parse_mentions("@NYX 整理计划", _members()) == ["m-codex"]


def test_mention_both_members():
    got = parse_mentions("@Claude · iris @Codex · nyx 各自检查", _members())
    assert got == ["m-claude", "m-codex"]


def test_mention_broadcast():
    assert parse_mentions("@全体 各自检查一下", _members()) == ["m-claude", "m-codex"]
    assert parse_mentions("@all check", _members()) == ["m-claude", "m-codex"]


def test_mention_engine_segment_resolves_member():
    # People type the engine name, not the full display label.
    assert parse_mentions("@Claude 看下架构", _members()) == ["m-claude"]
    assert parse_mentions("@Codex 整理计划", _members()) == ["m-codex"]


def test_mention_shared_engine_segment_addresses_every_matching_member():
    members = [
        RoomMember(member_id="m-1", label="Claude · iris", engine="claude"),
        RoomMember(member_id="m-2", label="Claude · nyx", engine="claude"),
    ]
    # "@Claude" is ambiguous, so it reaches both; the agent name disambiguates.
    assert parse_mentions("@Claude 你好", members) == ["m-1", "m-2"]
    assert parse_mentions("@iris 你好", members) == ["m-1"]


def test_mention_unknown_and_plain_text_ignored():
    assert parse_mentions("@media 这个 CSS 有问题", _members()) == []
    assert parse_mentions("没有任何 @ 符号", _members()) == []
    assert parse_mentions("", _members()) == []


def test_mention_deduplicates():
    assert parse_mentions("@iris @iris @nyx", _members()) == ["m-claude", "m-codex"]
