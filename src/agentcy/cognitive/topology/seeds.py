"""Pre-built logistics topology skeletons with embedded mutation rules.

Each skeleton is a canonical DAG pattern for a common logistics workflow.
Mutation rules adapt the skeleton based on business template constraints
(compliance, human approval, integration types, criticality).
"""
from __future__ import annotations

from typing import List

from agentcy.pydantic_models.topology_models import (
    MutationAction,
    MutationCondition,
    MutationRule,
    SkeletonStep,
    TopologySkeleton,
)


# ── Shared mutation rules ────────────────────────────────────────────────

def _compliance_gate_rule(after_step_id: str) -> MutationRule:
    """Insert a compliance verification step after the target when compliance is strict."""
    return MutationRule(
        rule_id="rule_compliance_gate",
        name="Insert compliance verification gate",
        priority=100,
        conditions=[
            MutationCondition(field="compliance_strictness", operator="eq", value="strict"),
        ],
        actions=[
            MutationAction(
                action_type="insert_after",
                target_step_id=after_step_id,
                step=SkeletonStep(
                    step_id="compliance_verify",
                    role="verify",
                    name="Compliance Verification",
                    description="Verify decision against compliance rules and regulations.",
                    required_capabilities=["validate", "data_read"],
                    required_tags=["compliance", "verification"],
                    coordination_mode="coalition_allowed",
                ),
            ),
        ],
    )


def _human_approval_rule(after_step_id: str) -> MutationRule:
    """Insert a human approval gate after the target when human approval is required."""
    return MutationRule(
        rule_id="rule_human_approval",
        name="Insert human approval gate",
        priority=90,
        conditions=[
            MutationCondition(field="human_approval_required", operator="eq", value=True),
        ],
        actions=[
            MutationAction(
                action_type="insert_after",
                target_step_id=after_step_id,
                step=SkeletonStep(
                    step_id="human_approve",
                    role="approve",
                    name="Human Approval",
                    description="Route to human operator for review and approval.",
                    required_capabilities=["human_review"],
                    required_tags=["approval", "human_in_the_loop"],
                ),
            ),
        ],
    )


def _email_notify_rule(final_step_id: str) -> MutationRule:
    """Append an email notification step when email integration is specified."""
    return MutationRule(
        rule_id="rule_email_notify",
        name="Append email notification",
        priority=50,
        conditions=[
            MutationCondition(field="integration_types", operator="in", value="email"),
        ],
        actions=[
            MutationAction(
                action_type="insert_after",
                target_step_id=final_step_id,
                step=SkeletonStep(
                    step_id="email_notify",
                    role="notify",
                    name="Email Notification",
                    description="Send status notification via email.",
                    required_capabilities=["integration", "notification"],
                    required_tags=["email", "notification"],
                    is_final=True,
                ),
            ),
            # Unmark the previous final step
            MutationAction(
                action_type="modify_field",
                target_step_id=final_step_id,
                field_path="is_final",
                field_value=False,
            ),
        ],
    )


def _carrier_api_adapter_rule(before_step_id: str) -> MutationRule:
    """Insert a carrier API adapter before the target when carrier_api integration is needed."""
    return MutationRule(
        rule_id="rule_carrier_adapter",
        name="Insert carrier API adapter",
        priority=70,
        conditions=[
            MutationCondition(field="integration_types", operator="in", value="carrier_api"),
        ],
        actions=[
            MutationAction(
                action_type="insert_before",
                target_step_id=before_step_id,
                step=SkeletonStep(
                    step_id="carrier_adapter",
                    role="integrate",
                    name="Carrier API Adapter",
                    description="Connect to carrier API for rates, booking, or tracking.",
                    required_capabilities=["http_request", "api_call"],
                    required_tags=["carrier", "integration"],
                ),
            ),
        ],
    )


# ── Skeleton 1: Shipment Exception Handling ──────────────────────────────


