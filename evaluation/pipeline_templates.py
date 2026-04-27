"""Pipeline templates for the 5 client ablation scenarios.

Each template defines the DAG from the experimental design:
  Call Transcription -> Deal Summary -> (3 WH agents parallel + 2 estimators parallel)
                                     -> Client Necessity
                                     -> Compliance (after WH)
                                     -> Proposal (final, joins all branches)

The `available_services` field maps to the agent's SERVICE_NAME in docker-compose.
Tasks with the same `available_services` capability trigger CNP bidding when
multiple agents share that capability.
"""

import os
from typing import Any

# ── DAG structure (shared across all 5 clients) ───────────────────────────
# The DAG is the same for every client — only the task descriptions change.
# CNP bidding handles agent selection based on capabilities + pheromones.

_SHARED_DAG_TASKS = [
    {
        "id": "call-transcription",
        "name": "Call Transcription",
        "available_services": "call-transcription",
        "action": "call_transcription",
        "is_entry": True,
        "is_final_task": False,
        "description": None,  # filled per client
        "inputs": {"dependencies": []},
    },
    {
        "id": "deal-summary",
        "name": "Deal Summary",
        "available_services": "deal-summary",
        "action": "deal_summary",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["call-transcription"]},
    },
    {
        "id": "warehouse-match",
        "name": "Warehouse Matching",
        "available_services": "warehouse-south",
        "action": "warehouse_matching",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["deal-summary"]},
        "metadata": {"cnp_capability": "warehouse_matching"},
    },
    {
        "id": "deal-estimation",
        "name": "Deal Estimation",
        "available_services": "cost-estimator",
        "action": "deal_estimation",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["deal-summary"]},
        "metadata": {"cnp_capability": "deal_estimation"},
    },
    {
        "id": "client-necessity",
        "name": "Client Necessity Form",
        "available_services": "client-necessity-form",
        "action": "client_necessity",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["deal-summary"]},
    },
    {
        "id": "compliance-check",
        "name": "Compliance Check",
        "available_services": "compliance-check",
        "action": "compliance_check",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["warehouse-match"]},
    },
    {
        "id": "proposal-generation",
        "name": "Proposal Generation",
        "available_services": "proposal-template",
        "action": "proposal_generation",
        "is_entry": False,
        "is_final_task": True,
        "description": None,
        "inputs": {
            "dependencies": [
                "compliance-check",
                "deal-estimation",
                "client-necessity",
            ]
        },
    },
]

_COORDINATION_TASKS = [
    {
        "id": "graph-build",
        "name": "Graph Builder",
        "available_services": "graph_builder",
        "action": "graph_builder",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": []},
    },
    {
        "id": "plan-validation",
        "name": "Plan Validation",
        "available_services": "plan_validator",
        "action": "plan_validation",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["graph-build"]},
    },
    {
        "id": "plan-cache",
        "name": "Plan Cache",
        "available_services": "plan_cache",
        "action": "plan_cache",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["plan-validation"]},
    },
    {
        "id": "llm-strategist",
        "name": "LLM Strategist",
        "available_services": "llm_strategist",
        "action": "llm_strategist",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["plan-cache"]},
    },
    {
        "id": "ethics-check",
        "name": "Ethics Check",
        "available_services": "ethics_checker",
        "action": "ethics_check",
        "is_entry": False,
        "is_final_task": False,
        "description": None,
        "inputs": {"dependencies": ["llm-strategist"]},
    },
]

_ERROR_HANDLING = {
    "retry_policy": {"max_retries": 2, "backoff_strategy": "Rolling"},
    "on_failure": "Stop",
}


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _pipeline_mode() -> str:
    return (os.getenv("EVAL_PIPELINE_MODE", "full") or "full").strip().lower()


def _compliance_enabled() -> bool:
    if not _env_enabled("COMPLIANCE_AGENT_ENABLE", True):
        return False
    return not _env_enabled("SKIP_COMPLIANCE", False)


def _strategist_enabled() -> bool:
    if not _env_enabled("LLM_STRATEGIST_ENABLE", True):
        return False
    provider = os.getenv("LLM_STRATEGIST_PROVIDER")
    return provider is None or provider.strip() != ""


def _ethics_enabled() -> bool:
    if not _env_enabled("ETHICS_CHECK_ENABLE", True):
        return False
    provider = os.getenv("LLM_ETHICS_PROVIDER")
    stub_mode = _env_enabled("LLM_STUB_MODE", False)
    return stub_mode or provider is None or provider.strip() != ""


