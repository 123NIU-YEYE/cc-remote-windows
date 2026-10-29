"""Rollout merge: old Codex conversations must not vanish from the sidebar."""
from __future__ import annotations

import json

import pytest

from cc_remote.wrapper.codex_sessions import (
    _read_rollout_head,
    _rollout_only_sessions,
    _rollout_session_id,
    _usable_prompt,
)


def _rollout(path, *entries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def _meta(*, cwd=None, branch=None, subagent=False, prompt=None) -> list[dict]:
    payload = {"type": "session_meta"}
    if cwd:
        payload["cwd"] = cwd
    if branch:
        payload["git"] = {"branch": branch}
    if subagent:
        payload["thread_source"] = "subagent"
        payload["source"] = {"subagent": {"thread_spawn": {"depth": 1}}}
    entries = [{"type": "session_meta", "payload": payload}]
    if prompt is not None:
        entries.append({"type": "event", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "text", "text": prompt}],
        }})
    return entries


def test_session_id_is_parsed_from_the_filename(tmp_path):
    a = tmp_path / "rollout-2026-08-04T19-36-05-019fcc8f-37ac-7602-846b-ef1b7f3ae419.jsonl"
    assert _rollout_session_id(a) == "019fcc8f-37ac-7602-846b-ef1b7f3ae419"


def test_a_forked_filename_yields_the_parent_id(tmp_path):
    """Forked rollouts embed a second id; the thread is the first one."""
    a = tmp_path / "rollout-2026-10-18T18-24-39-01a14db0-ab1f-7ac0-8f35-fd50f6226c37_01a14e8a-c350-72b3-b699-9abaf9d1387d.jsonl"
    assert _rollout_session_id(a) == "01a14db0-ab1f-7ac0-8f35-fd50f6226c37"


def test_a_name_without_an_id_is_ignored(tmp_path):
    assert _rollout_session_id(tmp_path / "rollout-not-an-id.jsonl") is None
    assert _rollout_session_id(tmp_path / "notes.jsonl") is None


def test_head_extracts_cwd_prompt_and_branch(tmp_path):
    path = tmp_path / "rollout-2026-10-16T09-06-44-01a14414-66e1-77b1-b3d0-ca6ea5f0ed15.jsonl"
    _rollout(path, *_meta(cwd=r"D:\work", branch="feat/x",
                          prompt="给这个项目加测试"))
    cwd, prompt, branch, subagent = _read_rollout_head(path)
    assert cwd == r"D:\work"
    assert prompt == "给这个项目加测试"
    assert branch == "feat/x"
    assert subagent is False


def test_head_flags_subagent_threads(tmp_path):
    path = tmp_path / "rollout-2026-10-27T21-41-58-01a17d98-a3f9-73c0-b07f-30a70ee40380.jsonl"
    _rollout(path, *_meta(cwd=r"D:\cc-remote", subagent=True, prompt="子任务"))
    assert _read_rollout_head(path)[3] is True


def test_injected_context_is_not_a_prompt():
    assert _usable_prompt("<environment_context>\n  <cwd>C:\\x</cwd>") is None
    assert _usable_prompt("<user_instructions>...</user_instructions>") is None
    assert _usable_prompt("   ") is None
    assert _usable_prompt(None) is None
    assert _usable_prompt("真正的请求") == "真正的请求"


def test_merge_adds_disk_only_sessions_and_skips_subagents(tmp_path):
    """The RPC omits old threads; their transcripts must still reach the client."""
    root = tmp_path / "sessions"
    normal = root / "2026" / "10" / "16" / \
        "rollout-2026-10-16T09-06-44-01a14414-66e1-77b1-b3d0-ca6ea5f0ed15.jsonl"
    _rollout(normal, *_meta(cwd=r"D:\work", prompt="加测试"))

    child = root / "2026" / "10" / "27" / \
        "rollout-2026-10-27T21-41-58-01a17d98-a3f9-73c0-b07f-30a70ee40380.jsonl"
    _rollout(child, *_meta(cwd=r"D:\cc-remote", subagent=True, prompt="子任务"))

    already = root / "2026" / "10" / "28" / \
        "rollout-2026-10-28T19-20-12-01a1823d-301a-78d3-b6cd-ece636707cfe.jsonl"
    _rollout(already, *_meta(cwd=r"D:\cc-remote", prompt="已在列表"))

    found = _rollout_only_sessions(tmp_path, known={"01a1823d-301a-78d3-b6cd-ece636707cfe"})

    assert set(found) == {"01a14414-66e1-77b1-b3d0-ca6ea5f0ed15"}
    entry = found["01a14414-66e1-77b1-b3d0-ca6ea5f0ed15"]
    assert entry["summary"] == "加测试"
    assert entry["cwd"] == r"D:\work"
    assert entry["last_modified"].isdigit()
    assert entry["tag"] is None


def test_merge_is_empty_without_a_sessions_directory(tmp_path):
    assert _rollout_only_sessions(tmp_path, known=set()) == {}


def test_merge_survives_a_corrupt_rollout(tmp_path):
    root = tmp_path / "sessions"
    broken = root / "rollout-2026-10-01T00-00-00-01a00000-0000-7000-8000-000000000000.jsonl"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('{"type":"session_meta","payload":{"cwd":"D:\\\\x"\n', encoding="utf-8")
    found = _rollout_only_sessions(tmp_path, known=set())
    assert set(found) == {"01a00000-0000-7000-8000-000000000000"}
    assert found["01a00000-0000-7000-8000-000000000000"]["cwd"] is None
