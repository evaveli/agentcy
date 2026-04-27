"""Tests for strategist loop stub mode and convergence detection."""
import pytest


# ── Stub mode ──────────────────────────────────────────────────────────────

def test_stub_mode_enabled(monkeypatch):
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _is_stub_mode

    monkeypatch.setenv("LLM_STUB_MODE", "1")
    assert _is_stub_mode() is True

    monkeypatch.setenv("LLM_STUB_MODE", "true")
    assert _is_stub_mode() is True


def test_stub_mode_disabled(monkeypatch):
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _is_stub_mode

    monkeypatch.setenv("LLM_STUB_MODE", "0")
    assert _is_stub_mode() is False

    monkeypatch.setenv("LLM_STUB_MODE", "")
    assert _is_stub_mode() is False


def test_stub_delta_on_task_failure_with_downstream():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _stub_generate_delta

    graph = {
        "tasks": [{"task_id": "t1"}, {"task_id": "t2"}],
        "edges": [{"from": "t1", "to": "t2"}],
    }
    run_state = {"tasks": {"t1": {"status": "FAILED"}, "t2": {"status": "PENDING"}}}
    event = {"task_id": "t1", "status": "FAILED"}

    delta = _stub_generate_delta(graph_spec=graph, run_state=run_state, last_event=event)
    assert delta is not None
    assert "remove_edges" in delta
    assert len(delta["remove_edges"]) == 1
    assert delta["remove_edges"][0]["from"] == "t1"


def test_stub_delta_on_task_failure_no_downstream():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _stub_generate_delta

    graph = {
        "tasks": [{"task_id": "t1"}, {"task_id": "t2"}],
        "edges": [],
    }
    run_state = {"tasks": {"t1": {"status": "FAILED"}, "t2": {"status": "PENDING"}}}
    event = {"task_id": "t1", "status": "FAILED"}

    delta = _stub_generate_delta(graph_spec=graph, run_state=run_state, last_event=event)
    assert delta is not None
    assert "task_overrides" in delta
    assert "retry_candidate" in delta["task_overrides"]["t1"]["tags"]


def test_stub_delta_on_bottleneck():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _stub_generate_delta

    graph = {
        "tasks": [{"task_id": "t1"}, {"task_id": "t2"}, {"task_id": "t3"}],
        "edges": [],
    }
    run_state = {
        "tasks": {
            "t1": {"status": "COMPLETED"},
            "t2": {"status": "PENDING"},
            "t3": {"status": "PENDING"},
        }
    }
    event = {"task_id": "t1", "status": "COMPLETED"}

    delta = _stub_generate_delta(graph_spec=graph, run_state=run_state, last_event=event)
    assert delta is not None
    assert "task_overrides" in delta
    assert "parallel_candidate" in str(delta)


def test_stub_returns_none_for_irrelevant_event():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _stub_generate_delta

    graph = {"tasks": [{"task_id": "t1"}], "edges": []}
    run_state = {"tasks": {"t1": {"status": "COMPLETED"}}}
    event = {"task_id": "t1", "status": "COMPLETED"}

    delta = _stub_generate_delta(graph_spec=graph, run_state=run_state, last_event=event)
    # Only one task completed, no pending, no bottleneck
    assert delta is None


# ── Convergence detection ──────────────────────────────────────────────────

def test_convergence_blocks_after_max_suggestions(monkeypatch):
    from src.agentcy.agent_runtime.services import llm_strategist_loop as loop

    monkeypatch.setenv("LLM_STRATEGIST_MAX_SUGGESTIONS", "3")
    loop._suggestion_counts["run-test-max"] = 3

    result = loop._check_convergence("run-test-max")
    assert result is not None
    assert "max_suggestions_reached" in result

    # Cleanup
    del loop._suggestion_counts["run-test-max"]


def test_convergence_blocks_after_rejection_streak(monkeypatch):
    from src.agentcy.agent_runtime.services import llm_strategist_loop as loop

    monkeypatch.setenv("LLM_STRATEGIST_REJECTION_WINDOW", "2")
    loop._rejection_streak["run-test-rej"] = 2

    result = loop._check_convergence("run-test-rej")
    assert result is not None
    assert "consecutive_rejections" in result

    # Cleanup
    del loop._rejection_streak["run-test-rej"]


def test_convergence_allows_when_under_limits(monkeypatch):
    from src.agentcy.agent_runtime.services import llm_strategist_loop as loop

    monkeypatch.setenv("LLM_STRATEGIST_MAX_SUGGESTIONS", "10")
    monkeypatch.setenv("LLM_STRATEGIST_REJECTION_WINDOW", "3")
    loop._suggestion_counts["run-test-ok"] = 2
    loop._rejection_streak["run-test-ok"] = 1

    result = loop._check_convergence("run-test-ok")
    assert result is None

    # Cleanup
    del loop._suggestion_counts["run-test-ok"]
    del loop._rejection_streak["run-test-ok"]


def test_convergence_allows_fresh_run():
    from src.agentcy.agent_runtime.services import llm_strategist_loop as loop

    # A brand new run_id has no tracking entries
    result = loop._check_convergence("run-brand-new")
    assert result is None


# ── Delta helpers ──────────────────────────────────────────────────────────

def test_delta_has_changes_with_overrides():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _delta_has_changes

    assert _delta_has_changes({"task_overrides": {"t1": {"tags": ["x"]}}}) is True
    assert _delta_has_changes({"remove_tasks": ["t1"]}) is True
    assert _delta_has_changes({"add_edges": [{"from": "a", "to": "b"}]}) is True


def test_delta_has_changes_empty():
    from src.agentcy.agent_runtime.services.llm_strategist_loop import _delta_has_changes

    assert _delta_has_changes({}) is False
    assert _delta_has_changes({"task_overrides": {}}) is False
    assert _delta_has_changes({"remove_tasks": []}) is False
