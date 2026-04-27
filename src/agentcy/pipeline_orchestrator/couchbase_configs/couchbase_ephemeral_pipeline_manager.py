#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_ephemeral_pipeline_manager.py
import logging
from typing import Optional, Dict, Any
import os

import rich
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_pool import DynamicCouchbaseConnectionPool
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState


logger = logging.getLogger(__name__)

class EphemeralPipelineDocumentManager:
    """
    Manages ephemeral pipeline run documents in Couchbase.
    
    Uses the ephemeral connection pool (a DynamicCouchbaseConnectionPool instance)
    from ResourceManager to perform fast, transient read/update operations on pipeline
    run documents stored in the ephemeral bucket.
    """

    def __init__(self, pool: DynamicCouchbaseConnectionPool) -> None:

        """
        Initialize the manager with the ephemeral connection pool.
        
        :param pool: A DynamicCouchbaseConnectionPool instance configured for ephemeral connections.
        """
        self.pool = pool
        self.collection_name = os.getenv("PIPELINE_RUNS_COLLECTION", "pipeline_runs_collection_ephemeral")
        self.document_prefix = os.getenv("PIPELINE_RUN_DOC_PREFIX", "pipeline_run::")
        self.large_outputs_collection = os.getenv("EPHEMERAL_LARGE_OUTPUTS", "ephemeral_large_outputs")
        logger.info(f"EphemeralPipelineDocumentManager initialized for collection '{self.collection_name}'.")

    def _document_key(self, run_id: str, username: str, pipeline_id) -> str:

        """
        Generate the document key for the given pipeline run ID.
        """
        return f"{self.document_prefix}::{username}::{pipeline_id}::{run_id}"

    def read_pipeline_document(self, username: str, run_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:

        """
        Read a pipeline run document from the ephemeral bucket.
        
        :param run_id: The pipeline run identifier.
        :return: A dictionary representing the document, or None if not found.
        """
        doc_key = self._document_key(run_id=run_id, username=username, pipeline_id=pipeline_id)
        try:
            with self.pool.connection(timeout=10.0) as conn:
                # Access the ephemeral bucket's collection.
                # Adjust the following line to match your Couchbase connection API.
                collection = conn.bucket.scope(conn.scope_name).collection(self.collection_name)
                result = collection.get(doc_key)
                doc = result.content_as[dict]
                logger.info(f"Successfully read ephemeral document for run '{run_id}'.")
                return doc
        except Exception as e:
            logger.error(f"Error reading ephemeral document for run '{run_id}': {e}", exc_info=True)
            return None

    def update_pipeline_run(self, username: str, run_id: str, updated_doc: Dict[str, Any], pipeline_id: str) -> None:
        """
        Update (or replace) a pipeline run document in the ephemeral bucket.
        
        :param run_id: The pipeline run identifier.
        :param updated_doc: The updated document data as a dictionary.
        """
        doc_key = self._document_key(run_id=run_id, username=username, pipeline_id=pipeline_id)
        try:
            with self.pool.connection(timeout=10.0) as conn:
                collection = conn.bucket.scope(conn.scope_name).collection(self.collection_name)
                # Replace the document with the updated version.
                collection.replace(doc_key, updated_doc)
                logger.info(f"Successfully updated ephemeral document for run '{run_id}'.")
        except Exception as e:
            logger.error(f"Error updating ephemeral document for run '{run_id}': {e}", exc_info=True)

    def create_pipeline_run(self,username, run_id: str, pipeline_run_model, pipeline_id) -> None:
        """
        Insert or upsert a brand-new pipeline run document into the ephemeral bucket.

        :param run_id: The pipeline run identifier (must be unique).
        :param pipeline_run: A PipelineRun pydantic model or dict representing initial run state.
        """
        doc_key = self._document_key(run_id, username, pipeline_id)
        doc_data = (
            pipeline_run_model.model_dump() if hasattr(pipeline_run_model, "model_dump") 
            else dict(pipeline_run_model)
        )
        try:
            with self.pool.connection(timeout=10.0) as conn:
                collection = conn.bucket.scope(conn.scope_name).collection(self.collection_name)
                collection.insert(doc_key, doc_data)

                logger.info(
                    f"Successfully created ephemeral pipeline document for run '{run_id}' with key '{doc_key}'."
                )
        except Exception as e:
            logger.error(f"Error creating ephemeral document for run '{run_id}': {e}", exc_info=True)
            # Raise or handle error as you see fit
            raise

    def read_task_output(self,doc_id:str, pipeline_run_id: str, task_id: str):

        with self.pool.connection() as conn:
            logger.info("Retrieving ephemeral output from doc_id=%s", doc_id)
            try:
                collection = conn.bucket.scope(conn.scope_name).collection(self.large_outputs_collection)
                result = collection.get(doc_id)
                logger.info("Read ephemeral output for run_id=%s, task_id=%s => %s", pipeline_run_id, task_id, result.content_as[dict])
                return result.content_as[dict]
            except Exception as e:
                # e.g., DocumentNotFoundException or other
                logger.warning("No ephemeral output found for doc_id=%s, returning empty dict. Error=%s", doc_id, e)
                return {}

    def store_task_output(self, pipeline_run_id: str, task_id: str, task_state: TaskState) -> None:
        """
        Store only the 'data' field from the TaskState for a given pipeline_run_id and task_id.
        
        The document key is constructed using the username, task_id, and pipeline_run_id.
        Only the contents of the task_state.data dictionary are persisted.
        """
        doc_id = f"task_output::{task_state.username}::{task_id}::{pipeline_run_id}"
        try:
            with self.pool.connection(timeout=10.0) as conn:
                collection = conn.bucket.scope(conn.scope_name).collection(self.collection_name)
                # Extract just the data from the task state.
                data_to_store = task_state.data
                collection.insert(doc_id, data_to_store)
                logger.info(
                    f"Successfully stored task output for run '{pipeline_run_id}' with key '{doc_id}'."
                )
        except Exception as e:
            logger.error(f"Error storing task output for run '{doc_id}': {e}", exc_info=True)
            raise
