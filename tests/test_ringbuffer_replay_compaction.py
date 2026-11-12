import pytest

from cc_remote.protocol import AssistantMsgStart, Delta, UserMsg
from cc_remote.wrapper.ringbuffer import RingBuffer


@pytest.mark.parametrize("mode", ["cursor", "future", "rebuild", "current"])
def test_replay_compacts_adjacent_deltas_and_preserves_cursor_tail(mode):
    ring = RingBuffer(100, 100_000)
    user = UserMsg(msg_id="u", prompt="hello")
    user.seq = 1
    start = AssistantMsgStart(message_id="m")
    start.seq = 2
    first = Delta(message_id="m", text="a")
    first.seq = 3
    second = Delta(message_id="m", text="b")
    second.seq = 4
    for frame in (user, start, first, second):
        ring.append(frame)

    if mode == "current":
        replay = ring.current_turn_replay(message_id="u")
    else:
        replay = ring.replay_from(100 if mode == "future" else 0,
                                 cc_session_id="s", state="running",
                                 rebuild=mode == "rebuild")
    deltas = [frame for frame in replay if frame.type == "delta"]
    assert len(deltas) == 1
    assert deltas[0].text == "ab"
    assert deltas[0].seq == 4
    assert replay[-1].to_seq == 4


def test_replay_preserves_replace_boundaries_and_source_ring():
    ring = RingBuffer(100, 100_000)
    values = [("a", False), ("prefix", True), ("replacement", True), ("b", False)]
    for seq, (text, replace) in enumerate(values, 1):
        delta = Delta(message_id="m", text=text, replace=replace, seq=seq)
        ring.append(delta)
    frames = ring.replay_from(0, cc_session_id="s", state="running")
    assert [(f.text, f.replace) for f in frames if f.type == "delta"] == values
    suffix = ring.replay_from(3, cc_session_id="s", state="running")
    assert [(f.text, f.seq) for f in suffix if f.type == "delta"] == [("b", 4)]