# ── CNP metadata per client per task (drives bid scoring differentiation) ──
# priority: 1=critical, 2=high, 3=medium (maps to stimulus in score_bid)
# stimulus: 0.0-1.0 (higher = more urgent, favors speed-estimator)
# reward: task value (higher for critical tasks)
# preferred_tags: match against agent tags for region/strategy affinity

CLIENT_CNP_METADATA: dict[str, dict[str, dict[str, Any]]] = {
    "freshco": {
        "warehouse-match": {
            "priority": 2, "stimulus": 0.6, "reward": 1.0,
            "preferred_tags": ["southern_europe"],
            "risk_level": "medium",
        },
        "deal-estimation": {
            "priority": 2, "stimulus": 0.4, "reward": 1.2,
            "preferred_tags": ["cost"],
            "risk_level": "low",
        },
    },
    "techparts": {
        "warehouse-match": {
            "priority": 2, "stimulus": 0.5, "reward": 1.0,
            "preferred_tags": ["central_europe"],
            "risk_level": "medium",
        },
        "deal-estimation": {
            "priority": 2, "stimulus": 0.5, "reward": 1.0,
            "preferred_tags": ["cost"],
            "risk_level": "medium",
        },
    },
    "greenleaf": {
        "warehouse-match": {
            "priority": 1, "stimulus": 0.95, "reward": 2.0,
            "preferred_tags": ["central_europe"],
            "risk_level": "high",
        },
        "deal-estimation": {
            "priority": 1, "stimulus": 0.95, "reward": 2.0,
            "preferred_tags": ["speed"],
            "risk_level": "high",
        },
    },
    "quickship": {
        "warehouse-match": {
            "priority": 3, "stimulus": 0.4, "reward": 1.0,
            "preferred_tags": ["southern_europe"],
            "risk_level": "medium",
        },
        "deal-estimation": {
            "priority": 3, "stimulus": 0.3, "reward": 1.0,
            "preferred_tags": ["cost"],
            "risk_level": "low",
        },
    },
    "nordicsteel": {
        "warehouse-match": {
            "priority": 2, "stimulus": 0.5, "reward": 1.0,
            "preferred_tags": ["northern_europe"],
            "risk_level": "medium",
        },
        "deal-estimation": {
            "priority": 2, "stimulus": 0.6, "reward": 1.5,
            "preferred_tags": ["speed"],
            "risk_level": "medium",
        },
    },
}


# ── Client-specific task descriptions ──────────────────────────────────────

