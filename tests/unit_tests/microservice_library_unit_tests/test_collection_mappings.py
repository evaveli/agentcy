# tests/test_collection_mappings.py

import pytest
from src.agentcy.settings import get_settings
from src.agentcy.orchestrator_core.couch.config import CB_COLLECTIONS, EPHEMERAL_COLLECTIONS

def test_collection_keys_match_env_settings():
    """
    Ensure that the collections our code uses are exactly those
    defined in the .env-driven Settings.collections / ephemeral_collections.
    """
    s = get_settings()
    expected = set(s.collections) | set(s.ephemeral_collections)
    actual   = set(CB_COLLECTIONS.keys()) | set(EPHEMERAL_COLLECTIONS.keys())

    # any stray key in code that isn't in the env-driven map is a failure
    assert actual <= expected, f"Found unexpected collection keys: {actual - expected}"
