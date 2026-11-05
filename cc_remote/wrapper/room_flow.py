"""Explicit plan/execute state machine for a room.

The point of a state machine rather than a prompt convention: "one agent advises,
another executes" must stop and wait for a human between the two phases. A model
cannot be trusted to hold that boundary, so the room records it:

    idle ──post@advisor──▶ planning ──reply with steps──▶ awaiting_execute
                                   └─reply without steps─▶ idle
    awaiting_execute ──approve──▶ executing ──all done──▶ idle
    awaiting_execute ──discard──▶ idle

Only ``awaiting_execute`` can be approved, and only that transition dispatches to
the executor. Everything else is refused with a reason, so a stale click on an old
card cannot start work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PHASE_IDLE = "idle"
PHASE_PLANNING = "planning"
PHASE_AWAITING = "awaiting_execute"
PHASE_EXECUTING = "executing"

PHASES = (PHASE_IDLE, PHASE_PLANNING, PHASE_AWAITING, PHASE_EXECUTING)

# Bounded so a runaway plan cannot produce an unbounded dispatch fan-out.
MAX_STEPS = 12


class PlanStateError(ValueError):
    """A requested transition is not legal in the current phase."""


@dataclass
class PlanStep:
    step_id: str
    text: str
    status: str = "pending"     # pending|running|done|failed|skipped
    owner: Optional[str] = None  # member_id once dispatched

    def as_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "text": self.text,
            "status": self.status,
            "owner": self.owner,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlanStep":
        return cls(
            step_id=str(payload["step_id"]),
            text=str(payload.get("text") or "")[:2000],
            status=str(payload.get("status") or "pending"),
            owner=(str(payload["owner"]) if payload.get("owner") else None),
        )


@dataclass
class PlanState:
    """The room's current plan and where we are in its lifecycle."""

    phase: str = PHASE_IDLE
    # Member that produced the plan (the advisor).
    author_member: Optional[str] = None
    # Message holding the plan card, so the UI can point at it.
    source_msg_id: Optional[str] = None
    summary: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    # Member the steps will be dispatched to once approved.
    executor_member: Optional[str] = None
    started_at: Optional[float] = None
    approved_at: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "author_member": self.author_member,
            "source_msg_id": self.source_msg_id,
            "summary": self.summary,
            "steps": [step.as_dict() for step in self.steps],
            "executor_member": self.executor_member,
            "started_at": self.started_at,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "PlanState":
        if not isinstance(payload, dict):
            return cls()
        phase = str(payload.get("phase") or PHASE_IDLE)
        raw_steps = payload.get("steps")
        steps: list[PlanStep] = []
        if isinstance(raw_steps, list):
            for item in raw_steps:
                if not isinstance(item, dict) or "step_id" not in item:
                    continue
                steps.append(PlanStep.from_dict(item))
        return cls(
            phase=phase if phase in PHASES else PHASE_IDLE,
            author_member=(str(payload["author_member"])
                           if payload.get("author_member") else None),
            source_msg_id=(str(payload["source_msg_id"])
                           if payload.get("source_msg_id") else None),
            summary=str(payload.get("summary") or "")[:1000],
            steps=steps[:MAX_STEPS],
            executor_member=(str(payload["executor_member"])
                             if payload.get("executor_member") else None),
            started_at=(float(payload["started_at"])
                        if payload.get("started_at") else None),
            approved_at=(float(payload["approved_at"])
                         if payload.get("approved_at") else None),
        )

    def pending_steps(self) -> list[PlanStep]:
        return [step for step in self.steps if step.status == "pending"]

    def all_finished(self) -> bool:
        return bool(self.steps) and all(
            step.status in {"done", "failed", "skipped"} for step in self.steps)


def begin_planning(state: PlanState, author_member: str,
                   source_msg_id: Optional[str] = None) -> PlanState:
    """An advisor was addressed: enter (or restart) the planning phase."""
    return PlanState(
        phase=PHASE_PLANNING,
        author_member=author_member,
        source_msg_id=source_msg_id,
        executor_member=state.executor_member,
    )


def record_plan(state: PlanState, *, summary: str, steps: list[str],
                msg_id: Optional[str] = None) -> PlanState:
    """The advisor answered. Steps present → wait for approval; else go idle."""
    if state.phase != PHASE_PLANNING:
        raise PlanStateError(f"not planning (phase={state.phase})")
    cleaned = [text.strip()[:2000] for text in steps if text and text.strip()]
    cleaned = cleaned[:MAX_STEPS]
    if not cleaned:
        # A plan without steps is just advice: nothing to approve or execute.
        return PlanState(phase=PHASE_IDLE, executor_member=state.executor_member)
    state.summary = summary.strip()[:1000]
    state.source_msg_id = msg_id or state.source_msg_id
    state.steps = [
        PlanStep(step_id=f"step-{index + 1}", text=text)
        for index, text in enumerate(cleaned)
    ]
    state.phase = PHASE_AWAITING
    return state


def approve(state: PlanState, executor_member: str) -> PlanState:
    """The human clicked 执行: only legal while awaiting approval."""
    if state.phase != PHASE_AWAITING:
        raise PlanStateError(
            "当前没有等待执行计划" if state.phase != PHASE_EXECUTING
            else "计划已在执行中")
    if not state.steps:
        raise PlanStateError("计划没有可执行的步骤")
    if not executor_member:
        raise PlanStateError("请先选择执行成员")
    state.phase = PHASE_EXECUTING
    state.executor_member = executor_member
    state.approved_at = None  # filled by the caller with a timestamp
    for step in state.steps:
        step.status = "pending"
        step.owner = None
    return state


def discard(state: PlanState) -> PlanState:
    """The human dismissed the plan."""
    if state.phase == PHASE_EXECUTING:
        raise PlanStateError("计划已在执行中，无法丢弃")
    return PlanState(
        phase=PHASE_IDLE,
        executor_member=state.executor_member,
    )


def mark_step(state: PlanState, step_id: str, status: str,
              owner: Optional[str] = None) -> PlanState:
    if status not in {"pending", "running", "done", "failed", "skipped"}:
        raise PlanStateError(f"unknown step status: {status}")
    for step in state.steps:
        if step.step_id == step_id:
            step.status = status
            if owner:
                step.owner = owner
            if state.phase == PHASE_EXECUTING and state.all_finished():
                state.phase = PHASE_IDLE
            return state
    raise PlanStateError(f"unknown step: {step_id}")


def plan_prompt(plan: PlanState) -> str:
    """Prompt fragment telling the executor what to do, step by step."""
    if not plan.steps:
        return ""
    lines = ["[执行计划]", f"总目标：{plan.summary}" if plan.summary else "按下列步骤执行："]
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. {step.text}")
    lines.append("要求：按顺序执行；每完成一步在回答里说明该步结果；不要扩大范围。")
    return "\n".join(lines)


def advisor_prompt() -> str:
    """Prompt fragment telling the advisor to produce an executable plan."""
    return (
        "[角色：顾问] 请只给方案，不要动手执行。"
        "在 ```cc-plan 块里的 steps 数组中给出可直接执行的步骤（每条一句话、可验证），"
        "执行方会照着做。"
    )
