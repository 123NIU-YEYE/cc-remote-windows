"""Plan/execute state machine: the human boundary must hold."""
from __future__ import annotations

import pytest

from cc_remote.wrapper.room_flow import (
    MAX_STEPS,
    PHASE_AWAITING,
    PHASE_EXECUTING,
    PHASE_IDLE,
    PHASE_PLANNING,
    PlanState,
    PlanStateError,
    advisor_prompt,
    approve,
    begin_planning,
    discard,
    mark_step,
    plan_prompt,
    record_plan,
)


def _awaiting(steps=("步骤一", "步骤二")) -> PlanState:
    state = begin_planning(PlanState(), "m-advisor")
    return record_plan(state, summary="总目标", steps=list(steps))


def test_idle_to_planning_on_dispatch():
    state = begin_planning(PlanState(), "m-advisor")
    assert state.phase == PHASE_PLANNING
    assert state.author_member == "m-advisor"
    assert state.steps == []


def test_planning_with_steps_waits_for_approval():
    state = _awaiting()
    assert state.phase == PHASE_AWAITING
    assert [step.text for step in state.steps] == ["步骤一", "步骤二"]
    assert [step.step_id for step in state.steps] == ["step-1", "step-2"]
    assert all(step.status == "pending" for step in state.steps)
    assert state.summary == "总目标"


def test_planning_without_steps_goes_idle():
    """Advice without steps is not executable, so nothing should wait."""
    state = begin_planning(PlanState(), "m-advisor")
    state = record_plan(state, summary="只是建议", steps=[])
    assert state.phase == PHASE_IDLE
    assert state.steps == []


def test_blank_and_duplicate_steps_are_dropped():
    state = begin_planning(PlanState(), "m")
    state = record_plan(state, summary="s", steps=["   ", "真步骤", ""])
    assert state.phase == PHASE_AWAITING
    assert [step.text for step in state.steps] == ["真步骤"]


def test_steps_are_bounded():
    state = begin_planning(PlanState(), "m")
    state = record_plan(state, summary="s", steps=[f"step {i}" for i in range(50)])
    assert len(state.steps) == MAX_STEPS


def test_approve_requires_awaiting():
    """The whole point: an executor cannot be dispatched without approval."""
    with pytest.raises(PlanStateError):
        approve(PlanState(), "m-exec")
    with pytest.raises(PlanStateError):
        approve(begin_planning(PlanState(), "m"), "m-exec")
    executing = approve(_awaiting(), "m-exec")
    with pytest.raises(PlanStateError):
        approve(executing, "m-exec")


def test_approve_requires_an_executor_and_steps():
    with pytest.raises(PlanStateError):
        approve(_awaiting(), "")
    with pytest.raises(PlanStateError):
        approve(PlanState(phase=PHASE_AWAITING, steps=[]), "m-exec")


def test_approve_sets_executor_and_resets_step_state():
    state = _awaiting()
    state.steps[0].status = "done"      # stale from a previous run
    state.steps[0].owner = "someone"
    executing = approve(state, "m-exec")
    assert executing.phase == PHASE_EXECUTING
    assert executing.executor_member == "m-exec"
    assert all(step.status == "pending" for step in executing.steps)
    assert all(step.owner is None for step in executing.steps)


def test_steps_completing_returns_to_idle():
    state = approve(_awaiting(), "m-exec")
    mark_step(state, "step-1", "running", owner="m-exec")
    assert state.phase == PHASE_EXECUTING
    mark_step(state, "step-1", "done")
    assert state.phase == PHASE_EXECUTING, "still one step left"
    mark_step(state, "step-2", "done")
    assert state.phase == PHASE_IDLE


def test_failed_and_skipped_steps_also_finish_the_run():
    state = approve(_awaiting(("a", "b")), "m-exec")
    mark_step(state, "step-1", "failed")
    mark_step(state, "step-2", "skipped")
    assert state.phase == PHASE_IDLE


def test_mark_step_rejects_unknown_ids_and_statuses():
    state = approve(_awaiting(), "m-exec")
    with pytest.raises(PlanStateError):
        mark_step(state, "step-99", "done")
    with pytest.raises(PlanStateError):
        mark_step(state, "step-1", "exploded")


def test_discard_clears_the_plan():
    state = discard(_awaiting())
    assert state.phase == PHASE_IDLE
    assert state.steps == []


def test_discard_refused_while_executing():
    with pytest.raises(PlanStateError):
        discard(approve(_awaiting(), "m-exec"))


def test_pending_steps_and_all_finished():
    state = approve(_awaiting(("a", "b")), "m-exec")
    assert len(state.pending_steps()) == 2
    assert state.all_finished() is False
    mark_step(state, "step-1", "done")
    mark_step(state, "step-2", "done")
    assert state.pending_steps() == []
    assert state.all_finished() is True


def test_round_trips_through_dict():
    state = approve(_awaiting(), "m-exec")
    mark_step(state, "step-1", "running", owner="m-exec")
    again = PlanState.from_dict(state.as_dict())
    assert again.phase == PHASE_EXECUTING
    assert again.executor_member == "m-exec"
    assert [step.status for step in again.steps] == ["running", "pending"]
    assert again.steps[0].owner == "m-exec"


def test_from_dict_tolerates_junk():
    assert PlanState.from_dict(None).phase == PHASE_IDLE
    assert PlanState.from_dict({"phase": "nonsense"}).phase == PHASE_IDLE
    assert PlanState.from_dict({"steps": "not a list"}).steps == []


def test_prompts_mention_the_expected_behaviour():
    plan = approve(_awaiting(("补测试", "跑通")), "m-exec")
    text = plan_prompt(plan)
    assert "[执行计划]" in text
    assert "1. 补测试" in text and "2. 跑通" in text
    assert "不要扩大范围" in text
    advisor = advisor_prompt()
    assert "顾问" in advisor and "cc-plan" in advisor
    assert "不要动手执行" in advisor
    assert plan_prompt(PlanState()) == ""