CLIENT_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "freshco": {
        "call-transcription": (
            "Transcribe and extract key information from FreshCo Logistics call records. "
            "Client: Maria Rossi. Key needs: cold storage -25C, 12 refrigerated truck docks, "
            "budget 18K/month, 3-year lease with extension option."
        ),
        "deal-summary": (
            "Summarize the FreshCo Milan Expansion deal. Stage: negotiation. "
            "Key context: cold storage in Milan, budget approved by CFO at EUR 18K/month, "
            "LogisPark Milano Nord is primary candidate."
        ),
        "warehouse-match": (
            "Find cold-storage warehouse in Milan metropolitan area for FreshCo Logistics. "
            "Requirements: 15,000-25,000 sqft, 24ft ceiling, 6 dock doors, cold storage required, "
            "food-grade flooring, temperature monitoring. Budget: EUR 18,000/month. Priority: high."
        ),
        "deal-estimation": (
            "Estimate total cost of occupancy for FreshCo warehouse options. "
            "Client budget is firm at EUR 18,000/month. Lease term: 36 months. "
            "Include fit-out costs for food-grade flooring and temperature monitoring."
        ),
        "client-necessity": (
            "Pre-fill client necessity form for FreshCo Logistics. "
            "Industry: Food & Beverage. Contact: Maria Rossi. "
            "Key requirements: cold storage, food-grade flooring, Milan area."
        ),
        "compliance-check": (
            "Validate warehouse-client pairing for FreshCo Logistics. "
            "Check cold storage requirement, budget compliance, and space adequacy."
        ),
        "proposal-generation": (
            "Generate client proposal for FreshCo Logistics warehouse deal. "
            "Include recommended warehouse, financial terms, fit-out timeline, and next steps."
        ),
    },
    "techparts": {
        "call-transcription": (
            "Transcribe and extract key information from TechParts GmbH call records. "
            "Client: Hans Mueller. Key needs: ESD flooring IEC 61340-5-1 Cat 2, "
            "ISO Class 7 clean room, heavy power 400A. Must be operational by Sept 1."
        ),
        "deal-summary": (
            "Summarize the TechParts Munich Relocation deal. Stage: qualification. "
            "Key context: current lease expires Sept, exploring Bavaria Hub and Stuttgart TechCenter, "
            "willing to invest in fit-out for 5+ year lease."
        ),
        "warehouse-match": (
            "Find ESD-compliant warehouse in Munich or Stuttgart region for TechParts GmbH. "
            "Requirements: 30,000-50,000 sqft, 30ft ceiling, 10 dock doors, ESD flooring, "
            "clean room section 2000 sqft, heavy power 400A. Budget: EUR 35,000/month. Priority: high."
        ),
        "deal-estimation": (
            "Estimate total cost of occupancy for TechParts warehouse options. "
            "Include clean room retrofit costs, ESD flooring, and heavy power installation. "
            "Lease term: 60 months. Budget: EUR 35,000/month."
        ),
        "client-necessity": (
            "Pre-fill client necessity form for TechParts GmbH. "
            "Industry: Electronics Manufacturing. Contact: Hans Mueller. "
            "Key requirements: ESD flooring, clean room, Munich/Stuttgart."
        ),
        "compliance-check": (
            "Validate warehouse-client pairing for TechParts GmbH. "
            "Check ESD compliance, space adequacy, and budget compliance."
        ),
        "proposal-generation": (
            "Generate client proposal for TechParts GmbH warehouse relocation. "
            "Include recommended warehouse, ESD/clean room specs, financial terms, and timeline."
        ),
    },
    "greenleaf": {
        "call-transcription": (
            "Transcribe and extract key information from GreenLeaf Pharma call records. "
            "Client: Sophie Dupont. CRITICAL: GDP compliance mandatory, May 15 audit deadline, "
            "hazmat storage, backup power <10s switchover. Budget: EUR 28,000/month."
        ),
        "deal-summary": (
            "Summarize the GreenLeaf GDP Warehouse deal. Stage: proposal. CRITICAL priority. "
            "Key context: GDP compliance non-negotiable, May 15 audit, PharmaStore Lyon primary, "
            "Paris CDG backup. Timeline is very tight."
        ),
        "warehouse-match": (
            "Find GDP-compliant hazmat warehouse near Paris or Lyon for GreenLeaf Pharma. "
            "Requirements: 20,000-35,000 sqft, 20ft ceiling, 4 dock doors, GDP-compliant cold chain, "
            "hazmat storage, 24/7 security, backup power. Budget: EUR 28,000/month. "
            "Priority: CRITICAL. May 15 audit deadline."
        ),
        "deal-estimation": (
            "Estimate timeline-to-operational for GreenLeaf warehouse options. "
            "CRITICAL: Must be GDP-certified before May 15 audit. "
            "Include GDP certification timeline, fit-out, and compliance milestones."
        ),
        "client-necessity": (
            "Pre-fill client necessity form for GreenLeaf Pharma. "
            "Industry: Pharmaceuticals. Contact: Sophie Dupont. "
            "Key requirements: GDP cold chain, hazmat, 24/7 security, Paris/Lyon."
        ),
        "compliance-check": (
            "Validate warehouse-client pairing for GreenLeaf Pharma. "
            "CRITICAL: Check hazmat certification, cold storage, security level for critical deal, "
            "budget compliance. This is a critical-priority deal requiring human approval."
        ),
        "proposal-generation": (
            "Generate client proposal for GreenLeaf Pharma GDP warehouse. "
            "CRITICAL priority. Include dual-track strategy (primary + backup), "
            "GDP certification timeline, and risk mitigation plan."
        ),
    },
    "quickship": {
        "call-transcription": (
            "Transcribe and extract key information from QuickShip Express call records. "
            "Client: Luca Bianchi. Key needs: 5000 orders/day peak, mezzanine for packing, "
            "high-speed internet, office space 3000 sqft. Bologna-Rome corridor."
        ),
        "deal-summary": (
            "Summarize the QuickShip Fulfillment Center deal. Stage: prospecting. "
            "Key context: new e-commerce client, looking for high-throughput space "
            "with mezzanine capability, Bologna or Rome corridor."
        ),
        "warehouse-match": (
            "Find high-throughput fulfillment warehouse in Rome or Bologna corridor for QuickShip Express. "
            "Requirements: 40,000-60,000 sqft, 32ft ceiling, 12 dock doors, "
            "mezzanine capability, high-speed internet, office space 3000 sqft. "
            "Budget: EUR 40,000/month. Priority: medium."
        ),
        "deal-estimation": (
            "Estimate total cost of occupancy for QuickShip warehouse options. "
            "Include mezzanine installation costs, internet infrastructure, and office fit-out. "
            "Lease term: 24 months. Budget: EUR 40,000/month."
        ),
        "client-necessity": (
            "Pre-fill client necessity form for QuickShip Express. "
            "Industry: E-commerce Fulfillment. Contact: Luca Bianchi. "
            "Key requirements: mezzanine, high throughput, Bologna/Rome."
        ),
        "compliance-check": (
            "Validate warehouse-client pairing for QuickShip Express. "
            "Check space adequacy, dock door count, and budget compliance."
        ),
        "proposal-generation": (
            "Generate client proposal for QuickShip Express fulfillment center. "
            "Include recommended warehouse, mezzanine specs, and e-commerce throughput analysis."
        ),
    },
    "nordicsteel": {
        "call-transcription": (
            "Transcribe and extract key information from Nordic Steel AB call records. "
            "Client: Erik Lindgren. Key needs: 10-ton overhead crane (15-ton preferred), "
            "reinforced flooring, rail siding standard gauge 1435mm, 24/7 access."
        ),
        "deal-summary": (
            "Summarize the Nordic Steel Port Facility deal. Stage: negotiation. "
            "Key context: need crane and rail access at Gothenburg port, "
            "port authority confirmed 8-ton crane upgradeable to 15-ton in 8 weeks."
        ),
        "warehouse-match": (
            "Find port warehouse in Gothenburg area for Nordic Steel AB. "
            "Requirements: 50,000-80,000 sqft, 40ft ceiling, 8 dock doors, "
            "overhead crane 10 ton minimum, reinforced flooring, rail siding access. "
            "Budget: EUR 55,000/month. Priority: high."
        ),
        "deal-estimation": (
            "Estimate total cost of occupancy for Nordic Steel warehouse options. "
            "Include crane upgrade costs, reinforced flooring, and rail siding installation. "
            "Lease term: 60 months. Budget: EUR 55,000/month."
        ),
        "client-necessity": (
            "Pre-fill client necessity form for Nordic Steel AB. "
            "Industry: Heavy Industry. Contact: Erik Lindgren. "
            "Key requirements: overhead crane, rail siding, Gothenburg port."
        ),
        "compliance-check": (
            "Validate warehouse-client pairing for Nordic Steel AB. "
            "Check crane capacity, space adequacy, and budget compliance."
        ),
        "proposal-generation": (
            "Generate client proposal for Nordic Steel AB port facility. "
            "Include crane upgrade timeline, rail access details, and port authority terms."
        ),
    },
}


