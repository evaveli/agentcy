import pytest

from src.agentcy.agent_runtime.services.plan_revision_utils import apply_delta, validate_runtime_constraints, validate_candidate_graph


def _base_graph():
    return {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["plan"],
                "tags": ["core"],
                "task_type": "plan",
            },
            {
                "task_id": "t2",
                "assigned_agent": "agent-b",
                "required_capabilities": ["execute"],
                "tags": ["core"],
                "task_type": "execute",
            },
        ],
        "edges": [{"from": "t1", "to": "t2"}],
    }


def _run_doc(status_t1="COMPLETED", status_t2="PENDING"):
    return {
        "tasks": {
            "t1": {"status": status_t1},
            "t2": {"status": status_t2},
        }
    }


def test_apply_delta_overrides():
    base = _base_graph()
    delta = {"task_overrides": {"t2": {"tags": ["llm_suggested"]}}}
    updated, applied = apply_delta(base, delta)
    assert applied == 1
    tasks = {t["task_id"]: t for t in updated["tasks"]}
    assert tasks["t2"]["tags"] == ["llm_suggested"]


def test_runtime_constraints_block_removal_of_started_task():
    base = _base_graph()
    candidate, _ = apply_delta(base, {"remove_tasks": ["t1"]})
    violations = validate_runtime_constraints(
        base_graph=base,
        candidate_graph=candidate,
        run_doc=_run_doc(),
    )
    assert any(v["code"] == "remove_started_task" for v in violations)


def test_candidate_graph_validation_conforms_on_safe_delta():
    base = _base_graph()
    candidate, _ = apply_delta(base, {"task_overrides": {"t2": {"tags": ["updated"]}}})
    report = validate_candidate_graph(
        candidate_graph=candidate,
        base_graph=base,
        run_doc=_run_doc(status_t1="COMPLETED", status_t2="PENDING"),
        plan_id="plan-1",
        pipeline_id="pipe-1",
        username="tester",
    )
    assert report["conforms"] is True
