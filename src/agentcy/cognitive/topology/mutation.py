"""Conditional mutation engine for topology skeletons.

Evaluates rules against a BusinessTemplate and applies structural mutations
(insert, remove, modify) to a skeleton's step graph.  Dependency rewiring
is handled automatically to maintain DAG validity.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    MutationAction,
    MutationCondition,
    MutationRule,
    SkeletonStep,
    TopologySkeleton,
)


def _resolve_field(obj: Dict[str, Any], field_path: str) -> Any:
    """Resolve a dotted field path against a dict."""
    parts = field_path.split(".")
    current: Any = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def evaluate_condition(condition: MutationCondition, business_template: BusinessTemplate) -> bool:
    """Evaluate a single condition against a business template."""
    bt_dict = business_template.model_dump()
    actual = _resolve_field(bt_dict, condition.field)

    op = condition.operator
    expected = condition.value

    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        # "value in field" — field should be a list
        if isinstance(actual, list):
            return expected in actual
        return False
    if op == "gte":
        try:
            return float(actual) >= float(expected)
        except (TypeError, ValueError):
            return False
    if op == "lte":
        try:
            return float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    return False


def _find_step_index(steps: List[SkeletonStep], step_id: str) -> Optional[int]:
    """Find index of a step by step_id."""
    for i, s in enumerate(steps):
        if s.step_id == step_id:
            return i
    return None


def _rewire_after_insert(
    steps: List[SkeletonStep],
    target_id: str,
    new_step: SkeletonStep,
    position: str,
) -> None:
    """Rewire dependencies after inserting a step before or after *target_id*.

    For ``insert_after``:
      - new_step depends on target
      - any step that depended on target now depends on new_step

    For ``insert_before``:
      - new_step inherits target's dependencies
      - target now depends on new_step
    """
    if position == "insert_after":
        # new_step depends on target
        if target_id not in new_step.dependencies:
            new_step.dependencies = list(new_step.dependencies) + [target_id]

        # Steps that depended on target now depend on new_step
        for s in steps:
            if s.step_id == new_step.step_id:
                continue
            if target_id in s.dependencies:
                deps = list(s.dependencies)
                deps = [new_step.step_id if d == target_id else d for d in deps]
                s.dependencies = deps

    elif position == "insert_before":
        target_step = next((s for s in steps if s.step_id == target_id), None)
        if target_step is None:
            return

        # new_step inherits target's dependencies
        new_step.dependencies = list(target_step.dependencies)

        # target now depends on new_step
        target_step.dependencies = [new_step.step_id]


def _apply_action(
    steps: List[SkeletonStep],
    action: MutationAction,
) -> None:
    """Apply a single mutation action to the step list (in-place)."""
    if action.action_type in ("insert_after", "insert_before") and action.step is not None:
        target_idx = _find_step_index(steps, action.target_step_id)
        if target_idx is None:
            return

        new_step = action.step.model_copy(deep=True)

        if action.action_type == "insert_after":
            steps.insert(target_idx + 1, new_step)
            _rewire_after_insert(steps, action.target_step_id, new_step, "insert_after")
        else:
            steps.insert(target_idx, new_step)
            _rewire_after_insert(steps, action.target_step_id, new_step, "insert_before")

    elif action.action_type == "remove":
        target_idx = _find_step_index(steps, action.target_step_id)
        if target_idx is None:
            return
        removed = steps[target_idx]
        removed_deps = list(removed.dependencies)

        # Steps that depended on removed now depend on removed's dependencies
        for s in steps:
            if action.target_step_id in s.dependencies:
                deps = [d for d in s.dependencies if d != action.target_step_id]
                deps.extend(removed_deps)
                s.dependencies = list(dict.fromkeys(deps))  # deduplicate, preserve order

        steps.pop(target_idx)

    elif action.action_type == "modify_field":
        target_idx = _find_step_index(steps, action.target_step_id)
        if target_idx is None or action.field_path is None:
            return
        step = steps[target_idx]
        # Only modify top-level fields on SkeletonStep
        if hasattr(step, action.field_path):
            setattr(step, action.field_path, action.field_value)


def apply_mutations(
    skeleton: TopologySkeleton,
    business_template: BusinessTemplate,
) -> Tuple[TopologySkeleton, List[str]]:
    """Apply all matching mutation rules to a skeleton.

    Returns a deep copy of the skeleton with mutations applied, plus a list of
    applied rule_ids.  The original skeleton is never modified.
    """
    mutated = skeleton.model_copy(deep=True)
    steps = list(mutated.steps)

    # Sort rules by priority descending (highest first)
    rules = sorted(mutated.mutation_rules, key=lambda r: r.priority, reverse=True)

    applied_ids: List[str] = []
    for rule in rules:
        # All conditions must match (AND logic)
        if all(evaluate_condition(c, business_template) for c in rule.conditions):
            for action in rule.actions:
                _apply_action(steps, action)
            applied_ids.append(rule.rule_id)

    mutated.steps = steps
    return mutated, applied_ids
