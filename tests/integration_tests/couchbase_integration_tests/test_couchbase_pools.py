#tests/integration_tests/couchbase_integration_tests/test_couchbase_pools.py
import time
import pytest
import asyncio
from src.agentcy.pipeline_orchestrator.couchbase_configs.couchbase_pool import DynamicCouchbaseConnectionPool

# The following tests assume that your environment variables (.env) are set up to connect to a test Couchbase cluster.
# They will use the actual CouchbaseConnectionManager to create real connections.

@pytest.fixture(scope="module")
def pool():
    # Configure a small pool for testing; adjust idle_timeout to a low value to test cleanup.
    pool = DynamicCouchbaseConnectionPool(min_size=2, max_size=8, idle_timeout=5)
    yield pool
    pool.close_all()

def test_initialization(pool):
    # On initialization, the pool should have min_size connections.
    assert pool.total_connections == pool.min_size, "Pool did not initialize with the minimum connections."
    assert len(pool.idle_connections) == pool.min_size, "Idle connection count should equal min_size."

def test_get_and_return_connection(pool):
    # Get a connection from the pool.
    conn = pool.get_connection(timeout=10)
    # Verify that the connection has a valid bucket (as a basic check that it works).
    assert hasattr(conn, "bucket"), "Connection is missing a bucket attribute."
    # Return the connection.
    pool.return_connection(conn)
    # After returning, idle_connections should be increased.
    assert len(pool.idle_connections) >= pool.min_size, "Returned connection was not added to the idle pool."

def test_dynamic_scaling(pool):
    # Acquire connections until reaching max_size.
    connections = []
    for _ in range(pool.max_size):
        conn = pool.get_connection(timeout=10)
        connections.append(conn)
    assert pool.total_connections == pool.max_size, "Pool did not scale up to max_size."
    assert len(pool.idle_connections) == 0, "No connections should be idle when all are checked out."

    # Return all connections.
    for conn in connections:
        pool.return_connection(conn)
    assert len(pool.idle_connections) == pool.max_size, "Not all connections returned to the idle pool."

def test_timeout_when_pool_exhausted(pool):
    # Exhaust the pool by acquiring max_size connections.
    connections = [pool.get_connection(timeout=10) for _ in range(pool.max_size)]
    with pytest.raises(Exception) as excinfo:
        pool.get_connection(timeout=3)
    assert "Timeout" in str(excinfo.value), "Expected a timeout exception when pool is exhausted."
    # Return the connections.
    for conn in connections:
        pool.return_connection(conn)

def test_cleanup_idle_connections(pool):
    # Get a connection and return it to update its timestamp.
    conn = pool.get_connection(timeout=10)
    pool.return_connection(conn)
    initial_total = pool.total_connections

    # Wait for longer than idle_timeout (plus a small buffer) so that the cleanup thread can run.
    time.sleep(pool.idle_timeout + 2)
    # Ensure that the pool did not shrink below min_size.
    assert pool.total_connections >= pool.min_size, "Pool dropped below the minimum number of connections."
    # Optionally, if pool had scaled up previously, expect some to be pruned.
    if initial_total > pool.min_size:
        assert pool.total_connections < initial_total, "Idle connections were not pruned as expected."

def test_close_all(pool):
    pool.close_all()
    # After closing, the idle connections list should be empty.
    assert len(pool.idle_connections) == 0, "Pool did not clear idle connections after close_all()."
