"""Tests for the topology mutation engine: condition evaluation, rule application, dependency rewiring."""
import pytest
from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    MutationAction,
    MutationCondition,
    MutationRule,
    SkeletonStep,
    TopologySkeleton,
)
from src.agentcy.cognitive.topology.mutation import (
    apply_mutations,
    evaluate_condition,
)


def _make_skeleton(steps, rules=None) -> TopologySkeleton:
    return TopologySkeleton(
        name="test", workflow_class="test",
        steps=steps, mutation_rules=rules or [],
    )


# ── Condition evaluation ─────────────────────────────────────────────────


class TestEvaluateCondition:
    def test_eq_true(self):
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        assert evaluate_condition(
            MutationCondition(field="compliance_strictness", operator="eq", value="strict"), bt
        ) is True

    def test_eq_false(self):
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="none")
        assert evaluate_condition(
            MutationCondition(field="compliance_strictness", operator="eq", value="strict"), bt
        ) is False

    def test_neq(self):
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        assert evaluate_condition(
            MutationCondition(field="compliance_strictness", operator="neq", value="none"), bt
        ) is True

    def test_in_list_true(self):
        bt = BusinessTemplate(workflow_class="x", integration_types=["email", "tms"])
        assert evaluate_condition(
            MutationCondition(field="integration_types", operator="in", value="email"), bt
        ) is True

    def test_in_list_false(self):
        bt = BusinessTemplate(workflow_class="x", integration_types=["tms"])
        assert evaluate_condition(
            MutationCondition(field="integration_types", operator="in", value="email"), bt
        ) is False

    def test_in_empty_list(self):
        bt = BusinessTemplate(workflow_class="x", integration_types=[])
        assert evaluate_condition(
            MutationCondition(field="integration_types", operator="in", value="email"), bt
        ) is False

    def test_gte(self):
        bt = BusinessTemplate(workflow_class="x", volume_per_day=500)
        assert evaluate_condition(
            MutationCondition(field="volume_per_day", operator="gte", value=100), bt
        ) is True
        assert evaluate_condition(
            MutationCondition(field="volume_per_day", operator="gte", value=1000), bt
        ) is False

    def test_lte(self):
        bt = BusinessTemplate(workflow_class="x", volume_per_day=50)
        assert evaluate_condition(
            MutationCondition(field="volume_per_day", operator="lte", value=100), bt
        ) is True

    def test_nonexistent_field(self):
        bt = BusinessTemplate(workflow_class="x")
        assert evaluate_condition(
            MutationCondition(field="nonexistent_field", operator="eq", value="x"), bt
        ) is False

    def test_boolean_eq(self):
        bt = BusinessTemplate(workflow_class="x", human_approval_required=True)
        assert evaluate_condition(
            MutationCondition(field="human_approval_required", operator="eq", value=True), bt
        ) is True

    def test_gte_with_non_numeric(self):
        bt = BusinessTemplate(workflow_class="x")
        assert evaluate_condition(
            MutationCondition(field="workflow_class", operator="gte", value=5), bt
        ) is False


# ── insert_after ─────────────────────────────────────────────────────────