def _shipment_exception_skeleton() -> TopologySkeleton:
    return TopologySkeleton(
        skeleton_id="skel_shipment_exception_v1",
        name="Shipment Exception Handling",
        workflow_class="shipment_exception",
        description=(
            "Handles shipment delays, damage, or routing issues. "
            "Intake → classify cause → decide action → execute → verify → notify."
        ),
        steps=[
            SkeletonStep(
                step_id="intake",
                role="intake",
                name="Exception Intake",
                description="Receive and parse shipment exception alert from TMS or carrier.",
                required_capabilities=["data_read", "parse"],
                is_entry=True,
            ),
            SkeletonStep(
                step_id="classify",
                role="classify",
                name="Classify Exception",
                description="Classify exception type: delay, damage, misroute, customs hold, etc.",
                required_capabilities=["processing", "ml_inference"],
                required_tags=["classification"],
                dependencies=["intake"],
            ),
            SkeletonStep(
                step_id="decide",
                role="decide",
                name="Decide Action",
                description="Determine corrective action: reroute, escalate, notify customer, file claim.",
                required_capabilities=["processing", "validate"],
                required_tags=["decision"],
                dependencies=["classify"],
                coordination_mode="coalition_allowed",
            ),
            SkeletonStep(
                step_id="execute",
                role="execute",
                name="Execute Action",
                description="Execute the decided corrective action (API calls, updates, etc.).",
                required_capabilities=["http_request", "data_write"],
                required_tags=["execution"],
                dependencies=["decide"],
            ),
            SkeletonStep(
                step_id="verify",
                role="verify",
                name="Verify Resolution",
                description="Verify the exception has been resolved or escalate further.",
                required_capabilities=["validate", "data_read"],
                required_tags=["verification"],
                dependencies=["execute"],
            ),
            SkeletonStep(
                step_id="notify",
                role="notify",
                name="Notify Stakeholders",
                description="Send resolution notification to relevant stakeholders.",
                required_capabilities=["notification"],
                required_tags=["notification"],
                dependencies=["verify"],
                is_final=True,
            ),
        ],
        mutation_rules=[
            _compliance_gate_rule("decide"),
            _human_approval_rule("decide"),
            _email_notify_rule("notify"),
        ],
        control_patterns=["verification_gate", "retry_wrapper"],
    )


# ── Skeleton 2: Order Fulfillment ────────────────────────────────────────


def _order_fulfillment_skeleton() -> TopologySkeleton:
    return TopologySkeleton(
        skeleton_id="skel_order_fulfillment_v1",
        name="Order Fulfillment",
        workflow_class="order_fulfillment",
        description=(
            "End-to-end order processing: intake → validate → check inventory → "
            "allocate → pick & pack → ship → confirm."
        ),
        steps=[
            SkeletonStep(
                step_id="intake",
                role="intake",
                name="Order Intake",
                description="Receive and parse incoming order from ERP or e-commerce platform.",
                required_capabilities=["data_read", "parse"],
                is_entry=True,
            ),
            SkeletonStep(
                step_id="validate",
                role="verify",
                name="Validate Order",
                description="Validate order completeness, pricing, and customer eligibility.",
                required_capabilities=["validate", "data_read"],
                required_tags=["validation"],
                dependencies=["intake"],
            ),
            SkeletonStep(
                step_id="check_inventory",
                role="integrate",
                name="Check Inventory",
                description="Query WMS for stock availability at applicable warehouses.",
                required_capabilities=["db_read", "api_call"],
                required_tags=["inventory", "wms"],
                dependencies=["validate"],
            ),
            SkeletonStep(
                step_id="allocate",
                role="decide",
                name="Allocate Warehouse",
                description="Select optimal warehouse and allocate inventory.",
                required_capabilities=["processing", "validate"],
                required_tags=["allocation", "decision"],
                dependencies=["check_inventory"],
                coordination_mode="coalition_allowed",
            ),
            SkeletonStep(
                step_id="pick_pack",
                role="execute",
                name="Pick & Pack",
                description="Trigger pick and pack workflow at allocated warehouse.",
                required_capabilities=["data_write", "api_call"],
                required_tags=["warehouse", "fulfillment"],
                dependencies=["allocate"],
            ),
            SkeletonStep(
                step_id="ship",
                role="execute",
                name="Ship Order",
                description="Book carrier and generate shipping label.",
                required_capabilities=["http_request", "data_write"],
                required_tags=["shipping", "carrier"],
                dependencies=["pick_pack"],
            ),
            SkeletonStep(
                step_id="confirm",
                role="notify",
                name="Confirm Delivery",
                description="Send order confirmation and tracking information to customer.",
                required_capabilities=["notification", "data_write"],
                required_tags=["confirmation", "notification"],
                dependencies=["ship"],
                is_final=True,
            ),
        ],
        mutation_rules=[
            _human_approval_rule("allocate"),
            _compliance_gate_rule("allocate"),
            _email_notify_rule("confirm"),
            MutationRule(
                rule_id="rule_wms_adapter",
                name="Insert WMS adapter for inventory check",
                priority=70,
                conditions=[
                    MutationCondition(field="integration_types", operator="in", value="wms"),
                ],
                actions=[
                    MutationAction(
                        action_type="modify_field",
                        target_step_id="check_inventory",
                        field_path="required_capabilities",
                        field_value=["db_read", "api_call", "http_request"],
                    ),
                ],
            ),
        ],
        control_patterns=["verification_gate", "fan_out_fan_in"],
    )


# ── Skeleton 3: Carrier Selection & Rate Optimization ────────────────────


