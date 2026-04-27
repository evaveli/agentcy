"""Ablation configuration profiles (C0-C7 + C3+C4).

Maps to the experimental design plan Section 3.1:
  C0  Full framework (control)
  C1  No pheromone adaptation (stigmergic learning)
  C2  No CNP bidding (competitive selection → round-robin)
  C3  No SHACL validation (structural constraints)
  C4  No compliance agent (domain rules)
  C34 No SHACL + no compliance (combined validation — H3 interaction)
  C5  No LLM strategist (phase optimization)
  C6  No ethics checker (policy enforcement)
  C7  Minimal baseline (all coordination removed)
"""

from __future__ import annotations


# Base env vars shared by all configs (full system defaults)
_BASE: dict[str, str] = {
    # Ethics
    "LLM_ETHICS_PROVIDER": "openai",
    "LLM_STUB_MODE": "0",
    "EXECUTION_REQUIRE_ETHICS": "1",
    "ETHICS_ENABLE_COMPANY_RULES": "0",
    # Strategist
    "LLM_STRATEGIST_PROVIDER": "openai",
    "LLM_STRATEGIST_LOOP": "1",
    "LLM_STRATEGIST_LOOP_PROVIDER": "openai",
    # CNP
    "CNP_MANAGER_ENABLE": "1",
    "CNP_MAX_REFORWARDS": "3",
    "CNP_FAILURE_REFORWARD": "1",
    "AGENT_ASSIGNMENT_BASELINE": "direct",
    # Pheromone
    "PHEROMONE_ENABLE": "1",
    # SHACL
    "SHACL_ENABLE": "1",
    "SHACL_SHAPES_PATH": "schemas/plan_draft_shapes.ttl",
    "PLAN_SHACL_RULESET_PATH": "schemas/plan_draft_shacl.json",
    # Compliance agent (included in pipeline DAG)
    "COMPLIANCE_AGENT_ENABLE": "1",
    # Optional coordination branch
    "LLM_STRATEGIST_ENABLE": "1",
    "ETHICS_CHECK_ENABLE": "1",
    # Evaluation pipeline shape
    "EVAL_PIPELINE_MODE": "full",
    # Observability TTL
    "CNP_BID_TTL_SECONDS": "86400",
    # Failure handling
    "FAILURE_ESCALATION_MAX_RETRIES": "2",
    # Runtime policy
    "RUNTIME_POLICY_ENABLE": "1",
    # Execution
    "SYSTEM_EXECUTION_MODE": "auto",
}


def _derive(overrides: dict[str, str]) -> dict[str, str]:
    return {**_BASE, **overrides}


ABLATION_CONFIGS: dict[str, dict[str, str]] = {
    # C0: Full framework — control group
    "C0_full": _derive({}),

    # C1: No pheromone adaptation — tests stigmergic learning (H1)
    "C1_no_pheromone": _derive({
        "PHEROMONE_ENABLE": "0",
    }),

    # C2: No CNP bidding — round-robin assignment (H2)
    "C2_no_cnp": _derive({
        "CNP_MANAGER_ENABLE": "0",
        "AGENT_ASSIGNMENT_BASELINE": "round_robin",
    }),

    # C3: No SHACL validation — structural constraints removed (H3)
    "C3_no_shacl": _derive({
        "SHACL_ENABLE": "0",
    }),

    # C4: No compliance agent — domain rules removed (H3)
    "C4_no_compliance": _derive({
        "COMPLIANCE_AGENT_ENABLE": "0",
    }),

    # C3+C4: Both validation layers removed — tests interaction effect (H3)
    "C34_no_validation": _derive({
        "SHACL_ENABLE": "0",
        "COMPLIANCE_AGENT_ENABLE": "0",
    }),

    # C5: No LLM strategist — topological fallback (H4)
    "C5_no_strategist": _derive({
        "LLM_STRATEGIST_ENABLE": "0",
        "LLM_STRATEGIST_PROVIDER": "",
        "LLM_STRATEGIST_LOOP": "0",
        "LLM_STRATEGIST_LOOP_PROVIDER": "",
    }),

    # C6: No ethics checker — policy enforcement removed
    "C6_no_ethics": _derive({
        "ETHICS_CHECK_ENABLE": "0",
        "LLM_ETHICS_PROVIDER": "",
        "EXECUTION_REQUIRE_ETHICS": "0",
    }),

    # C7: Minimal baseline — all coordination removed (H5)
    "C7_minimal": _derive({
        "LLM_ETHICS_PROVIDER": "",
        "LLM_STUB_MODE": "1",
        "EXECUTION_REQUIRE_ETHICS": "0",
        "LLM_STRATEGIST_PROVIDER": "",
        "LLM_STRATEGIST_LOOP": "0",
        "LLM_STRATEGIST_LOOP_PROVIDER": "",
        "CNP_MANAGER_ENABLE": "0",
        "AGENT_ASSIGNMENT_BASELINE": "direct",
        "CNP_MAX_REFORWARDS": "0",
        "CNP_FAILURE_REFORWARD": "0",
        "PHEROMONE_ENABLE": "0",
        "SHACL_ENABLE": "0",
        "COMPLIANCE_AGENT_ENABLE": "0",
        "LLM_STRATEGIST_ENABLE": "0",
        "ETHICS_CHECK_ENABLE": "0",
        "EVAL_PIPELINE_MODE": "minimal",
        "FAILURE_ESCALATION_MAX_RETRIES": "0",
        "RUNTIME_POLICY_ENABLE": "0",
    }),
}


CONFIG_DESCRIPTIONS: dict[str, str] = {
    "C0_full": "Full framework — all components enabled (control group)",
    "C1_no_pheromone": "No pheromone adaptation — tests stigmergic learning (H1)",
    "C2_no_cnp": "No CNP bidding — round-robin agent assignment (H2)",
    "C3_no_shacl": "No SHACL validation — structural constraints removed (H3)",
    "C4_no_compliance": "No compliance agent — domain rules removed (H3)",
    "C34_no_validation": "No SHACL + no compliance — combined validation removed (H3 interaction)",
    "C5_no_strategist": "No LLM strategist — topological sort fallback (H4)",
    "C6_no_ethics": "No ethics checker — policy enforcement removed",
    "C7_minimal": "Minimal baseline — all coordination removed, sequential execution (H5)",
}


# Hypothesis mapping: which configs are compared for each hypothesis
HYPOTHESIS_CONFIGS: dict[str, list[str]] = {
    "H1_stigmergic": ["C0_full", "C1_no_pheromone"],
    "H2_competitive": ["C0_full", "C2_no_cnp"],
    "H3_defense_in_depth": ["C0_full", "C3_no_shacl", "C4_no_compliance", "C34_no_validation"],
    "H4_optional": ["C0_full", "C5_no_strategist"],
    "H5_justification": ["C0_full", "C7_minimal"],
}


def get_config(name: str) -> dict[str, str]:
    if name not in ABLATION_CONFIGS:
        raise ValueError(f"Unknown config: {name}. Available: {list(ABLATION_CONFIGS.keys())}")
    return ABLATION_CONFIGS[name]


def list_configs() -> list[str]:
    return list(ABLATION_CONFIGS.keys())
