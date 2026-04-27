## Table 5.0: Ablation Configuration Summary

| Config | Component Removed | What It Tests | Implementation |
|--------|-------------------|---------------|----------------|
| **C0** | None (full framework) | Control group | Default env vars |
| **C1** | Pheromone adaptation | Stigmergic learning (H1) | Skip pheromone_engine in pipeline DAG |
| **C2** | ICNP bidding | Competitive selection (H2) | CNP_MANAGER_ENABLE=0, round-robin assignment |
| **C3** | SHACL validation | Structural constraints (H3) | PLAN_SHACL_RULESET_PATH="" |
| **C4** | Compliance agent | Domain rules (H3) | Remove compliance-check from pipeline DAG |
| **C3+C4** | SHACL + compliance | Combined validation (H3) | Both C3 and C4 changes |
| **C5** | LLM strategist | Phase optimization (H4) | Skip llm_strategist in pipeline DAG |
| **C6** | Ethics checker | Policy enforcement | Skip ethics_checker in pipeline DAG |
| **C7** | All coordination | Lower bound baseline (H5) | Direct sequential calls, no ICNP/markers/validation |