class TestInsertAfter:
    def test_basic_insert_after(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="insert_c",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="c", role="verify", name="C"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        mutated, applied = apply_mutations(sk, bt)

        assert len(mutated.steps) == 3
        ids = [s.step_id for s in mutated.steps]
        assert ids == ["a", "c", "b"]
        # C depends on A
        assert "a" in mutated.steps[1].dependencies
        # B now depends on C (rewired from A)
        assert "c" in mutated.steps[2].dependencies
        assert "a" not in mutated.steps[2].dependencies

    def test_insert_after_with_multiple_dependents(self):
        """If A has two dependents B and D, inserting C after A should rewire both."""
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"]),
            SkeletonStep(step_id="d", role="notify", name="D", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="insert_c",
            conditions=[MutationCondition(field="human_approval_required", operator="eq", value=True)],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="c", role="approve", name="C"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", human_approval_required=True)
        mutated, _ = apply_mutations(sk, bt)

        c_step = next(s for s in mutated.steps if s.step_id == "c")
        b_step = next(s for s in mutated.steps if s.step_id == "b")
        d_step = next(s for s in mutated.steps if s.step_id == "d")

        assert "a" in c_step.dependencies
        assert "c" in b_step.dependencies and "a" not in b_step.dependencies
        assert "c" in d_step.dependencies and "a" not in d_step.dependencies

    def test_insert_after_final_step(self):
        """Inserting after the final step should work."""
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="notify", name="B", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="add_email",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="b",
                step=SkeletonStep(step_id="email", role="notify", name="Email", is_final=True),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        mutated, _ = apply_mutations(sk, bt)

        email = next(s for s in mutated.steps if s.step_id == "email")
        assert "b" in email.dependencies


# ── insert_before ────────────────────────────────────────────────────────


class TestInsertBefore:
    def test_basic_insert_before(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="insert_c_before_b",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_before", target_step_id="b",
                step=SkeletonStep(step_id="c", role="verify", name="C"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        mutated, _ = apply_mutations(sk, bt)

        ids = [s.step_id for s in mutated.steps]
        assert ids == ["a", "c", "b"]
        # C inherits B's old dependencies
        c = next(s for s in mutated.steps if s.step_id == "c")
        assert "a" in c.dependencies
        # B now depends on C
        b = next(s for s in mutated.steps if s.step_id == "b")
        assert b.dependencies == ["c"]

    def test_insert_before_entry_step(self):
        """Inserting before the entry step — new step becomes entry-like in position."""
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="prepend",
            conditions=[MutationCondition(field="human_approval_required", operator="eq", value=True)],
            actions=[MutationAction(
                action_type="insert_before", target_step_id="a",
                step=SkeletonStep(step_id="pre", role="approve", name="Pre"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", human_approval_required=True)
        mutated, _ = apply_mutations(sk, bt)

        pre = next(s for s in mutated.steps if s.step_id == "pre")
        a = next(s for s in mutated.steps if s.step_id == "a")
        assert pre.dependencies == []  # A had no deps, so pre inherits none
        assert a.dependencies == ["pre"]


# ── remove ───────────────────────────────────────────────────────────────


class TestRemove:
    def test_remove_middle_step(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="verify", name="B", dependencies=["a"]),
            SkeletonStep(step_id="c", role="execute", name="C", dependencies=["b"], is_final=True),
        ]
        rule = MutationRule(
            name="remove_b",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="none")],
            actions=[MutationAction(action_type="remove", target_step_id="b")],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="none")
        mutated, _ = apply_mutations(sk, bt)

        assert len(mutated.steps) == 2
        c = next(s for s in mutated.steps if s.step_id == "c")
        # C should now depend on A (B's old dependency)
        assert "a" in c.dependencies
        assert "b" not in c.dependencies

    def test_remove_step_with_multiple_dependents(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="classify", name="B", dependencies=["a"]),
            SkeletonStep(step_id="c", role="execute", name="C", dependencies=["b"]),
            SkeletonStep(step_id="d", role="notify", name="D", dependencies=["b"], is_final=True),
        ]
        rule = MutationRule(
            name="remove_b",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="none")],
            actions=[MutationAction(action_type="remove", target_step_id="b")],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="none")
        mutated, _ = apply_mutations(sk, bt)

        c = next(s for s in mutated.steps if s.step_id == "c")
        d = next(s for s in mutated.steps if s.step_id == "d")
        assert "a" in c.dependencies
        assert "a" in d.dependencies

    def test_remove_nonexistent_step(self):
        """Removing a step that doesn't exist should be a no-op."""
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True, is_final=True),
        ]
        rule = MutationRule(
            name="remove_ghost",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(action_type="remove", target_step_id="nonexistent")],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        mutated, applied = apply_mutations(sk, bt)
        assert len(mutated.steps) == 1
        assert len(applied) == 1  # Rule still counted as applied


# ── modify_field ─────────────────────────────────────────────────────────


class TestModifyField:
    def test_modify_capabilities(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True, is_final=True,
                         required_capabilities=["data_read"]),
        ]
        rule = MutationRule(
            name="enhance",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="modify_field", target_step_id="a",
                field_path="required_capabilities", field_value=["data_read", "validate"],
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        mutated, _ = apply_mutations(sk, bt)
        assert mutated.steps[0].required_capabilities == ["data_read", "validate"]


# ── Multiple rules + priority ────────────────────────────────────────────


class TestMultipleRules:
    def test_priority_ordering(self):
        """Higher priority rules fire first."""
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"], is_final=True),
        ]
        rule_low = MutationRule(
            rule_id="low", name="low_pri", priority=10,
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="low_step", role="verify", name="Low"),
            )],
        )
        rule_high = MutationRule(
            rule_id="high", name="high_pri", priority=100,
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="high_step", role="verify", name="High"),
            )],
        )
        sk = _make_skeleton(steps, [rule_low, rule_high])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")
        _, applied = apply_mutations(sk, bt)
        # High priority should fire first
        assert applied[0] == "high"
        assert applied[1] == "low"

    def test_condition_not_met_skips_rule(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True, is_final=True),
        ]
        rule = MutationRule(
            name="skip_me",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="x", role="verify", name="X"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="none")
        mutated, applied = apply_mutations(sk, bt)
        assert len(mutated.steps) == 1
        assert len(applied) == 0


# ── Deep copy guarantee ──────────────────────────────────────────────────


class TestDeepCopy:
    def test_original_unchanged(self):
        steps = [
            SkeletonStep(step_id="a", role="intake", name="A", is_entry=True),
            SkeletonStep(step_id="b", role="execute", name="B", dependencies=["a"], is_final=True),
        ]
        rule = MutationRule(
            name="insert",
            conditions=[MutationCondition(field="compliance_strictness", operator="eq", value="strict")],
            actions=[MutationAction(
                action_type="insert_after", target_step_id="a",
                step=SkeletonStep(step_id="c", role="verify", name="C"),
            )],
        )
        sk = _make_skeleton(steps, [rule])
        bt = BusinessTemplate(workflow_class="x", compliance_strictness="strict")

        original_count = len(sk.steps)
        mutated, _ = apply_mutations(sk, bt)

        assert len(sk.steps) == original_count
        assert len(mutated.steps) == original_count + 1
