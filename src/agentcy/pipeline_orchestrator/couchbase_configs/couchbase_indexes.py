#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_indexes.py

from datetime import timedelta
import logging
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_connection_manager import CouchbaseConnectionManager
from couchbase.exceptions import (
    AuthenticationException,
    QueryIndexAlreadyExistsException,
    CouchbaseException,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IndexManager:

    def __init__(self, cb_manager: CouchbaseConnectionManager):
        self.cb_manager = cb_manager
        self.cluster = self.cb_manager.cluster
        self.bucket_name = self.cb_manager.bucket_name
        

    
    def ensure_primary_index(self):
        """
        Ensure that a primary index exists on the specified bucket.
        """
        index_manager = self.cluster.query_indexes()
        try:
            query = f"SELECT * FROM system:indexes WHERE keyspace_id = '{self.bucket_name}' AND is_primary = true;"
            result = self.cluster.query(query).rows()

            if any(result):
                logger.info(f"Primary index already exists on bucket '{self.bucket_name}'.")
            else:
                logger.info(f"No primary index found on bucket '{self.bucket_name}'. Creating one...")
                index_manager.create_primary_index(
                    self.bucket_name,
                    ignore_if_exists=True,
                    timeout=timedelta(seconds=5),
                )
                logger.info(f"Primary index created on bucket '{self.bucket_name}'.")
        except QueryIndexAlreadyExistsException:
            logger.warning(f"Primary index on bucket '{self.bucket_name}' already exists.")
        except CouchbaseException as e:
            logger.error(f"Error ensuring primary index: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ensure_primary_index: {e}")
            raise

    def ensure_secondary_index_services(self):
        """
        Ensures that necessary indexes exist.
        Creates index if it does not already exist.
        """

        index_name = "idx_service_lookup"
        fields = ["META().id", "service_name"]

        try:
            index_manager = self.cluster.query_indexes()
            existing_indexes = index_manager.get_all_indexes(self.bucket_name)

            if not any(idx.name == index_name for idx in existing_indexes):
                index_manager.create_index(
                    self.bucket_name,
                    index_name,
                    fields,
                    ignore_if_exists=True,
                    timeout=timedelta(seconds=10)
                )
                logger.info(f"Created index '{index_name}' on bucket '{self.bucket_name}'.")
            else:
                logger.info(f"Index '{index_name}' already exists.")
        except QueryIndexAlreadyExistsException:
            logger.warning(f"Secondary index on bucket '{self.bucket_name}' already exists.")
        except CouchbaseException as e:
            logger.error(f"Error ensuring secondary index: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ensure_primary_index: {e}")
            raise


    def ensure_secondary_index_pipelines(self):
        """
        Ensures that necessary indexes exist.
        Creates index if it does not already exist.
        """

        index_name = "idx_pipeline_lookup"
        fields = ["META().id", "pipeline_name"]

        try:
            index_manager = self.cluster.query_indexes()
            existing_indexes = index_manager.get_all_indexes(self.bucket_name)

            if not any(idx.name == index_name for idx in existing_indexes):
                index_manager.create_index(
                    self.bucket_name,
                    index_name,
                    fields,
                    ignore_if_exists=True,
                    timeout=timedelta(seconds=10)
                )
                logger.info(f"Created index '{index_name}' on bucket '{self.bucket_name}'.")
            else:
                logger.info(f"Index '{index_name}' already exists.")
        except QueryIndexAlreadyExistsException:
            logger.warning(f"Secondary index on bucket '{self.bucket_name}' already exists.")
        except CouchbaseException as e:
            logger.error(f"Error ensuring secondary index: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ensure_primary_index: {e}")
            raise