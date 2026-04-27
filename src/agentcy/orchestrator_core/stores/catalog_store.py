# src/agentcy/orchestrator_core/stores/user_catalog_store.py
from __future__ import annotations
import copy, re
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Tuple, Optional, cast

from couchbase.exceptions import DocumentExistsException, DocumentNotFoundException, CASMismatchException
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.shared_lib.kv.protocols import KVCollection, KVResult

_name = re.compile(r"[^a-z0-9\-]+")
_user = re.compile(r"[^a-z0-9_\-\.]+")

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _doc_id(env: str, username: str) -> str:
    e = _name.sub("-", env.strip().lower())
    u = _user.sub("-", username.strip().lower())
    return f"catalog::{e}::user::{u}"

def _path(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    kind = entry["kind"]
    if kind == "wheel":
        return ("wheel", entry["name"], entry["version"])
    if kind == "oci":
        return ("oci", entry["name"], entry["version"])  # name=repo, version=tag (we keep same keys)
    raise ValueError("entry.kind must be 'wheel' or 'oci'")

def _digest(entry: Dict[str, Any]) -> str:
    return entry["sha256"] if entry["kind"] == "wheel" else entry["digest"]

def _immutables(entry: Dict[str, Any]) -> Dict[str, Any]:
    base = {"kind": entry["kind"], "name": entry["name"], "version": entry["version"], "status": entry["status"]}
    if entry["kind"] == "wheel":
        base.update({
            "index_url": entry["index_url"],
            "requires_python": entry.get("requires_python"),
            "agentcy_abi": entry.get("agentcy_abi"),
        })
    else:
        base.update({"registry_url": entry["registry_url"]})
    return base

class CatalogConflict(Exception): ...
class TooManyRetries(Exception): ...

class UserCatalogStore:
    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    def upsert(
        self,
        *,
        env: str,
        username: str,
        entry: Dict[str, Any],
        allow_overwrite: bool = False,
    ) -> Tuple[str, Literal["created","unchanged","updated"]]:
        """
        Writes/updates exactly ONE document per {env, user}.
        Creates the doc if missing, updates a single leaf under (kind/name/version).
        """
        doc_id = _doc_id(env, username)
        kind, name, version = _path(entry)
        for _ in range(6):  # small CAS retry loop
            with self._pool.collections("catalog") as col_cm:
                col: KVCollection = cast(KVCollection, col_cm)
                try:
                    res: KVResult = cast(KVResult, col.get(doc_id))
                    doc = cast(dict, res.content_as[dict])
                except DocumentNotFoundException:
                    # create skeleton doc
                    doc = {"_meta": {"type": "user-catalog", "created_at": _now(), "updated_at": _now()},
                           "wheel": {}, "oci": {}}
                    try:
                        col.insert(doc_id, doc)
                        # re-read to get CAS on next loop
                        continue
                    except DocumentExistsException:
                        continue  # race; retry

                # navigate to node
                node = doc.setdefault(kind, {}).setdefault(name, {})
                existing: Optional[Dict[str, Any]] = node.get(version)

                if existing is None:
                    node[version] = entry
                    doc["_meta"]["updated_at"] = _now()
                    try:
                        cas_val: Any = getattr(res, "cas", None)   # satisfy Pylance; works with your KVResult wrapper
                        col.replace(doc_id, doc, cas=cas_val)
                        return doc_id, "created"
                    except CASMismatchException:
                        # raced; retry outer loop
                        continue


                # compare immutables & digest
                if _immutables(existing) != _immutables(entry):
                    raise CatalogConflict(f"Immutable fields changed for {doc_id}:{kind}/{name}/{version}")

                if _digest(existing) == _digest(entry):
                    # Merge wheel signatures if present (idempotent)
                    if (existing.get("kind") == "wheel") and (entry.get("signatures")):
                        cur_sigs = existing.get("signatures") or []
                        new_sigs = entry.get("signatures") or []
                        merged = sorted(set(cur_sigs) | set(new_sigs))
                        if merged != cur_sigs:
                            existing["signatures"] = merged
                            doc["_meta"]["updated_at"] = _now()
                            cas_val: Any = getattr(res, "cas", None)  # KVResult CAS is implementation-specific
                            try:
                                col.replace(doc_id, doc, cas=cas_val)
                                return doc_id, "updated"
                            except CASMismatchException:
                                # race: retry outer loop
                                continue
                    return doc_id, "unchanged"

                # digest differs
                if not allow_overwrite:
                    raise CatalogConflict(f"Digest mismatch for {doc_id}:{kind}/{name}/{version}")

                prev = {"digest": _digest(existing), "updated_at": existing.get("_meta", {}).get("updated_at")}
                node[version] = copy.deepcopy(entry)
                node[version]["_previous"] = prev
                doc["_meta"]["updated_at"] = _now()
                try:
                    cas_val: Any = getattr(res, "cas", None)
                    col.replace(doc_id, doc, cas=cas_val)
                    return doc_id, "updated"
                except CASMismatchException:
                    continue

        raise TooManyRetries(f"CAS retries exhausted for {doc_id}")
    
    # inside class UserCatalogStore:

    async def query(
        self,
        *,
        env: str,
        username: str,
        kind: Optional[Literal["wheel","oci"]] = None,
        status: Optional[Literal["dev","stg","prod"]] = None,
        name: Optional[str] = None,
        version: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        Return catalog entries for a given (env, user), optionally filtered.
        Each result is a dict that includes at least: kind, name, version, status (+ whatever was stored).
        """
        doc_id = _doc_id(env, username)
        try:
            with self._pool.collections("catalog") as col_cm:
                col: KVCollection = cast(KVCollection, col_cm)
                res: KVResult = cast(KVResult, col.get(doc_id))
                doc = cast(dict, res.content_as[dict])
        except DocumentNotFoundException:
            return []

        out: list[Dict[str, Any]] = []

        def _matches(e: Dict[str, Any]) -> bool:
            if kind    and e.get("kind")    != kind:    return False
            if status  and e.get("status")  != status:  return False
            if name    and e.get("name")    != name:    return False
            if version and e.get("version") != version: return False
            return True

        # Flatten both kinds
        for k in ("wheel", "oci"):
            tree = doc.get(k) or {}
            for n, versions in tree.items():
                if not isinstance(versions, dict):
                    continue
                for v, entry in versions.items():
                    if not isinstance(entry, dict):
                        continue
                    # Ensure core fields are present even if missing in stored value
                    e = dict(entry)
                    e.setdefault("kind", k)
                    e.setdefault("name", n)
                    e.setdefault("version", v)
                    if _matches(e):
                        out.append(e)

        # (optional) stable-ish ordering
        out.sort(key=lambda e: (e.get("kind",""), e.get("name",""), str(e.get("version",""))), reverse=False)
        return out

    async def resolve(
        self,
        *,
        username: str,
        kind: Literal["wheel","oci"],
        name: str,
        version: str,
        status: str = "prod",
    ) -> Optional[dict[str, Any]]:
        res = await self.query(username=username, kind=kind, status=status, name=name, version=version)
        return res[0] if res else None
