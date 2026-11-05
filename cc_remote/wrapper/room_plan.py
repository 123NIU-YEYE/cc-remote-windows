"""Parse the structured block a room member may emit at the end of its answer.

A free-text reply makes the room read like a chat log. Asking members to close
with a fenced ``cc-plan`` block gives the timeline something to render as a card
(summary / steps / risks / needs) without forcing the whole answer to be JSON,
which models are worse at and which would destroy the readable text.

The parser is deliberately forgiving: anything malformed is simply "no plan", and
the original text is shown as-is. Never raise, never retry — a formatting mistake
must not cost a turn.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

BLOCK_LANGUAGE = "cc-plan"
# Bounded so one reply cannot smuggle an unbounded structure into storage.
MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 500
MAX_SUMMARY_CHARS = 1000
MAX_BLOCK_CHARS = 20_000

_FENCE = re.compile(
    r"```[ \t]*" + re.escape(BLOCK_LANGUAGE) + r"[ \t]*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class RoomPlan:
    """The structured part of a member's answer."""

    summary: str = ""
    steps: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.summary or self.steps or self.risks or self.needs)

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "steps": list(self.steps),
            "risks": list(self.risks),
            "needs": list(self.needs),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> Optional["RoomPlan"]:
        if not isinstance(payload, dict):
            return None
        plan = cls(
            summary=_clip(payload.get("summary"), MAX_SUMMARY_CHARS),
            steps=_items(payload.get("steps")),
            risks=_items(payload.get("risks")),
            needs=_items(payload.get("needs")),
        )
        return None if plan.is_empty() else plan


def _clip(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _items(value: Any) -> list[str]:
    """Accept a list of strings, or objects with a ``text``/``label`` field."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            # Tolerate {"text": ...} / {"step": ...}: models add keys freely.
            text = item.get("text") or item.get("label") or item.get("step") or ""
        else:
            text = item
        if not isinstance(text, str):
            continue
        cleaned = text.strip()[:MAX_ITEM_CHARS]
        if cleaned:
            result.append(cleaned)
        if len(result) >= MAX_LIST_ITEMS:
            break
    return result


def extract_plan(text: str) -> tuple[str, Optional[RoomPlan]]:
    """Split an answer into (text without the block, parsed plan or None).

    Only the first block is consumed. The surrounding prose is returned with the
    block removed so the card and the text do not show the same JSON twice.
    """
    if not text or not isinstance(text, str):
        return text or "", None
    match = _FENCE.search(text)
    if not match:
        return text, None
    if len(match.group(1)) > MAX_BLOCK_CHARS:
        return text, None

    plan: Optional[RoomPlan] = None
    raw = match.group(1).strip()
    try:
        plan = RoomPlan.from_dict(json.loads(raw))
    except ValueError:
        plan = None

    # Drop the block even when it did not parse: an unparsed blob is noise, and
    # the raw block is preserved nowhere else, so keep it as text in that case.
    if plan is None:
        return text, None
    remainder = (text[:match.start()] + text[match.end():]).strip()
    return remainder, plan


def plan_instruction(allow: bool = True) -> str:
    """Prompt fragment telling the member how to emit the block."""
    if not allow:
        return ""
    return (
        "回答的最后附一个 ```cc-plan 代码块，内容为 JSON："
        '{"summary": "一句话结论", "steps": ["步骤"], '
        '"risks": ["风险"], "needs": ["需要我确认的点"]}。'
        "不需要的字段可以省略；除这个块以外正常用文字回答。"
    )
