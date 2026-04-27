#src/agentcy/api_service/utils.py
from typing import List
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import DAGConfig

def derive_final_task_ids(dag: DAGConfig) -> List[str]:
    all_ids = [t.id for t in dag.tasks]
    depended_on = set()

    for t in dag.tasks:
        deps = []
        if t.inputs and isinstance(t.inputs, dict):
            deps = t.inputs.get("dependencies") or []
        for d in deps:
            depended_on.add(d)

    leaves   = [tid for tid in all_ids if tid not in depended_on]
    flagged  = [t.id for t in dag.tasks if getattr(t, "is_final_task", False)]
    finals   = set(leaves) | set(flagged)

    # keep a stable, human-friendly order
    return [tid for tid in all_ids if tid in finals]
