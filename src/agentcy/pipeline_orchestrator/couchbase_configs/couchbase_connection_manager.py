#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_connection_manager.py

import os
from couchbase.cluster import Cluster, ClusterOptions
from couchbase.auth import PasswordAuthenticator
from couchbase.exceptions import (
    AuthenticationException,
    CouchbaseException,
)
from datetime import timedelta
from dotenv import load_dotenv
import logging

from agentcy.orchestrator_core.couch.config import (
    CB_CONN_STR,
    CB_USER,
    CB_PASS,
    CB_BUCKET,
    CB_SCOPE,
    CB_COLLECTIONS,
    EPHEMERAL_COLLECTIONS,
)

# Load environment variables from .env file (do not override provided envs)
load_dotenv(override=False)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CouchbaseConnectionManager:

    def __init__(self, classic: bool = False, ephemeral: bool = False):
        # Retrieve environment variables
        self.ephemeral = ephemeral
        if self.ephemeral:
            self.bucket_name = os.getenv("CB_EPHEMERAL_BUCKET_NAME", "pipeline_runs")
            logger.info("Using ephemeral bucket for pipeline runs.")
            self.ephemeral_collections = list(EPHEMERAL_COLLECTIONS.values())
            self.collection_names = list(self.ephemeral_collections)
        else:
            self.bucket_name = os.getenv("CB_BUCKET", CB_BUCKET)
            self.collection_names = list(CB_COLLECTIONS.values())

        self.connection_string = os.getenv("CB_CONN_STR", CB_CONN_STR)
        self.username = os.getenv("CB_USER", os.getenv("CB_USERNAME", CB_USER))
        self.password = os.getenv("CB_PASS", os.getenv("CB_PASSWORD", CB_PASS))
        self.scope_name = os.getenv("CB_SCOPE", os.getenv("CB_SCOPE_NAME", CB_SCOPE))
        
        # Initialize Cluster
        try:
            auth = PasswordAuthenticator(self.username, self.password)
            self.cluster = Cluster.connect(self.connection_string, ClusterOptions(auth))
            self.cluster.wait_until_ready(timedelta(seconds=10))
            logger.info("Connected to Couchbase cluster successfully.")
        except AuthenticationException as e:
            logger.error(f"Authentication failed: {e}")
            raise
        except CouchbaseException as e:
            logger.error(f"Failed to connect to Couchbase: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during Couchbase connection: {e}")
            raise

        # Access Bucket
        self.bucket = self.cluster.bucket(self.bucket_name)

        # Initialize Managers
        self.query_manager = self.cluster.query_indexes()
        # Ensure Primary Index Exists
        # Access Default Collection
        self.default_collection = self.bucket.default_collection()
        self.collection_manager = self.bucket.collections()
        self.pipelines_collection = self.bucket.scope(self.scope_name).collection("pipelines")
        self.agents_collection = self.bucket.scope(self.scope_name).collection("agents")
        self.pipeline_configs_collection = self.bucket.scope(self.scope_name).collection("pipeline_config_collection")
        self.collections_map = {}
        self._ensure_core_collections()
        self._init_collections()  # Create references based on self.collection_names
        
        logger.info("Accessed default collection successfully.")
        logger.info("Accessed default collection successfully.")

    def _ensure_collection(self, collection_name: str) -> None:
        """
        Create a collection if it does not exist (ignore 'already exists' errors).
        """
        try:
            self.collection_manager.create_collection(collection_name=collection_name, scope_name=self.scope_name)
            logger.info("Collection '%s' created (scope=%s).", collection_name, self.scope_name)
        except CouchbaseException as e:
            if "already exists" in str(e):
                logger.info("Collection '%s' already exists.", collection_name)
            else:
                logger.error("Failed to create collection '%s': %s", collection_name, e)
                raise

    def _ensure_core_collections(self) -> None:
        """
        Ensure base collections used throughout the orchestrator exist.
        """
        for cname in ("pipelines", "agents", "pipeline_config_collection"):
            self._ensure_collection(cname)
        if self.ephemeral:
            cols = (
                [c.strip() for c in self.ephemeral_collections.split(",") if c.strip()]
                if isinstance(self.ephemeral_collections, str)
                else self.ephemeral_collections
            )
        else:
            cols = self.collection_names
        for cname in cols:
            self._ensure_collection(cname)


    def _init_collections(self):
        """
        Create references for every collection specified in self.collection_names.
        Store them in a dictionary for easy retrieval.
        """
        for collection_name in self.collection_names:
            # It's good practice to handle potential exceptions in case scope/collection doesn't exist
            try:
                self.collections_map[collection_name] = (
                    self.bucket
                        .scope(self.scope_name)
                        .collection(collection_name)
                )
                logger.info(f"Collection reference for '{collection_name}' initialized.")
            except CouchbaseException as e:
                logger.error(f"Failed to initialize collection '{collection_name}': {e}")
                raise

     
    
    #Flag3 this need to be tested
    def ensure_collections(self):
        """
        Ensures that the required collections exist within the specified scope.
        Creates them if they do not exist.
        """
        if self.ephemeral:
            # ephemeral -> ephemeral_collections
            # Because ephemeral_collections is set in the constructor if ephemeral=True
            # If ephemeral_collections is a single string, we might need to wrap it in a list
            if isinstance(self.ephemeral_collections, str):
                ephemeral_cols = [col.strip() for col in self.ephemeral_collections.split(",") if col.strip()]
            else:
                ephemeral_cols = self.ephemeral_collections
            for collection in ephemeral_cols:
                try:
                    self.collection_manager.create_collection(collection_name=collection, scope_name=self.scope_name)
                    logger.info(f"Collection '{collection}' created.")
                except CouchbaseException as e:
                    if "already exists" in str(e):
                        logger.info(f"Collection '{collection}' already exists.")
                    else:
                        logger.error(f"Failed to create collection '{collection}': {e}")
                        raise
        else:
            # non-ephemeral -> collection_names
            for collection in self.collection_names:
                try:
                    self.collection_manager.create_collection(collection_name=collection, scope_name=self.scope_name)
                    logger.info(f"Collection '{collection}' created.")
                except CouchbaseException as e:
                    if "already exists" in str(e):
                        logger.info(f"Collection '{collection}' already exists.")
                    else:
                        logger.error(f"Failed to create collection '{collection}': {e}")
                        raise



    async def close(self) -> None:
        try:
            if hasattr(self, 'cluster') and self.cluster:
                self.cluster.close()
                logger.info("Couchbase connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing Couchbase connection: {str(e)}")
            raise
