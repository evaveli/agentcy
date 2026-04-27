#src/agentcy/orchestrator_core/couch/pool.py
import time, threading, contextlib, logging
from typing import Any, Dict, Tuple, cast
from couchbase.cluster import Cluster
from datetime import timedelta
from couchbase.options import ClusterOptions
from couchbase.transactions import Transactions
from couchbase.auth import PasswordAuthenticator
from couchbase.options import TransactionConfig
from couchbase.durability import DurabilityLevel

from agentcy.orchestrator_core.couch.config import (
    CB_CONN_STR, CB_USER, CB_PASS, CB_SCOPE,

)
from agentcy.shared_lib.kv.protocols import KVCollection
from agentcy.orchestrator_core.couch.safe_collection import SafeCollection 
from agentcy.observability.couchbase_shim import (
    TracedCollection,
    TracedTransactions,
    TracedCluster,
    )


logger = logging.getLogger(__name__)

class _ConnBundle:
    """Wraps a Cluster + all collections for *one* bucket."""

    def __init__(self, bucket_name: str, collections_map: dict[str, str]):
        raw_cluster =  Cluster(
            CB_CONN_STR,
            cast(Any, ClusterOptions(PasswordAuthenticator(CB_USER, CB_PASS))),
        )
        try:
            raw_cluster.wait_until_ready(timedelta(seconds=10))
        except Exception as e:
            logger.warning("Cluster wait_until_ready failed: %s", e)

        raw_txns = Transactions(
            raw_cluster,
            TransactionConfig(
                durability=None,
        ))

        traced_txns = TracedTransactions(raw_txns, bucket=bucket_name, scope=CB_SCOPE, conn_str=CB_CONN_STR)
        self.cluster = TracedCluster(raw_cluster, default_bucket=bucket_name, conn_str=CB_CONN_STR, traced_txns=traced_txns)
        self.txns = traced_txns
        bucket = raw_cluster.bucket(bucket_name)

        # Ensure required collections exist in the target scope
        coll_mgr = bucket.collections()
        for phys_name in set(collections_map.values()):
            try:
                coll_mgr.create_collection(collection_name=phys_name, scope_name=CB_SCOPE)
            except Exception as e:
                if "already exists" not in str(e):
                    logger.debug("Collection %s exists? %s", phys_name, e)

        scope  = bucket.scope(CB_SCOPE)

        self._collections: Dict[str, KVCollection] = {
            logical: TracedCollection(
                SafeCollection(scope.collection(phys)),
                bucket=bucket_name,
                scope=CB_SCOPE,
                collection=phys,
                conn_str=CB_CONN_STR,
            )
            for logical, phys in collections_map.items()
        }

    # expose to DynamicCouchbaseConnectionPool
    def collection(self, logical: str) -> KVCollection:
        try:
            return self._collections[logical]
        except KeyError as e:
            raise KeyError(f"Unknown collection key '{logical}'") from e
        
    def transactions(self) -> Transactions:
        return cast(Transactions, self.txns)

class DynamicCouchbaseConnectionPool:
    """
    Thread‑safe pool of _ConnBundle, with idle reaper.
    """

    def __init__(
            self,
            bucket_name: str,
            collections_map: dict[str, str],
            *, 
            min_size=1, 
            max_size=10, 
            idle_timeout=60.0
            ):
        self._bucket_name   = bucket_name
        self._collections   = collections_map
        self.min           = min_size
        self.max           = max_size
        self._idle_timeout  = idle_timeout
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._idle: list[Tuple[_ConnBundle, float]] = []
        self._total = 0
        self._running = True

        # eagerly create min_size bundles
        for _ in range(self.min):
            self._idle.append((self._new_bundle(), time.time()))
            self._total += 1

        threading.Thread(target=self._reaper, daemon=True).start()


    @contextlib.contextmanager
    def cluster(self, timeout=10.0):
        """Borrow a TracedCluster for N1QL or admin ops."""
        bundle = self.acquire(timeout=timeout)
        try:
            yield bundle.cluster
        finally:
            self.release(bundle)

    @contextlib.contextmanager
    def transactions(self, timeout=10.0):
        """Borrow the traced Transactions facade."""
        bundle = self.acquire(timeout=timeout)
        try:
            yield bundle.transactions()
        finally:
            self.release(bundle)

    def _new_bundle(self) -> _ConnBundle:
        logger.info("Opening new Couchbase connection bundle")
        return _ConnBundle(self._bucket_name, self._collections)

    def _close_bundle(self, bundle: _ConnBundle):
        try:
            bundle.cluster.close()
        except Exception as e:
            logger.warning("Error closing Couchbase cluster: %s", e)

    def acquire(self, timeout=10.0) -> _ConnBundle:
        deadline = time.time() + timeout
        with self._cond:
            while True:
                if self._idle:
                    bundle, _ = self._idle.pop()
                    return bundle
                if self._total < self.max:
                    self._total += 1
                    return self._new_bundle()
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for Couchbase connection")
                self._cond.wait(remaining)

    def release(self, bundle: _ConnBundle):
        with self._cond:
            self._idle.append((bundle, time.time()))
            self._cond.notify()

    @contextlib.contextmanager
    def collections(self, *keys: str, timeout=10.0):
        """
        Usage:
            with pool.collections("agents", "pipelines") as (agents, pipelines):
                agents.upsert(...)
        """
        if not keys:
            raise ValueError("collections() requires at least one collection key")
        bundle = self.acquire(timeout=timeout)
        try:
            if len(keys) == 1:
                # Single collection → yield the collection itself
                yield bundle.collection(keys[0])
            else:
                # Multiple → yield a tuple of collections
                cols = tuple(bundle.collection(k) for k in keys)
                yield cols
        finally:
            self.release(bundle)

    def _reaper(self):
        """Periodically close truly idle bundles."""
        while self._running:
            with self._cond:
                now = time.time()
                keep: list[Tuple[_ConnBundle, float]] = []
                for bundle, ts in self._idle:
                    if (now - ts) > self._idle_timeout and self._total > self.min:
                        self._close_bundle(bundle)
                        self._total -= 1
                    else:
                        keep.append((bundle, ts))
                self._idle = keep
            time.sleep(1)

    def close_all(self):
        """Shutdown pool and all open bundles."""
        with self._cond:
            self._running = False
            for bundle, _ in self._idle:
                try:
                    bundle.cluster.close()
                except Exception:
                    pass
            self._idle.clear()
            self._total = 0
            self._cond.notify_all()
        logger.info("Couchbase pool shut down")
