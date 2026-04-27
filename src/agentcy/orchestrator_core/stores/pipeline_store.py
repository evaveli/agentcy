# src/agentcy/orchestrator_core/stores/pipeline_store.py
import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, cast, ContextManager

from click import Context
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CB_COLLECTIONS, CB_BUCKET, CB_SCOPE, CNames
from couchbase.exceptions import (
    CouchbaseException,
    DocumentNotFoundException,
    TransactionFailed,
    TransactionCommitAmbiguous,
    TransactionOperationFailed,
)
from couchbase.transactions import AttemptContext
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import PipelineConfig
from agentcy.parsing_layer.generate_rabbitmq_manifests import PipelineGenerator
from agentcy.rabbitmq_workflow.workflow_config_parser import ConfigParser
from agentcy.pydantic_models.pipeline_validation_models.user_define_pipeline_model import PipelineCreate
from agentcy.api_service.utils import derive_final_task_ids
from agentcy.shared_lib.kv.protocols import KVCollection

logger = logging.getLogger(__name__)

PIPELINE_KEY_FMT        = "pipeline::{username}::{pipeline_id}"
PIPELINE_CONFIG_FMT     = "pipeline_config::{username}::{pipeline_id}"
PIPELINE_VERSION_FMT    = "pipeline_versioning::{username}::{pipeline_id}"
RUN_KEY_FMT             = "pipeline_run::{username}::{pipeline_id}::{run_id}"
RUNS_COLLECTION_KEY     = CNames.PIPELINE_RUNS      
CONFIG_COLLECTION_KEY   = CNames.PIPELINE_CONFIG
VERSIONING_COLLECTION   = CNames.PIPELINE_VERSIONING
CONFIG_VERSIONING_KEY   = CNames.PIPELINE_CONFIG_VERSION
RUNS_VERSIONING_KEY     = CNames.PIPELINE_RUNS_VERSION