def _carrier_selection_skeleton() -> TopologySkeleton:
    return TopologySkeleton(
        skeleton_id="skel_carrier_selection_v1",
        name="Carrier Selection & Rate Optimization",
        workflow_class="carrier_selection",
        description=(
            "Compare carrier rates and select optimal carrier for a shipment: "
            "intake → collect rates → score → select → book."
        ),
        steps=[
            SkeletonStep(
                step_id="intake",
                role="intake",
                name="Shipment Specs Intake",
                description="Receive shipment specifications: dimensions, weight, origin, destination, SLA.",
                required_capabilities=["data_read", "parse"],
                is_entry=True,
            ),
            SkeletonStep(
                step_id="collect_rates",
                role="integrate",
                name="Collect Carrier Rates",
                description="Query multiple carrier APIs for rate quotes.",
                required_capabilities=["http_request", "api_call"],
                required_tags=["carrier", "rates"],
                dependencies=["intake"],
            ),
            SkeletonStep(
                step_id="score_carriers",
                role="decide",
                name="Score Carriers",
                description="Score and rank carriers by cost, reliability, transit time, and SLA fit.",
                required_capabilities=["processing", "statistics"],
                required_tags=["scoring", "optimization"],
                dependencies=["collect_rates"],
                coordination_mode="coalition_allowed",
            ),
            SkeletonStep(
                step_id="select",
                role="decide",
                name="Select Carrier",
                description="Select the best carrier based on scoring and business rules.",
                required_capabilities=["processing", "validate"],
                required_tags=["selection", "decision"],
                dependencies=["score_carriers"],
            ),
            SkeletonStep(
                step_id="book",
                role="execute",
                name="Book Shipment",
                description="Book the shipment with the selected carrier and generate tracking.",
                required_capabilities=["http_request", "data_write"],
                required_tags=["booking", "carrier"],
                dependencies=["select"],
                is_final=True,
            ),
        ],
        mutation_rules=[
            _compliance_gate_rule("score_carriers"),
            _carrier_api_adapter_rule("collect_rates"),
            _email_notify_rule("book"),
        ],
        control_patterns=["retry_wrapper"],
    )


# ── Skeleton 4: Customs & Compliance Review ──────────────────────────────


def _customs_compliance_skeleton() -> TopologySkeleton:
    return TopologySkeleton(
        skeleton_id="skel_customs_compliance_v1",
        name="Customs & Compliance Review",
        workflow_class="customs_compliance",
        description=(
            "Review shipment for customs compliance: extract docs → classify goods → "
            "compliance check → approve → submit declarations."
        ),
        steps=[
            SkeletonStep(
                step_id="intake",
                role="intake",
                name="Document Intake",
                description="Receive shipment documentation: invoices, packing lists, certificates.",
                required_capabilities=["data_read", "parse"],
                is_entry=True,
            ),
            SkeletonStep(
                step_id="extract_docs",
                role="classify",
                name="Extract Document Data",
                description="Extract structured data from shipping documents using OCR/NLP.",
                required_capabilities=["parse", "ml_inference"],
                required_tags=["extraction", "nlp"],
                dependencies=["intake"],
            ),
            SkeletonStep(
                step_id="classify_goods",
                role="classify",
                name="Classify Goods",
                description="Classify goods by HS code, determine tariff category and restrictions.",
                required_capabilities=["processing", "ml_inference"],
                required_tags=["classification", "customs"],
                dependencies=["extract_docs"],
            ),
            SkeletonStep(
                step_id="compliance_check",
                role="verify",
                name="Compliance Check",
                description="Check against trade regulations, sanctions lists, and import restrictions.",
                required_capabilities=["validate", "db_read"],
                required_tags=["compliance", "regulations"],
                dependencies=["classify_goods"],
                coordination_mode="coalition_allowed",
            ),
            SkeletonStep(
                step_id="approve",
                role="approve",
                name="Approve Declaration",
                description="Review and approve customs declaration for submission.",
                required_capabilities=["validate", "human_review"],
                required_tags=["approval"],
                dependencies=["compliance_check"],
            ),
            SkeletonStep(
                step_id="submit",
                role="execute",
                name="Submit Declaration",
                description="Submit customs declaration to relevant authority.",
                required_capabilities=["http_request", "data_write"],
                required_tags=["customs", "submission"],
                dependencies=["approve"],
                is_final=True,
            ),
        ],
        mutation_rules=[
            _compliance_gate_rule("compliance_check"),
            _human_approval_rule("compliance_check"),
            _email_notify_rule("submit"),
            MutationRule(
                rule_id="rule_customs_db_adapter",
                name="Enhance compliance check with customs DB",
                priority=70,
                conditions=[
                    MutationCondition(field="integration_types", operator="in", value="customs_db"),
                ],
                actions=[
                    MutationAction(
                        action_type="modify_field",
                        target_step_id="compliance_check",
                        field_path="required_capabilities",
                        field_value=["validate", "db_read", "http_request"],
                    ),
                ],
            ),
        ],
        control_patterns=["verification_gate", "human_approval"],
    )


# ── Public API ───────────────────────────────────────────────────────────


def get_logistics_seeds() -> List[TopologySkeleton]:
    """Return all pre-built logistics topology skeletons."""
    return [
        _shipment_exception_skeleton(),
        _order_fulfillment_skeleton(),
        _carrier_selection_skeleton(),
        _customs_compliance_skeleton(),
    ]
