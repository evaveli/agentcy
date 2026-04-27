# orchestrator_core/consumers/__init__.py
import asyncio
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.orchestrator_core.consumers.pipeline import register_pipeline_consumer
from agentcy.orchestrator_core.consumers.service  import register_service_consumer
from agentcy.orchestrator_core.consumers.plan_revision import revise_plan_consumer
from agentcy.orchestrator_core.consumers.cnp_lifecycle import cnp_lifecycle_consumer
from agentcy.orchestrator_core.consumers.task_dispatch import task_dispatch_consumer
from agentcy.orchestrator_core.consumers.ethics_re_evaluation import ethics_re_evaluation_consumer
from agentcy.orchestrator_core.consumers.cnp_manager import cnp_manager_consumer

async def run_consumers(rm: ResourceManager):
    """
    Spin up all consumers and keep them alive forever.
    They all receive the SAME ResourceManager instance created in lifespan().
    """
    await asyncio.gather(
        register_service_consumer(rm),
        register_pipeline_consumer(rm),
        revise_plan_consumer(rm),
        cnp_lifecycle_consumer(rm),
        task_dispatch_consumer(rm),
        ethics_re_evaluation_consumer(rm),
        cnp_manager_consumer(rm),
    )
