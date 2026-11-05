"""cc-plan extraction: structured card data from a free-text answer."""
from __future__ import annotations

import json

from cc_remote.wrapper.room_plan import (
    MAX_ITEM_CHARS,
    MAX_LIST_ITEMS,
    MAX_SUMMARY_CHARS,
    RoomPlan,
    extract_plan,
    plan_instruction,
)


def block(payload: object) -> str:
    return "```cc-plan\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def test_extracts_a_full_plan_and_strips_the_block():
    text = "建议如下。\n\n" + block({
        "summary": "先加集成测试",
        "steps": ["补 fixture", "覆盖主流程"],
        "risks": ["用例不稳定"],
        "needs": ["确认目标目录"],
    }) + "\n\n以上。"
    prose, plan = extract_plan(text)
    assert plan is not None
    assert plan.summary == "先加集成测试"
    assert plan.steps == ["补 fixture", "覆盖主流程"]
    assert plan.risks == ["用例不稳定"]
    assert plan.needs == ["确认目标目录"]
    # The block must not appear in both the text and the card.
    assert "cc-plan" not in prose
    assert prose.startswith("建议如下。")
    assert prose.endswith("以上。")


def test_no_block_means_no_plan_and_text_unchanged():
    text = "就是一段普通回答，没有结构化块。"
    prose, plan = extract_plan(text)
    assert plan is None
    assert prose == text


def test_malformed_json_is_not_a_plan_and_the_block_is_kept_as_text():
    """A formatting mistake must not swallow the answer or raise."""
    text = "看这里\n```cc-plan\n{not json at all}\n```"
    prose, plan = extract_plan(text)
    assert plan is None
    assert prose == text, "an unparsed block stays visible rather than vanishing"


def test_empty_plan_is_ignored():
    prose, plan = extract_plan("答案\n" + block({}))
    assert plan is None
    assert "cc-plan" in prose


def test_partial_fields_are_allowed():
    _prose, plan = extract_plan("答案\n" + block({"steps": ["只给步骤"]}))
    assert plan is not None
    assert plan.steps == ["只给步骤"]
    assert plan.summary == ""
    assert plan.is_empty() is False


def test_items_accept_objects_and_clip():
    payload = {
        "steps": [
            {"text": "对象形式"},
            {"step": "另一种键"},
            {"label": "标签形式"},
            "  有空格  ",
            "",
            123,
        ],
        "summary": "s" * (MAX_SUMMARY_CHARS + 100),
    }
    _prose, plan = extract_plan("x\n" + block(payload))
    assert plan is not None
    assert plan.steps == ["对象形式", "另一种键", "标签形式", "有空格"]
    assert len(plan.summary) == MAX_SUMMARY_CHARS


def test_items_are_bounded():
    _prose, plan = extract_plan("x\n" + block({"steps": [f"step {i}" for i in range(50)]}))
    assert plan is not None
    assert len(plan.steps) == MAX_LIST_ITEMS
    _prose, plan = extract_plan("x\n" + block({"steps": ["y" * (MAX_ITEM_CHARS + 50)]}))
    assert plan is not None
    assert len(plan.steps[0]) == MAX_ITEM_CHARS


def test_only_the_first_block_is_consumed():
    text = "a\n" + block({"summary": "第一"}) + "\nb\n" + block({"summary": "第二"})
    prose, plan = extract_plan(text)
    assert plan is not None and plan.summary == "第一"
    assert "第二" in prose, "the leftover block stays as text"


def test_fence_variants_are_recognised():
    for opener in ("```cc-plan", "``` cc-plan", "```CC-PLAN"):
        _prose, plan = extract_plan(f"{opener}\n{{\"summary\": \"x\"}}\n```")
        assert plan is not None, opener


def test_empty_and_non_string_input():
    assert extract_plan("") == ("", None)
    assert extract_plan(None) == ("", None)  # type: ignore[arg-type]


def test_plan_round_trips_through_dict():
    plan = RoomPlan(summary="s", steps=["a"], risks=[], needs=["n"])
    again = RoomPlan.from_dict(plan.as_dict())
    assert again is not None
    assert again.as_dict() == plan.as_dict()
    assert RoomPlan.from_dict(None) is None
    assert RoomPlan.from_dict("nope") is None


def test_instruction_can_be_disabled():
    assert plan_instruction(True).find("cc-plan") >= 0
    assert plan_instruction(False) == ""
