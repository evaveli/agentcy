from __future__ import annotations

from pathlib import Path

import pytest

from src.agentcy.semantic.shacl_engine import validate_graph_spec


def _shapes_path() -> str:
    here = Path(__file__).resolve()
    root = here.parents[3]
    return str(root / "schemas" / "plan_draft_shapes.ttl")


def test_validate_graph_spec_flags_missing_assignment():
    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": None,
                "required_capabilities": ["cap-a"],
            }
        ],
        "edges": [],
    }

    result = validate_graph_spec(
        graph_spec,
        plan_id="plan-shacl-1",
        pipeline_id=None,
        username=None,
        shapes_path=_shapes_path(),
    )
    if result is None:
        pytest.skip("pyshacl not available")
    assert result["conforms"] is False
    assert result["results"]


def test_validate_graph_spec_conforms_on_valid_plan():
    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["cap-a"],
            }
        ],
        "edges": [],
    }

    result = validate_graph_spec(
        graph_spec,
        plan_id="plan-shacl-2",
        pipeline_id=None,
        username=None,
        shapes_path=_shapes_path(),
    )
    if result is None:
        pytest.skip("pyshacl not available")
    assert result["conforms"] is True
