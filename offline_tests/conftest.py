import pytest

from agentcy.agents import foundational_agents as fa


@pytest.fixture(autouse=True)
def _reset_foundational_state():
    # Ensure module-level caches do not leak across tests.
    fa.REGISTRY.clear()
    fa.PLAN_CACHE.clear()
    fa.AUDIT_LOGS.clear()
    fa.PHEROMONES.clear()
    yield
    fa.REGISTRY.clear()
    fa.PLAN_CACHE.clear()
    fa.AUDIT_LOGS.clear()
    fa.PHEROMONES.clear()
