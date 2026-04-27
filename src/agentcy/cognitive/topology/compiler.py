"""Compile a mutated topology skeleton into a valid PipelineCreate.

Reuses the existing template_matcher to assign agent templates to abstract
skeleton steps, then builds Task / DAGConfig / PipelineCreate objects that
feed into the existing pipeline creation flow unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentcy.cognitive.template_matcher import (
    WorkflowStep,
    best_matches,
    match_quality_score,
)
from agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    TopologySkeleton,
)


def _skeleton_step_to_workflow_step(step: Dict[str, Any]) -> WorkflowStep:
    """Convert a SkeletonStep dict to the WorkflowStep TypedDict used by template_matcher."""
    return WorkflowStep(
        step_id=step.get("step_id", ""),
        description=step.get("description", step.get("name", "")),
        inferred_capabilities=list(step.get("required_capabilities", [])),
        inferred_tags=list(step.get("required_tags", [])),
        dependencies=list(step.get("dependencies", [])),
        is_entry=step.get("is_entry", False),
        is_final=step.get("is_final", False),
    )


def _error_handling_for_criticality(criticality: str) -> Dict[str, Any]:
    """Return default error handling config based on decision criticality."""
    if criticality == "high":
        return {
            "retry_policy": {"max_retries": 5, "backoff_strategy": "exponential"},
            "on_failure": "escalate",
        }
    if criticality == "medium":
        return {
            "retry_policy": {"max_retries": 3, "backoff_strategy": "exponential"},
            "on_failure": "retry",
        }
    return {
        "retry_policy": {"max_retries": 1, "backoff_strategy": "linear"},
        "on_failure": "skip",
    }


def compile_skeleton_to_pipeline(
    skeleton: TopologySkeleton,
    business_template: BusinessTemplate,
    agent_templates: List[Dict[str, Any]],
    vhost: str = "/",
) -> Dict[str, Any]:
    """Convert a (mutated) skeleton + business template into a PipelineCreate dict.

    Returns a dict matching the ``PipelineCreate`` schema so it can be fed directly
    into the existing pipeline creation API.  The caller can instantiate
    ``PipelineCreate.model_validate(result)`` to get full Pydantic validation.

    The ``agent_templates`` list should be dicts from the template store.
    """
    # Convert skeleton steps to WorkflowSteps for template matching
    step_dicts = [s.model_dump() for s in skeleton.steps]
    workflow_steps = [_skeleton_step_to_workflow_step(sd) for sd in step_dicts]

    # Match agent templates to each step
    matches = best_matches(workflow_steps, agent_templates, min_score=0.0)
    quality = match_quality_score(matches)

    # Build Task list
    tasks: List[Dict[str, Any]] = []
    for step in skeleton.steps:
        match = matches.get(step.step_id)
        if match is not None:
            service_name = match.get("template_name", f"{step.role}-service")
            action = "process"
        else:
            service_name = f"{step.role}-service"
            action = "process"

        task = {
            "id": step.step_id,
            "name": step.name,
            "available_services": service_name,
            "action": action,
            "is_entry": step.is_entry,
            "is_final_task": step.is_final,
            "description": step.description or step.name,
            "inputs": {"dependencies": list(step.dependencies)} if step.dependencies else None,
        }
        tasks.append(task)

    # Build error handling
    if skeleton.default_error_handling:
        error_handling = skeleton.default_error_handling
    else:
        error_handling = _error_handling_for_criticality(business_template.decision_criticality)

    # Build PipelineCreate dict
    pipeline_create = {
        "name": f"{business_template.workflow_class}_{skeleton.skeleton_id[:8]}",
        "vhost": vhost,
        "pipeline_name": f"topology_{business_template.workflow_class}",
        "description": (
            f"Auto-generated from topology skeleton '{skeleton.name}'. "
            f"Match quality: {quality:.2f}."
        ),
        "dag": {"tasks": tasks},
        "error_handling": error_handling,
    }
    return pipeline_create