def build_pipeline_payload(client_key: str) -> dict[str, Any]:
    """Build a complete PipelineCreate payload for a given client scenario.

    Args:
        client_key: One of 'freshco', 'techparts', 'greenleaf', 'quickship', 'nordicsteel'

    Returns:
        dict matching the PipelineCreate schema, ready for POST /pipelines/{username}
    """
    if client_key not in CLIENT_DESCRIPTIONS:
        raise ValueError(f"Unknown client: {client_key}. Valid: {list(CLIENT_DESCRIPTIONS)}")

    descriptions = CLIENT_DESCRIPTIONS[client_key]
    client_name = {
        "freshco": "FreshCo Logistics",
        "techparts": "TechParts GmbH",
        "greenleaf": "GreenLeaf Pharma",
        "quickship": "QuickShip Express",
        "nordicsteel": "Nordic Steel AB",
    }[client_key]

    compliance_enabled = _compliance_enabled()
    strategist_enabled = _strategist_enabled()
    ethics_enabled = _ethics_enabled()
    pipeline_mode = _pipeline_mode()

    business_templates = [
        task for task in _SHARED_DAG_TASKS
        if compliance_enabled or task["id"] != "compliance-check"
    ]
    coordination_templates = []
    if pipeline_mode != "minimal":
        for task in _COORDINATION_TASKS:
            if not strategist_enabled and task["id"] == "llm-strategist":
                continue
            if not ethics_enabled and task["id"] == "ethics-check":
                continue
            coordination_templates.append(task)

    active_templates = business_templates + coordination_templates

    tasks = []
    cnp_meta = CLIENT_CNP_METADATA.get(client_key, {})
    for task_template in active_templates:
        task = dict(task_template)
        task["inputs"] = {"dependencies": list(task_template.get("inputs", {}).get("dependencies", []))}
        if task["id"] in descriptions:
            base_desc = descriptions[task["id"]]
        else:
            base_desc = {
                "graph-build": f"Build a run-scoped plan draft for {client_name} from the active brokerage workflow and selected agents.",
                "plan-validation": f"Validate the {client_name} plan draft with structural and SHACL checks, preserving detailed validation evidence.",
                "plan-cache": f"Cache the validated {client_name} plan draft for downstream strategy and audit steps.",
                "llm-strategist": f"Produce an execution strategy for the {client_name} brokerage plan, identifying phases and critical path.",
                "ethics-check": f"Review the {client_name} brokerage plan for policy and safety issues before final proposal generation.",
            }[task["id"]]
        # Embed CNP metadata as a JSON prefix in the description.
        # The forwarder's _resolve_competing_service() parses this back out.
        # This survives the pipeline config parser (description is preserved in task_dict).
        task_cnp = cnp_meta.get(task["id"], {})
        if task_cnp:
            import json as _json
            task["description"] = f"[CNP:{_json.dumps(task_cnp)}] {base_desc}"
        else:
            task["description"] = base_desc
        tasks.append(task)

    if not compliance_enabled:
        for task in tasks:
            if task["id"] == "proposal-generation":
                task["inputs"] = {
                    "dependencies": [
                        "warehouse-match",
                        "deal-estimation",
                        "client-necessity",
                    ]
                }
                break

    # ── Strategist-driven DAG optimization ──────────────────────────────
    # When the strategist is enabled, critical/high priority clients get
    # sequential estimation: deal-estimation waits for warehouse-match
    # output, giving the estimator richer context (which warehouse was
    # selected) at the cost of higher latency.
    #
    # When strategist is disabled (C5), estimation runs in parallel with
    # warehouse matching — faster but the estimator works without knowing
    # which warehouse was suggested.
    #
    # This produces measurable H4 differences:
    #   - LAT: C0 critical clients take longer (sequential)
    #   - ACC: C0 estimations may reference the selected warehouse
    #   - TOK: C0 critical estimations include warehouse context
    if strategist_enabled:
        client_cnp = CLIENT_CNP_METADATA.get(client_key, {})
        wh_meta = client_cnp.get("warehouse-match", {})
        client_priority = wh_meta.get("priority", 3)
        # Priority 1 (critical) or 2 (high) → sequential estimation
        if client_priority <= 2:
            for task in tasks:
                if task["id"] == "deal-estimation":
                    deps = list(task["inputs"].get("dependencies", []))
                    if "warehouse-match" not in deps:
                        deps.append("warehouse-match")
                        task["inputs"] = {"dependencies": deps}
                    break

    if pipeline_mode != "minimal":
        graph_build_dependencies = [
            "warehouse-match",
            "deal-estimation",
            "client-necessity",
        ]
        if compliance_enabled:
            graph_build_dependencies.append("compliance-check")

        coordination_tail = "plan-cache"
        if strategist_enabled and ethics_enabled:
            coordination_tail = "ethics-check"
        elif strategist_enabled:
            coordination_tail = "llm-strategist"
        elif ethics_enabled:
            coordination_tail = "ethics-check"

        for task in tasks:
            if task["id"] == "graph-build":
                task["inputs"] = {"dependencies": graph_build_dependencies}
            elif task["id"] == "ethics-check" and not strategist_enabled:
                task["inputs"] = {"dependencies": ["plan-cache"]}
            elif task["id"] == "proposal-generation":
                deps = list(task["inputs"]["dependencies"])
                if coordination_tail not in deps:
                    deps.append(coordination_tail)
                task["inputs"] = {"dependencies": deps}

    if pipeline_mode == "minimal":
        ordered_ids = [task["id"] for task in tasks]
        previous_id = ""
        for task in tasks:
            if task["id"] == ordered_ids[0]:
                task["is_entry"] = True
                task["inputs"] = {"dependencies": []}
            else:
                task["is_entry"] = False
                task["inputs"] = {"dependencies": [previous_id]}
            previous_id = task["id"]
        for task in tasks:
            task["is_final_task"] = task["id"] == ordered_ids[-1]

    return {
        "authors": ["evaluation"],
        "vhost": "/",
        "name": f"ablation-{client_key}",
        "description": f"Ablation study pipeline for {client_name}",
        "pipeline_name": f"ablation-{client_key}",
        "dag": {"tasks": tasks},
        "error_handling": _ERROR_HANDLING,
    }


# Convenience: all 5 payloads
ALL_CLIENT_KEYS = list(CLIENT_DESCRIPTIONS.keys())


if __name__ == "__main__":
    import json
    for key in ALL_CLIENT_KEYS:
        payload = build_pipeline_payload(key)
        print(f"\n{'='*60}")
        print(f"Pipeline: {payload['name']}")
        print(f"{'='*60}")
        print(json.dumps(payload, indent=2))
