"""The sidebar merge of rollouts missing from thread/list. No live engines."""
import json
from uuid import uuid4

from cc_remote.wrapper import codex_sessions


def _rollout(home, provider, *, subagent=False):
    sid = str(uuid4())
    meta = {"id": sid, "cwd": "/project", "model_provider": provider}
    if provider is None:
        del meta["model_provider"]
    if subagent:
        meta["source"] = {"subagent": {"parent": "x"}}
    rows = [
        {"type": "session_meta", "payload": meta},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": f"task for {provider}"}]}},
    ]
    path = home / "sessions" / "2026" / "11" / "24" / f"rollout-2026-11-24T10-00-00-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(json.dumps(row) + "\n" for row in rows).encode())
    return sid


def test_merge_respects_the_provider_filter_of_thread_list(tmp_path):
    custom = _rollout(tmp_path, "custom")
    other = _rollout(tmp_path, "openai")
    unknown = _rollout(tmp_path, None)
    _rollout(tmp_path, "custom", subagent=True)

    found = codex_sessions._rollout_only_sessions(tmp_path, known=set(), provider="custom")
    assert set(found) == {custom, unknown}
    assert found[custom]["first_prompt"] == "task for custom"

    unfiltered = codex_sessions._rollout_only_sessions(tmp_path, known=set())
    assert set(unfiltered) == {custom, other, unknown}


def test_merge_never_repeats_listed_threads(tmp_path):
    listed = _rollout(tmp_path, "custom")
    missing = _rollout(tmp_path, "custom")
    found = codex_sessions._rollout_only_sessions(tmp_path, known={listed}, provider="custom")
    assert set(found) == {missing}
