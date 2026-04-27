# src/agentcy/orchestrator_core/stores/ephemeral_pipeline_store.py

import logging
from pydoc import Doc
import uuid
from typing import Optional, Dict, Any, cast, ContextManager
from couchbase.exceptions import CouchbaseException, DocumentExistsException
from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_BUCKET_EPHEMERAL, CB_COLLECTIONS, CB_SCOPE, EPHEMERAL_COLLECTIONS, CNames
from agentcy.shared_lib.kv.protocols import KVCollection

logger = logging.getLogger(__name__)

# Key formats
RUN_KEY_FMT     = "pipeline_run::{username}::{pipeline_id}::{run_id}"
OUTPUT_KEY_FMT  = "task_output::{username}::{task_id}::{run_id}"

class EphemeralPipelineStore:
    """
    CRUD façade for ephemeral pipeline‑run docs and large task outputs,
    using a DynamicCouchbaseConnectionPool under the hood.
    """

    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    def _run_key(self, username: str, pipeline_id: str, run_id: str) -> str:
        return RUN_KEY_FMT.format(
            username=username,
            pipeline_id=pipeline_id,
            run_id=run_id
        )

    def _output_key(self, username: str, task_id: str, run_id: str) -> str:
        return OUTPUT_KEY_FMT.format(
            username=username,
            task_id=task_id,
            run_id=run_id
        )

    # ——— Pipeline‑run documents ———

    @with_backoff(msg="ephemeral.create_run")
    def create_run(
        self,
        username: str,
        pipeline_id: str,
        run_id: str,
        initial_state: Dict[str, Any]
    ) -> None:
        """
        Insert a brand‑new pipeline‑run document.
        """
        key = self._run_key(username, pipeline_id, run_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.PIPELINE_RUNS_EPHEMERAL)) as runs:
            runs.insert(key, initial_state)
        logger.info("Created ephemeral run doc %s", key)

    @with_backoff(msg="ephemeral.read_run")
    def read_run(
        self,
        username: str,
        pipeline_id: str,
        run_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Read back a pipeline‑run document.
        """
        key = self._run_key(username, pipeline_id, run_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.PIPELINE_RUNS_EPHEMERAL)) as runs:
            res = runs.get(key)
        return res.content_as[dict] if res else None

    @with_backoff(msg="ephemeral.update_run")
    def update_run(
        self,
        username: str,
        pipeline_id: str,
        run_id: str,
        updated_state: Dict[str, Any]
    ) -> None:
        """
        Replace an existing pipeline‑run document.
        """
        key = self._run_key(username, pipeline_id, run_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.PIPELINE_RUNS_EPHEMERAL)) as runs:
            runs.replace(key, updated_state)
        logger.info("Updated ephemeral run doc %s", key)

    # ——— Large task‑output documents ———

    @with_backoff(msg="ephemeral.read_task_output")
    def read_task_output(
        self,
        username: str,
        task_id: str,
        run_id: str
    ) -> Dict[str, Any]:
        """
        Read the “large output” for a particular task.
        Returns empty dict if not found.
        """
        key = self._output_key(username, task_id, run_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.EPHEMERAL_OUTPUTS)) as outputs:
            try:
                res = outputs.get(key)
                return res.content_as[dict]
            except Exception as e:
                logger.warning("No task output %s (%s): %s", key, run_id, e)
                return {}

    @with_backoff(msg="ephemeral.store_task_output")
    def store_task_output(
        self,
        username: str,
        task_id: str,
        run_id: str,
        data: Dict[str, Any]
    ) -> None:
        """
        Insert a “large output” document for a task.
        """
        key = self._output_key(username, task_id, run_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.EPHEMERAL_OUTPUTS)) as outputs:
            try:
                outputs.insert(key, data)
            except DocumentExistsException:
                outputs.replace(key, data)
        logger.info("Stored task output %s", key)
    
    @with_backoff(msg="pipeline.list_runs")
    def list_runs(self, username: str, pipeline_id: str) -> list[str]:
        """
        Return *all* run-IDs for this pipeline (latest key sorts last because we
        order by the whole document key).  Empty list if none yet.
        """
        # ---- 1) build the key prefix “pipeline_run::<user>::<pipe>::” ----------
        prefix = RUN_KEY_FMT.format(
            username=username,
            pipeline_id=pipeline_id,
            run_id=""               # empty → acts as “glob” prefix
        )

        # ---- 2) compose a one-liner N1QL query --------------------------------
        coll_name = EPHEMERAL_COLLECTIONS[CNames.PIPELINE_RUNS_EPHEMERAL]
        q = (
            f"SELECT RAW META(r).id "
            f"FROM `{CB_BUCKET_EPHEMERAL}`.`{CB_SCOPE}`.`{coll_name}` AS r "
            f"WHERE META(r).id LIKE '{prefix}%' "
            f"ORDER BY META(r).id"
        )

        bundle = self._pool.acquire()
        try:
            rows = bundle.cluster.query(q)
            doc_ids = list(rows)
        except CouchbaseException as exc:
            logger.error("N1QL list_runs failed: %s", exc, exc_info=True)
            raise
        finally:
            self._pool.release(bundle)

        # ---- 3) strip the prefix so we only return the run_id part ------------
        return [doc_id[len(prefix):] for doc_id in doc_ids]