class PipelineStore:
    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    @with_backoff(msg="pipeline.create")
    def create(self, username: str, pdata: PipelineConfig) -> str:
        """
        Fully create pipeline + config + initial run in a single transaction.
        Returns the new pipeline_id.
        """
        pipeline_id = str(uuid.uuid4())
        # stamp in the Pydantic model
        pdata = pdata.model_copy(update={
            "pipeline_id": pipeline_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        self._persist_pipeline(
            username=username,
            pipeline_id=pipeline_id,
            pipeline_data=pdata,
            action="CREATE",
            enable_versioning=True
        )
        return pipeline_id

    @with_backoff(msg="pipeline.read")
    def read(self, username: str, pipeline_id: str) -> Optional[Dict[str,Any]]:
        """
        Read the persisted pipeline document (no config or runs).
        """
        key = PIPELINE_KEY_FMT.format(username=username, pipeline_id=pipeline_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CNames.PIPELINES)) as pipelines:
            res = pipelines.get(key)
        return res.content_as[dict] if res else None
    

    @with_backoff(msg="pipeline.read_run")
    def read_run(
        self,
        username: str,
        pipeline_id: str,
        pipeline_run_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Read a single ephemeral pipeline‐run document.
        """
        doc_key = RUN_KEY_FMT.format(
            username=username,
            pipeline_id=pipeline_id,
            run_id=pipeline_run_id
        )
        # borrow one bundle, do the get, return bundle
        with cast(ContextManager[KVCollection],
                  self._pool.collections(RUNS_COLLECTION_KEY)) as runs_coll:
            res = runs_coll.get(doc_key)

        if not res:
            logger.info("No pipeline‐run found for key %s", doc_key)
            return None
        data = res.content_as[dict]
        logger.info("Retrieved pipeline‐run %s", doc_key)
        return data

    @with_backoff(msg="pipeline.delete")
    def delete(self, username: str, pipeline_id: str) -> None:
        """
        Delete the pipeline document (does not cascade-run artifacts).
        """
        key = PIPELINE_KEY_FMT.format(username=username, pipeline_id=pipeline_id)
        with cast(ContextManager[KVCollection],
                  self._pool.collections(CB_COLLECTIONS[CNames.PIPELINES])) as pipelines:
            pipelines.remove(key)
        logger.info("Deleted pipeline %s for %s", pipeline_id, username)

    @with_backoff(msg="pipeline.list")
    def list(self, username: str) -> list[Dict[str,str]]:
        """
        List all pipelines for a user.
        """
        prefix = PIPELINE_KEY_FMT.format(username=username, pipeline_id="")
        # build N1QL
        bucket = CB_BUCKET
        scope  = CB_SCOPE
        coll_name = CB_COLLECTIONS[CNames.PIPELINES]
        q = (
          "SELECT META(p).id AS id, p.pipeline_name "
          f"FROM `{bucket}`.`{scope}`.`{coll_name}` p "
          f"WHERE META(p).id LIKE '{prefix}%'"
        )
        # we can run query on the cluster object
        bundle = self._pool.acquire()
        try:
            rows = bundle.cluster.query(q)
        finally:
            self._pool.release(bundle)

        return [
            {"pipeline_id":   r["id"][len(prefix):],
             "pipeline_name": r["pipeline_name"]}
            for r in rows
        ]

    # ──────────────────────────────────────────────────────────────────────────────
    # replace *entire* update() in PipelineStore
    # ──────────────────────────────────────────────────────────────────────────────
    @with_backoff(msg="pipeline.update")
    def update(self, username: str, pipeline_id: str, pdata: PipelineConfig) -> None:
        """
        Upgrade the stub written by the REST endpoint to a full pipeline
        document **without** a multi-op transaction (dev bucket cannot satisfy
        durability).  We accept losing the atomicity guarantees in return for
        reliability on a single-node couchbase-server.

        • overwrites the pipeline doc
        • (re)generates & upserts pipeline_config
        • skips history/versioning
        """
        # ---------- keys & collections ------------------------------------------
        doc_key    = PIPELINE_KEY_FMT.format(username=username, pipeline_id=pipeline_id)
        cfg_key    = PIPELINE_CONFIG_FMT.format(username=username, pipeline_id=pipeline_id)

        bundle = self._pool.acquire()
        try:
            pipe_coll = bundle.collection(CNames.PIPELINES)
            cfg_coll  = bundle.collection(CONFIG_COLLECTION_KEY)

            # ---------- 1) prepare bodies ---------------------------------------
            pdata = pdata.model_copy(
                update=dict(last_updated=datetime.now(timezone.utc).isoformat())
            )

            final_cfg    = self._generate_pipeline_config(pdata)
            final_ids = final_cfg.get("final_task_ids") or derive_final_task_ids(pdata.dag)

            pdata = pdata.model_copy(update={"final_task_ids": final_ids})


            pipeline_doc = pdata.model_dump()
            pipeline_doc.update(
                pipeline_id=pipeline_id,
                version=1,                           # first real version
                rabbitmq_configs=final_cfg["rabbitmq_configs"],
            )
            final_cfg["version"] = 1

            # ---------- 2) perform simple upserts -------------------------------
            pipe_coll.upsert(doc_key,  jsonable_encoder(pipeline_doc))
            cfg_coll.upsert(cfg_key,   jsonable_encoder(final_cfg))

            logger.info("Stub %s upgraded to full pipeline document", pipeline_id)

        finally:
            self._pool.release(bundle)

    
    @with_backoff(msg='pipeline.insert_stub')
    def insert_stub(self, username: str, pipeline_id: str, payload: PipelineCreate) -> None:
        """
        Insert a lightweight “stub” document for immediate read-after-write in the API.
        """
        key = PIPELINE_KEY_FMT.format(username=username, pipeline_id=pipeline_id)
        now = datetime.now(timezone.utc).isoformat()
        stub = {
            "pipeline_id":   pipeline_id,
            "pipeline_name": payload.pipeline_name,
            "created_at":    now,
            "last_updated":  now,
            "version":       1,
        }
        # borrow a bundle & write just the stub
        with cast(ContextManager[KVCollection], self._pool.collections(CB_COLLECTIONS[CNames.PIPELINES])) as pipelines_coll:
            pipelines_coll.upsert(key, stub)
        logger.info("Inserted pipeline stub %s for user %s", pipeline_id, username)
        

    def _persist_pipeline(
        self,
        username: str,
        pipeline_id: str,
        pipeline_data: PipelineConfig,
        action: str,
        enable_versioning: bool
    ) -> None:
        """
        Transactionally create or update:
          - pipeline document
          - pipeline_config document
          - (if CREATE) initial pipeline_run document
          - versioned backups if enable_versioning=True
        """
        # pre‑compute keys
        doc_key         = PIPELINE_KEY_FMT.format(username=username, pipeline_id=pipeline_id)
        config_key      = PIPELINE_CONFIG_FMT.format(username=username, pipeline_id=pipeline_id)
        version_key     = PIPELINE_VERSION_FMT.format(username=username, pipeline_id=pipeline_id)

        bundle = self._pool.acquire()
        try:
            # get named KVCollections
            pipelines_coll        = bundle.collection(CNames.PIPELINES)  # sorry: this yields a single coll if you pass one key
            config_coll           = bundle.collection(CONFIG_COLLECTION_KEY)
            versioning_coll       = bundle.collection(VERSIONING_COLLECTION)
            config_version_coll   = bundle.collection(CONFIG_VERSIONING_KEY)
            runs_version_coll     = bundle.collection(RUNS_VERSIONING_KEY)
        except KeyError as ke:
            self._pool.release(bundle)
            raise
        # we have pipelines_coll as KVCollection, cluster from bundle.cluster
        def txn_logic(ctx: AttemptContext) -> None:
            """
            • upgrades the stub → full pipeline document  (or updates)
            • writes/updates pipeline_config
            • silently skips history-backups if the bucket cannot satisfy durability
            """
            logger.info("Transaction %s pipeline=%s user=%s",
                        action, pipeline_id, username)

            # ── 1) determine new version & (best-effort) backup current doc ─────────
            existing = None
            if action == "UPDATE":
                existing     = ctx.get(cast(Any, pipelines_coll), doc_key)
                existing_doc = existing.content_as[dict]
                curr_ver     = existing_doc.get("version", 1)
                version      = curr_ver + 1

                if enable_versioning:
                    bkp_key = f"{version_key}::v{curr_ver}::{now_str()}"
                    try:
                        ctx.insert(cast(Any, versioning_coll), bkp_key, existing_doc)
                    except TransactionOperationFailed:
                        # dev bucket without durability support → ignore
                        logger.info("Backup insert skipped (durability_impossible)")
            else:
                version = 1

            # ── 2) generate final wiring & build pipeline doc ───────────────────────
            final_cfg = self._generate_pipeline_config(pipeline_data)

            pipeline_body = pipeline_data.model_dump()
            pipeline_body.update(
                pipeline_id      = pipeline_id,
                version          = version,
                last_updated     = datetime.now(timezone.utc).isoformat(),
                rabbitmq_configs = final_cfg["rabbitmq_configs"],
            )

            if action == "CREATE":
                try:
                    ctx.insert(cast(Any, pipelines_coll), doc_key, jsonable_encoder(pipeline_body))
                except TransactionOperationFailed:
                    stub_doc = ctx.get(cast(Any, pipelines_coll), doc_key)
                    ctx.replace(stub_doc, jsonable_encoder(pipeline_body))
            else:
                assert existing is not None
                ctx.replace(existing, jsonable_encoder(pipeline_body))

            # ── 3) write / upsert pipeline_config (idempotent) ──────────────────────
            try:
                final_cfg["version"] = 1
                ctx.insert(cast(Any, config_coll), config_key, jsonable_encoder(final_cfg))

            except TransactionOperationFailed:
                # already exists → fetch, optional backup, then replace
                cur_doc = ctx.get(cast(Any, config_coll), config_key)
                cur_cfg = cur_doc.content_as[dict]

                if enable_versioning and action == "UPDATE":
                    cfg_bkp_key = f"{config_key}::v{cur_cfg['version']}::{now_str()}"
                    try:
                        ctx.insert(cast(Any, config_version_coll), cfg_bkp_key, cur_cfg)
                    except TransactionOperationFailed:
                        logger.info("Config-backup skipped (durability_impossible)")

                final_cfg["version"] = cur_cfg["version"] + 1
                ctx.replace(cur_doc, jsonable_encoder(final_cfg))


        try:
            bundle.transactions().run(txn_logic)
        except (TransactionFailed, TransactionCommitAmbiguous, CouchbaseException) as e:
            logger.error("Pipeline TX failed: %s", e, exc_info=True)
            raise
        finally:
            self._pool.release(bundle)

    def _generate_pipeline_config(self, pdata: PipelineConfig) -> Dict[str,Any]:
        """
        Given your Pydantic PipelineConfig, run ConfigParser + PipelineGenerator
        and return the final dict.

        The result now includes:
        - rabbitmq_topology: structured data (exchanges, queues, bindings)
        - rabbitmq_yaml_manifest: rendered YAML for Kubernetes CRDs (persisted to DB)
        """
        dag_dict = pdata.dag.model_dump()
        necessary = ConfigParser(dag_dict, pdata.pipeline_id).parse_graph()
        meta = pdata.model_dump()
        meta.pop("dag", None)
        merged = {**necessary, **meta}

        # Generate RabbitMQ config and store in pipeline_config (not file)
        generator = PipelineGenerator(merged)
        rmq_result = generator.generate_rabbitmq_config(write_file=False)

        # Add topology and manifest to the config document
        merged["rabbitmq_topology"] = rmq_result["topology"]
        merged["rabbitmq_yaml_manifest"] = rmq_result["yaml_manifest"]

        return merged

    
    @with_backoff(msg="pipeline.get_final_config")
    def get_final_config(self, username: str, pipeline_id: str) -> dict:
        """
        Retrieve the ‘pipeline_config::{username}::{pipeline_id}’ document
        from Couchbase, exactly as your old helper did.

        :raises HTTPException(404): if not found
        :raises HTTPException(500): on any Couchbase error
        """

        doc_key = PIPELINE_CONFIG_FMT.format(
            username=username,
            pipeline_id=pipeline_id
        )
        try:
            # borrow a bundle and get the right collection
            with cast(ContextManager[KVCollection], self._pool.collections(CONFIG_COLLECTION_KEY)) as coll:
                result = coll.get(doc_key)
        except DocumentNotFoundException:
            logger.error("Pipeline config '%s' not found", doc_key)
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline config '{pipeline_id}' not found."
            )
        except CouchbaseException as e:
            logger.error("Failed to retrieve pipeline config '%s': %s", doc_key, e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Internal server error retrieving configuration."
            )

        if result is None:
            logger.error("Pipeline config '%s' not found", doc_key)
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline config '{pipeline_id}' not found."
            )
        config_data : dict = result.content_as[dict]
        if not config_data:
            logger.error("Empty configuration for pipeline_id=%s (key=%s)",
                         pipeline_id, doc_key)
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline config '{pipeline_id}' is empty."
            )
        logger.info("Retrieved pipeline config '%s'", doc_key)
        return config_data
    
    # src/agentcy/orchestrator_core/stores/pipeline_store.py
    @with_backoff(msg="pipeline.list_runs_all")
    def list_runs(self, username: str, pipeline_id: str):
        """Return run-IDs from the hot bucket *and* the archive bucket."""
        from agentcy.orchestrator_core.couch.config import (
            EPHEMERAL_COLLECTIONS, CB_COLLECTIONS, CNames, CB_BUCKET, CB_BUCKET_EPHEMERAL, CB_SCOPE
        )

        prefix = RUN_KEY_FMT.format(username=username, pipeline_id=pipeline_id, run_id="")

        hot_coll  = EPHEMERAL_COLLECTIONS[CNames.PIPELINE_RUNS_EPHEMERAL]
        cold_coll = CB_COLLECTIONS[CNames.PIPELINE_RUNS]

        queries = [
            (CB_BUCKET_EPHEMERAL, hot_coll),
            (CB_BUCKET,          cold_coll),
        ]

        ids: list[str] = []
        bundle = self._pool.acquire()
        try:
            for bucket_name, coll_name in queries:
                q = (
                    f"SELECT RAW META(r).id "
                    f"FROM `{bucket_name}`.`{CB_SCOPE}`.`{coll_name}` AS r "
                    f"WHERE META(r).id LIKE '{prefix}%' "
                    f"ORDER BY META(r).id"
                )
                ids.extend(bundle.cluster.query(q))
        finally:
            self._pool.release(bundle)

        return [doc_id[len(prefix):] for doc_id in ids]

        




def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
