#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_pipelines_manager.py

from datetime import datetime, timezone
import json
import os
import uuid

from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_pool import DynamicCouchbaseConnectionPool
from agentcy.parsing_layer.generate_rabbitmq_manifests import PipelineGenerator
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import PipelineConfig
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import Metadata, PipelineRun, PipelineStatus, TaskState, TaskStatus
import logging
from couchbase.exceptions import CouchbaseException, DocumentNotFoundException, TransactionFailed, TransactionCommitAmbiguous
from couchbase.transactions import AttemptContext, Transactions
from couchbase.durability import DurabilityLevel
from agentcy.rabbitmq_workflow.workflow_config_parser import ConfigParser
from agentcy.pydantic_models.pipeline_validation_models.pipeline_payload_model import BackoffStrategy, Orchestration, PipelinePayload, Security, Tasks, TriggerProtocol, RetryPolicy
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_connection_manager import CouchbaseConnectionManager
# Configure logging for debugging and monitoring
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class PipelineDocumentManager:

    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self.pool = pool


    def _generate_doc_key(self, doc_type: str, username: str, pipeline_id: str, extra: str = "") -> str:
        """
        Centralized key generator for different types of documents.

        :param doc_type: Type of document. For example: 'pipeline', 'pipeline_run', 'pipeline_config', etc.
        :param username: Username forming part of the key.
        :param pipeline_id: Pipeline identifier.
        :param extra: Additional identifier if needed (e.g., pipeline run id).
        :return: The document key as a string.
        """
        if doc_type == "pipeline":
            return f"pipeline::{username}::{pipeline_id}"
        elif doc_type == "pipeline_run":
            if extra is None:
                raise ValueError("Extra identifier required for pipeline_run key.")
            return f"pipeline_run::{username}::{pipeline_id}::{extra}"
        elif doc_type == "pipeline_config":
            return f"pipeline_config::{username}::{pipeline_id}"
        elif doc_type == "pipeline_versioning":
            return f"pipeline_versioning::{username}::{pipeline_id}"
        # Add more types as needed
        else:
            raise ValueError(f"Unknown document type: {doc_type}")


    def _get_collection(self, conn: CouchbaseConnectionManager, collection_name: str):
        """
        Retrieve the already-initialized Collection object by name.
        Raises a KeyError if the collection_name was not found in self.collections_map.
        """
        if collection_name not in conn.collections_map:
            raise KeyError(f"No collection named '{collection_name}' is initialized in collections_map.")
        return conn.collections_map[collection_name]

    def _handle_transaction_error(self, e: Exception):
        """
        Centralized logging and re-raising for transaction-related errors.
        """
        if isinstance(e, TransactionFailed):
            logger.error(f"Transaction failed: {e}")
        elif isinstance(e, TransactionCommitAmbiguous):
            logger.warning(f"Transaction commit ambiguous: {e}")
            # Additional logic if you want to handle ambiguous commits
        elif isinstance(e, CouchbaseException):
            logger.error(f"Couchbase exception during transaction: {e}")
        else:
            logger.error(f"Unexpected error during transaction: {e}")
        raise e

    def _get_pipeline_doc_key(self, username: str, pipeline_id: str) -> str:
        """Generates the document key for a pipeline."""
        return f"pipeline::{username}::{pipeline_id}"
    
    
    def create_pipeline_document(self,username: str, pipeline_data: PipelineConfig) -> str:
        """
        Creates a new pipeline document in the 'pipelines' collection.

        :param pipeline_data: A dictionary containing pipeline-specific data.
        :param agent_id: The ID of the agent associated with this pipeline.
        :return: The ID of the created document.
        """
        # Generate a unique pipeline ID if not provided
        pipeline_id = uuid.uuid4()
        document_name =  f"pipeline::{username}::{pipeline_id}"
        updated_pipeline_data = pipeline_data.model_copy(
        update={
            "pipeline_id": str(pipeline_id),
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        })

        try:
            with self.pool.connection() as conn:
                pipelines_collection = conn.pipelines_collection
                pipelines_collection.upsert(document_name, updated_pipeline_data.model_dump(mode="json"))
            logger.info(f"Pipeline document '{pipeline_id}' created/updated successfully.")
            return pipeline_id
        except DocumentNotFoundException as e:
            logging.error(f"Document not found: {e}")
            raise

        except CouchbaseException as e:
            logging.error(f"Failed to create pipeline document: {e}")
            raise

    
    def read_pipeline_document(self, username: str, pipeline_id: str) -> None:

        """
        Reads (retrieves) a pipeline document by the computed key.

        :param username: Username that forms part of the doc key.
        :param pipeline_id: Pipeline ID that forms part of the doc key.
        :return: The pipeline document as a dictionary.
        """
        doc_key = self._get_pipeline_doc_key(username, pipeline_id)

        try:
            with self.pool.connection() as conn:
                pipelines_collection = conn.pipelines_collection
                result = pipelines_collection.get(doc_key)
                doc_content = result.content_as[dict]
            logger.info(f"Retrieved pipeline doc with key: {doc_key}")
            return doc_content
        except CouchbaseException as e:
            logger.error(f"Failed to read pipeline '{doc_key}': {e}")
            raise
    
    def read_pipeline_run(self, username: str, pipeline_id: str, pipeline_run_id: str):
        doc_key = f"pipeline_run::{username}::{pipeline_id}::{pipeline_run_id}"
        try:
            with self.pool.connection() as conn:
                pipeline_runs_collection = self._get_collection(conn, os.getenv("PIPELINE_RUNS_COLLECTION", "pipeline_runs_collection"))
                result = pipeline_runs_collection.get(doc_key)
                doc_content = result.content_as[dict]
            logger.info(f"Retrieved pipeline doc with key: {doc_key}")
            return doc_content
        except CouchbaseException as e:
            logger.error(f"Failed to read pipeline run '{doc_key}': {e}")
            raise

    def delete_pipeline_document(self, username: str, pipeline_id: str) -> None:
        """
        Deletes a pipeline document by key.

        :param username: Username that forms part of the doc key.
        :param pipeline_id: Pipeline ID that forms part of the doc key.
        """
        doc_key = self._get_pipeline_doc_key(username, pipeline_id)
        try:
            with self.pool.connection() as conn:
                pipelines_collection = conn.pipelines_collection
                pipelines_collection.remove(doc_key)
            logger.info(f"Deleted pipeline doc with key: {doc_key}")
        except CouchbaseException as e:
            logger.error(f"Failed to delete pipeline '{doc_key}': {e}")
            raise
    
    def list_all_pipelines(self, username: str):
        """
        Retrieves all pipeline documents for a given user by scanning
        document keys that match 'pipeline::{username}::%'.
        
        :param username: The username whose pipelines we want to list.
        :return: A list of pipeline documents (as dicts).
        """
        # Build the prefix we’ll match in doc IDs.
        prefix = f"pipeline::{username}::"

        try:
            # Use N1QL to find all docs that have a doc key matching that prefix.
            # Adjust bucket/scope/collection names based on your environment.
            with self.pool.connection() as conn:
                bucket_name = conn.bucket_name
                scope_name = conn.scope_name
                query = f"""
                    SELECT META(p).id AS doc_id, p.pipeline_name
                    FROM `{bucket_name}`.`{scope_name}`.`pipelines` p
                    WHERE META(p).id LIKE '{prefix}%'
                """

                # Run the query and gather results.
                rows = conn.cluster.query(query)
                pipelines= []
                for row in rows:
                    doc_id = row["doc_id"]
                    pipeline_id = doc_id[len(prefix):]
                    pipeline_name = row.get("pipeline_name")
                    pipelines.append({
                        "pipeline_id": pipeline_id,
                        "pipeline_name": pipeline_name
                    })

            logger.info(f"Found {len(pipelines)} pipeline documents for user '{username}'.")
            return pipelines
        except CouchbaseException as e:
            logging.error(f"Failed to list pipelines for user '{username}': {e}")
            raise

    
    def _persist_pipeline(self, username: str, pipeline_id: str, pipeline_data: PipelineConfig, action: str, enable_versioning: bool) -> None:
        pipeline_doc_key = self._get_pipeline_doc_key(username, pipeline_id)
        pipeline_config_key = f"pipeline_config::{username}::{pipeline_id}"

        with self.pool.connection() as conn:
            pipelines_collection = conn.pipelines_collection
            pipeline_versioning_collection = self._get_collection(conn, os.getenv("PIPELINE_VERSIONING_COLLECTION", "pipeline_versioning"))
            pipeline_config_collection = self._get_collection(conn, os.getenv("PIPELINE_CONFIG_COLLECTION", "pipeline_config_collection"))
            pipeline_config_versioning_collection = self._get_collection(conn, os.getenv("PIPELINE_CONFIG_VERSIONING_COLLECTION", "pipeline_config_versioning_collection"))
            pipeline_runs_collection = self._get_collection(conn, os.getenv("PIPELINE_RUNS_COLLECTION", "pipeline_runs_collection"))
            pipeline_runs_versioning_collection = self._get_collection(conn, os.getenv("PIPELINE_RUNS_VERSIONING_COLLECTION", "pipeline_runs_versioning_collection"))

            
            def txn_logic(ctx: AttemptContext):
                logger.info(f"Starting {action} transaction for pipeline='{pipeline_id}' (user='{username}').")

                if action == "UPDATE":
                    existing_pipeline_doc = ctx.get(pipelines_collection, pipeline_doc_key)
                    existing_doc_content = existing_pipeline_doc.content_as[dict]

                    current_version = existing_doc_content.get("version", 1)
                    pipeline_version = current_version + 1

                    if enable_versioning:
                        # Store old pipeline doc in the versioning collection
                        versioned_pipeline_key = (
                            f"{pipeline_doc_key}::v{current_version}::"
                            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                        )
                        ctx.insert(pipeline_config_versioning_collection, versioned_pipeline_key, existing_doc_content)
                        logger.info(f"Inserted pipeline version backup: {versioned_pipeline_key}")
                else:
                    # CREATE: just start at version=1
                    pipeline_version = 1

                # Convert the PipelineConfig into a dictionary for storage
                pipeline_doc_dict = self.convert_datetimes(pipeline_data.model_dump())
                pipeline_doc_dict["pipeline_id"] = pipeline_id
                pipeline_doc_dict["version"] = pipeline_version
                pipeline_doc_dict["last_updated"] = datetime.now(timezone.utc).isoformat()

                # Insert or replace pipeline doc
                if action == "CREATE":
                    ctx.insert(pipelines_collection, pipeline_doc_key, pipeline_doc_dict)
                    logger.info(f"Inserted new pipeline doc: {pipeline_doc_key}")
                else:
                    ctx.replace(existing_pipeline_doc, pipeline_doc_dict)
                    logger.info(f"Replaced existing pipeline doc: {pipeline_doc_key}")

                # Generate the pipeline config (task_graph, etc.) from the model
                new_config = self._generate_pipeline_config(pipeline_data)
                
                # For versioning of the pipeline config doc
                if action == "UPDATE" and enable_versioning:
                    old_config_result = ctx.get(pipeline_config_collection, pipeline_config_key)
                    old_config = old_config_result.content_as[dict]
                    old_config_version = old_config.get("version", 1)

                    versioned_config_key = (
                        f"{pipeline_config_key}::v{old_config_version}::"
                        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                    )
                    ctx.insert(pipeline_config_versioning_collection, versioned_config_key, old_config)
                    logger.info(f"Inserted old pipeline config version backup: {versioned_config_key}")

                    # Replace with new config
                    # You might want to track a version in the config doc as well
                    new_config["version"] = old_config_version + 1
                    ctx.replace(old_config_result, new_config)
                    logger.info(f"Replaced pipeline config: {pipeline_config_key}")
                else:
                    # CREATE, or UPDATE without versioning
                    new_config["version"] = 1  # or pipeline_version if you prefer to keep them in sync
                    ctx.insert(pipeline_config_collection, pipeline_config_key, new_config)
                    logger.info(f"Inserted new pipeline config: {pipeline_config_key}")

                # On CREATE, generate a pipeline run doc with an initial state
                if action == "CREATE":
                    pipeline_run_id = str(uuid.uuid4())
                    pipeline_run_key = f"pipeline_run::{username}::{pipeline_id}::{pipeline_run_id}"

                    # Because we just generated new_config, we know it has a "task_graph"
                    task_dict = new_config.get("task_dict")
                    if not task_dict:
                        
                        logger.warning("No 'task_graph' found in the generated config. Pipeline run will be empty.")
                        task_dict = {}

                    new_pipeline_run_data = self._generate_pipeline_run(username=username, pipeline_run_config_id=pipeline_run_id, task_dict=task_dict)
                    jsonify_pipeline_run_data = json.loads(new_pipeline_run_data)
                    ctx.insert(pipeline_runs_collection, pipeline_run_key, jsonify_pipeline_run_data)
                    logger.info(f"Inserted pipeline run doc: {pipeline_run_key}")

                logger.info(f"{action} transaction for pipeline='{pipeline_id}' completed successfully.")

            # Execute the transaction
            try:
                conn.cluster.transactions.run(txn_logic)
            except (TransactionFailed, TransactionCommitAmbiguous, CouchbaseException) as e:
                self._handle_transaction_error(e)
            except Exception as e:
                logger.error(f"Unexpected error during pipeline persistence: {e}")
                raise
    
    def _find_final_tasks(self, task_dict: dict) -> set:
        """
        Returns the set of task IDs where 'inferred_outputs' is empty.
        """
        final_tasks = set()
        for tid, data in task_dict.items():
            outputs = data.get("inferred_outputs", [])
            if not outputs:  # out-degree = 0
                final_tasks.add(tid)
        return final_tasks


    def _generate_pipeline_run(self, username, pipeline_run_config_id, task_dict: dict):
        final_task_ids = self._find_final_tasks(task_dict)
        tasks_list = []
        for task_id in task_dict.keys():
            details = task_dict.get(task_id, {})
            service = details.get("available_services")
            action = details.get("action")
            expected_response_time = os.getenv("AGENT_RESPONSE_TIME", 30)
            retry_policy = RetryPolicy(
                max_retries=os.getenv("MAX_RETRIES", 3),
                backoff_strategy=BackoffStrategy.FIXED.name
            )


            task_obj = Tasks(
                task_id=task_id,
                service=service,
                action=action,
                expected_response_time=expected_response_time,
                retry_policy=retry_policy,
                is_final_task=(task_id in final_task_ids)
            )
            
            tasks_list.append(task_obj)
            logger.info(f"Created Tasks object for task_id: {task_id}")
        
        orchestration_obj = Orchestration(
        tasks=tasks_list,
        security=Security(access_token=os.getenv("MICROSERVICE_COMMUNICATION_ACCESS_TOKEN"))
    )

        pipeline_run = PipelinePayload(
            schema_version = os.getenv("PIPELINE_RUN_SCHEMA_VERSION", "v1.0.0"),
            pipeline_run_config_id = pipeline_run_config_id,
            origin = username,
            trigger_protocol = TriggerProtocol.HTTP.value,
            timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
            orchestration = orchestration_obj
        )

        return pipeline_run.model_dump_json()

    
    def _generate_pipeline_config(self, pipeline_data: PipelineConfig):
        print("Generating pipeline config")
        fetch_dag = pipeline_data.dag
        # Get the complete pipeline metadata (which includes 'pipeline_id')
        pipeline_metadata = self.convert_datetimes(pipeline_data.model_dump())
        # Get the tasks configuration from the dag only
        tasks_config = self.convert_datetimes(fetch_dag.model_dump())
        pipeline_id = pipeline_metadata['pipeline_id']
        necessary_info = ConfigParser(tasks_config, pipeline_id).parse_graph()
        # Remove the 'dag' field if you don't need it in the final config
        pipeline_metadata.pop('dag', None)
        # Merge the dictionaries
        merged_config = {**necessary_info, **pipeline_metadata}
        # Convert the merged configuration to ensure all non-serializable objects (like sets) are converted
        final_config = self.convert_datetimes(merged_config)
        PipelineGenerator(final_config).generate_rabbitmq_config()
        return final_config




    def create_pipeline(self, username: str, pipeline_data: PipelineConfig) -> str:
        pipeline_id = str(uuid.uuid4())
        # Update pipeline_data to include the generated pipeline_id and timestamps.
        pipeline_data = pipeline_data.model_copy(update={
            "pipeline_id": pipeline_id,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
            "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        })
        self._persist_pipeline(
            username=username,
            pipeline_id=pipeline_id,
            pipeline_data=pipeline_data,
            action="CREATE",
            enable_versioning=True
        )
        return pipeline_id


    def update_pipeline(self, username: str, pipeline_id: str, pipeline_data: PipelineConfig):
        """
        Convenience method to update an existing pipeline with versioning.

        :param username: Owner username.
        :param pipeline_id: The pipeline ID to update.
        :param pipeline_data: Updated PipelineConfig data.
        """
        self._persist_pipeline(
            username=username,
            pipeline_id=pipeline_id,
            pipeline_data=pipeline_data,
            action="UPDATE",
            enable_versioning=True
        )


    def convert_datetimes(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, set):
            # Convert set to list and recursively process each element.
            return [self.convert_datetimes(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: self.convert_datetimes(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_datetimes(item) for item in obj]
        else:
            return obj