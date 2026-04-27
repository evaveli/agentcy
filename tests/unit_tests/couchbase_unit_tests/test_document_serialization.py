import threading, time
import pytest
from pydantic import BaseModel

from src.agentcy.orchestrator_core.couch.safe_collection import SafeCollection
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool


class DummyCollection:
    def __init__(self):
        self.calls = []

    def insert(self, key, value, *args, **kwargs):
        self.calls.append(('insert', key, value))

    def upsert(self, key, value, *args, **kwargs):
        self.calls.append(('upsert', key, value))

    def replace(self, key, value, *args, **kwargs):
        self.calls.append(('replace', key, value))

    def __getattr__(self, name):
        raise AttributeError(f"DummyCollection has no attribute '{name}'")


class FakeBundle:
    def __init__(self):
        # raw store of dummy collections
        self._raw_col = DummyCollection()

    def collection(self, key):
        # wrap raw DummyCollection in SafeCollection
        return SafeCollection(self._raw_col)

    @property
    def collections(self):
        return {'foo': self._raw_col}

    @property
    def cluster(self):
        return None

    def close(self):
        pass


class FakePool(DynamicCouchbaseConnectionPool):
    def __init__(self):
        # skip parent initialization
        self.min = self.max = 1
        self.idle_timeout = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # prime with one fake bundle
        bundle = self._new_bundle()
        self._idle = [(bundle, time.time())]
        self._total = 1
        self._running = False

    def _new_bundle(self):
        return FakeBundle()


class DummyModel(BaseModel):
    x: int
    y: str


def test_pool_collections_returns_safe_collection_and_encodes():
    pool = FakePool()
    with pool.collections('foo') as coll:
        # should be a SafeCollection proxy
        assert isinstance(coll, SafeCollection)
        # write a Pydantic model through it
        coll.insert('key1', DummyModel(x=42, y='answer'))

    # after release, inspect raw dummy
    bundle, _ = pool._idle[0]
    raw = bundle.collections['foo']

    # should have one insert call with encoded dict
    assert raw.calls == [('insert', 'key1', {'x': 42, 'y': 'answer'})]
