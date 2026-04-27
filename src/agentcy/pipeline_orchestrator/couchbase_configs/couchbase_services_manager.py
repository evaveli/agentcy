#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_services_manager.py

from datetime import datetime
import uuid
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_connection_manager import CouchbaseConnectionManager
import logging
from couchbase.exceptions import CouchbaseException
from agentcy.pydantic_models.service_registration_model import ServiceRegistration

class ServiceDocumentManager:

    def __init__(self, cb_manager: CouchbaseConnectionManager):
        self.cb_manager = cb_manager
        self.collection = self.cb_manager.agents_collection

    def create_service_document(self,username: str, service_data: ServiceRegistration) -> str:
        """
        Creates a new service document in the 'services' collection.

        :param service_data: A dictionary containing service-specific data.
        :param agent_id: The ID of the agent associated with this service.
        :return: The ID of the created document.
        """
        try:
            service_id = service_data.service_id
            document_name =  f"service::{username}::{service_id}"
            result = self.collection.upsert(document_name, service_data.model_dump(mode="json"))
            logging.info(f"Service document '{service_id}' created/updated successfully.")
            return service_id

        except CouchbaseException as e:
            logging.error(f"Failed to create service document: {e}")
            raise
        except Exception as e:
            logging.error(f"Failed to create service document: {e}")

    
    def update_service_document(self,username: str, service_id: uuid, service_data: ServiceRegistration) -> None:
        """
        Creates a new service document in the 'services' collection.

        :param service_data: A dictionary containing service-specific data.
        :param agent_id: The ID of the agent associated with this service.
        :return: The ID of the created document.
        """
        #FLAG 2 the updating can be further optimized 
        try:
           doc_key = f"service::{username}::{service_id}"
           self.collection.upsert(doc_key, service_data.model_dump(mode="json"))
           logging.info(f"Upserted service document with key")
        except CouchbaseException as e:
            logging.error(f"Failed to create service document: {e}")
            raise

    def read_service_document(self, username: str, service_id: str) -> None:

        """
        Reads (retrieves) a service document by the computed key.

        :param username: Username that forms part of the doc key.
        :param service_id: service ID that forms part of the doc key.
        :return: The service document as a dictionary.
        """
        doc_key = f"service::{username}::{service_id}"

        try:
            result = self.collection.get(doc_key)
            doc_content = result.content_as[dict]
            logging.info(f"Retrieved service doc with key: {doc_key}")
            return doc_content
        except CouchbaseException as e:
            logging.error(f"Failed to read service '{doc_key}': {e}")
            raise
    
    def delete_service_document(self, username: str, service_id: str) -> None:
        """
        Deletes a service document by key.

        :param username: Username that forms part of the doc key.
        :param service_id: service ID that forms part of the doc key.
        """
        doc_key = f"service::{username}::{service_id}"

        try:
            self.collection.remove(doc_key)
            logging.info(f"Deleted service doc with key: {doc_key}")
        except CouchbaseException as e:
            logging.error(f"Failed to delete service '{doc_key}': {e}")
            raise
    
    def list_all_services(self, username: str):
        """
        Retrieves all service documents for a given user by scanning
        document keys that match 'service::{username}::%'.
        
        :param username: The username whose services we want to list.
        :return: A list of service documents (as dicts).
        """
        # Build the prefix we’ll match in doc IDs.
        prefix = f"service::{username}::"

        try:
            # Use N1QL to find all docs that have a doc key matching that prefix.
            # Adjust bucket/scope/collection names based on your environment.
            query = f"""
                SELECT META(p).id AS doc_id, p.service_name
                FROM `{self.cb_manager.bucket_name}`.`{self.cb_manager.scope_name}`.`agents` p
                WHERE META(p).id LIKE '{prefix}%'
            """

            # Run the query and gather results.
            rows = self.cb_manager.cluster.query(query)
            services = []
            for row in rows:
                doc_id = row["doc_id"]
                service_id = doc_id[len(prefix):]  # Remove the prefix to get just the name.
                service_name = row.get("service_name")
                services.append({
                "service_id": service_id,
                "service_name": service_name
            })

            logging.info(f"Found {len(services)} service documents for user '{username}'.")
            return services
        except CouchbaseException as e:
            logging.error(f"Failed to list services for user '{username}': {e}")
            raise


