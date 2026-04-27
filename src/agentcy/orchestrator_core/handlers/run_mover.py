# src/agentcy/orchestrator_core/run_mover.py
"""Move a finished run from the *ephemeral* bucket into the *persistent*
archive bucket in a single Couchbase transaction.
"""

from __future__ import annotations
import logging
from typing import cast

from couchbase.transactions import AttemptContext
from agentcy.orchestrator_core.couch.config import (
    CNames,
    EPHEMERAL_COLLECTIONS,
    CB_COLLECTIONS,
    CB_BUCKET,
    CB_SCOPE,
)
from agentcy.orchestrator_core.couch.safe_collection import SafeCollection
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    PipelineStatus,
)
from agentcy.orchestrator_core.constants import TERMINAL
from couchbase.collection import Collection

RUN_KEY_FMT = "pipeline_run::{username}::{pipeline_id}::{run_id}"

logger = logging.getLogger(__name__)


def finalize_run(
    hot_pool: DynamicCouchbaseConnectionPool,
    username: str,
    pipeline_id: str,
    run_id: str,
) -> None:
    """Atomically *move* a terminal‑state run from the hot bucket to the
    archive bucket.

    Parameters
    ----------
    hot_pool : DynamicCouchbaseConnectionPool
        The pool that talks to the _ephemeral_ bucket (where the doc lives
        while RUNNING).
    username / pipeline_id / run_id : str
        Composite key identifying the run.
    """

    hot_key  = EPHEMERAL_COLLECTIONS[CNames.PIPELINE_RUNS_EPHEMERAL]
    cold_key = CB_COLLECTIONS[CNames.PIPELINE_RUNS]
    doc_key  = RUN_KEY_FMT.format(
        username=username, pipeline_id=pipeline_id, run_id=run_id
    )

    logger.info("finalize_run start for %s/%s/%s", username, pipeline_id, run_id)

    # --- borrow one bundle from the hot pool ---------------------------------
    bundle = hot_pool.acquire()
    try:
        hot_coll = bundle.collection(hot_key)

        # cold collection lives in *another bucket* – open it lazily via the
        # same Cluster object (multi-bucket transactions are supported).
        cold_bucket = bundle.cluster.bucket(CB_BUCKET)
        cold_scope  = cold_bucket.scope(CB_SCOPE)
        cold_coll   = SafeCollection(cold_scope.collection(cold_key))
        logger.info("Prepared cold collection %s in bucket %s", cold_key, CB_BUCKET)


        # ------------------------ transactional move ------------------------- #
        def _txn(ctx: AttemptContext):
            src = ctx.get(cast(Collection, hot_coll), doc_key)
            body = src.content_as[dict]
            status = PipelineStatus(body["status"])

            logger.info("[txn] current status for %s: %s", doc_key, status)

            if status not in TERMINAL:
                raise RuntimeError("Run is not in a terminal state; aborting move")

            logger.info("[txn] inserting doc %s into cold", doc_key)
            ctx.insert(cast(Collection, cold_coll), doc_key, body)
            logger.info("[txn] removing doc %s from hot", doc_key)
            ctx.remove(src)  # delete from hot store
        logger.info("Running couchbase transaction for run %s", doc_key)
        bundle.cluster.transactions.run(_txn)
        logger.info("Successfully finalized run %s", doc_key)
    finally:
        hot_pool.release(bundle)
        logger.info("Released couchbase bundle for %s", doc_key)
