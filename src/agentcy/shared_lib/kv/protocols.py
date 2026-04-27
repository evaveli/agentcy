"""
A **3 line** static duck protocol that lets any KV backend
(synchronous or asynchronous) pretend to be a Couchbase Collection.
"""
#src/agentcy/shared_lib/kv/protocols.py
from __future__ import annotations
from typing import Protocol, Any, Dict

class KVResult(Protocol):
    content_as: Dict

class KVCollection(Protocol):
    def upsert(self, key: str, value: Any, **kw) -> KVResult: ...
    def insert(self, key: str, value: Any, **kw) -> KVResult: ...
    def replace(self, key: str, value: Any, **kw) -> KVResult: ...
    def remove(self, key: str, **kw) -> KVResult: ...
    def get(self, key: str, **kw) -> KVResult: ...

    