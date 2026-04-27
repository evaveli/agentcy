#tests/e2e_tests/run_state.py
"""
Shared state for end-to-end tests.
Keeps track of asyncio tasks spawned per pipeline-run.
"""
from collections import defaultdict

RUN_CONSUMER_TASKS: dict[str, list] = defaultdict(list)
