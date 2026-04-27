from fastapi.encoders import jsonable_encoder
from couchbase.collection import Collection


class SafeCollection:
    """
    Thin proxy around a Couchbase Collection.

    * Every write op passes the value through jsonable_encoder().
    * All other attributes fall through to the underlying Collection.
    """

    def __init__(self, raw: Collection):
        self._raw = raw

    # ─────────── internal helpers ────────────────────────────────────────
    @staticmethod
    def _encode(value):
        return jsonable_encoder(value)          # handles BaseModel, UUID, set …

    # ─────────── write operations we care about ──────────────────────────
    def insert(self, key, value, *a, **kw):
        return self._raw.insert(key, self._encode(value), *a, **kw)

    def upsert(self, key, value, *a, **kw):
        return self._raw.upsert(key, self._encode(value), *a, **kw)

    def replace(self, key, value, *a, **kw):
        return self._raw.replace(key, self._encode(value), *a, **kw)

    # ─────────── everything else is proxied ──────────────────────────────
    def __getattr__(self, name):               # get(), exists(), remove(), …
        return getattr(self._raw, name)
