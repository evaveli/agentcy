import logging
import consul
import os
import json
from consul import ConsulException, Check
from agentcy.pydantic_models.service_registration_model import ServiceRegistration
from agentcy.consul_utils.consul_client import get_consul_client
from abc import ABC, abstractmethod
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


CONSUL_HOST = os.getenv("CONSUL_HOST", "localhost")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))

consul_client = consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)


class ServiceRegistry(ABC):
    @abstractmethod
    def register_service(self, service: ServiceRegistration):
        pass

    @abstractmethod
    def deregister_service(self, service_id: str):
        pass

    @abstractmethod
    def discover_service(self, service_name: str):
        pass

    @abstractmethod
    def store_service_schemas(self, service: ServiceRegistration):
        pass

    @abstractmethod
    def store_service_metadata(self, service: ServiceRegistration):
        pass

    @abstractmethod
    def store_service_endpoints(self, service: ServiceRegistration):
        pass

    @abstractmethod
    def store_service_deployment(self, service: ServiceRegistration):
        pass     

    @abstractmethod
    def store_service_authentication(self, service: ServiceRegistration):
        pass    



class ConsulServiceRegistry(ServiceRegistry):

    def __init__(self):
        self.consul_client = None
    
    async def get_consul_client(self):
        try:
            if self.consul_client is None:
                loop = asyncio.get_event_loop()
                self.consul_client = await loop.run_in_executor(None, get_consul_client)
            return self.consul_client
        except Exception as e:
            logger.error(f"Failed to get consul client : {e}")

    async def register_service(self, service: ServiceRegistration):
        try:
            await self.register_service_with_consul(service)
            await self.store_service_schemas(service)
            await self.store_service_metadata(service)
            await self.store_endpoints_in_kv(service)
            await self.store_deployments_in_kv(service)
            await self.store_service_authentication(service)

        except Exception as e:
            logger.error(f"Failed to register service '{service.service_name}': {e}")
            # Attempt to deregister the service to maintain consistency
            try:
                await self.deregister_service(service.service_id)
            except Exception as deregister_error:
                logger.error(f"Failed to deregister service '{service.service_name}' after registration failure: {deregister_error}")
            raise

    async def deregister_service(self, service_id: str):
        try:
            await self.register_service_with_consul(service_id)
            logger.info(f"Service ID '{service_id}' deregistered successfully.")
        except Exception as e:
            logger.error(f"Failed to deregister service ID '{service_id}': {e}")
            raise

    async def discover_service(self, service_name: str):
        try:
            #implement the discovery here maybe? or just go directly
            services = self.discover_service_http(service_name)
            return services
        except Exception as e:
            logger.error(f"Failed to discover service '{service_name}': {e}")
            return []

    async def store_service_schemas(self, service: ServiceRegistration):
        try:
            await self.store_schemas_in_kv(service)
        except Exception as e:
            logger.error(f"Failed to store schemas for service '{service.service_name}': {e}")
            raise

    async def store_service_metadata(self, service: ServiceRegistration):
        try:
            await self.store_metadata_in_kv(service)
        except Exception as e:
            logger.error(f"Failed to store metadata for service '{service.service_name}': {e}")
            raise

    async def store_service_endpoints(self, service: ServiceRegistration):
        try:
            await self.store_metadata_in_kv(service)
        except Exception as e:
            logger.error(f"Failed to store metadata for service '{service.service_name}': {e}")
            raise
    
    async def store_service_deployment(self, service: ServiceRegistration):

        try:
            await self.store_deployments_in_kv(service)
        except Exception as e:
            logger.error(f"Failed to store metadata for service '{service.service_name}': {e}")
            raise
        
    
    async def store_service_authentication(self, service: ServiceRegistration):
        try:
            await self.store_authentication_in_kv(service)
        except Exception as e:
            logger.error(f"Failed to store authentication data for service '{service.service_name}': {e}")
            raise

    async def register_service_with_consul(self,service: ServiceRegistration):
        """
        Registers a service with Consul.

        Args:
            service_data (dict): Dictionary containing service details.
                Required keys:
                    - service_name (str)
                    - service_id (str)
                    - base_url (str)
                Optional keys:
                    - metadata (dict): Additional metadata for the service.
        """
        consul_client = await self.get_consul_client()

        try:
            service_id = str(service.service_id)
            service_name = service.service_name
            address = service.base_url.host
            port = service.base_url.port

            tags = service.metadata.tags if service.metadata and service.metadata.tags else []
            meta = {
                "version": service.metadata.version if service.metadata else "",
                "environment": service.deployment.environment_variables.variables.get("ENV", "development") if service.deployment else "development",
                "deployment_strategy": service.deployment.deployment_strategy if service.deployment else "",
                #Keep an eye on this we don't know if protocol has a .value property
                "protocol": service.protocol.value if hasattr(service.protocol, 'value') else service.protocol,
                "description": service.description or "",
            }

            if service.authentication and service.authentication.type:
                meta["authentication_type"] = service.authentication.type
                

            # Prepare health check
            check = None
            if service.deployment and service.deployment.health_check:
                check = Check.http(
                    url=f"{service.base_url}{service.deployment.health_check.endpoint}",
                    interval=service.deployment.health_check.interval,
                    timeout=service.deployment.health_check.timeout,
                    deregister=service.deployment.health_check.deregister_after,
                )
            
            if service.protocol:
                tags.append(f"protocol:{service.protocol}")

            if service.deployment and service.deployment.environment_variables.variables.get("ENV"):
                tags.append(f"env:{service.deployment.environment_variables.variables.get('ENV')}")

            consul_client.agent.service.register(
                name=service_name,
                service_id=str(service_id),
                address=address,
                port=port,
                tags=tags,
                meta=meta,
                check=check,
            )
            logger.info(f"Registered service '{service.service_name}' with Consul")
        except (ConsulException, ValueError, Exception) as e:
            logger.error(f"Failed to register service '{service.service_name}': {e}")
            raise



    async def deregister_service_with_consul(self, service_id: str):
        """
        Deregisters a service from Consul.

        Args:
            service_id (str): The ID of the service to deregister.
        """
        try:
            consul_client = await self.get_consul_client()
            consul_client.agent.service.deregister(service_id)
            print(f"Deregistered service ID {service_id} from Consul")
        except ConsulException as e:
            logger.error(f"Failed to deregister service ID {service_id}: {e}")

    async def store_schemas_in_kv(self, service: ServiceRegistration):
        """
        Stores service schemas in Consul's key-value store.

        Args:
            service (ServiceRegistration): The service registration object containing schemas.
        """
        consul_client = await self.get_consul_client()
        service_name = service.service_name

        if not service.schemas:
            logger.warning(f"No schemas provided for service '{service_name}'. Skipping schema storage.")
            return

        try:
            schemas = service.schemas.model_dump()
            for schema_type, schema_dict in schemas.items():
                for endpoint, schema in schema_dict.items():
                    key = f"schemas/{service_name}/{endpoint}/{schema_type}_schema"
                    value = json.dumps(schema)
                    consul_client.kv.put(key, value)
                    logger.info(f"Stored schema at key: {key}")
        except ConsulException as e:
            logger.error(f"Failed to store schemas for service '{service_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error while storing schemas for service '{service_name}': {e}")
            raise


    async def store_metadata_in_kv(self, service: ServiceRegistration):
        """
        Stores service metadata in Consul's key-value store.

        Args:
            service (ServiceRegistration): The service registration object containing metadata.
        """
        consul_client = await self.get_consul_client()
        service_name = service.service_name

        if not service.metadata:
            logger.warning(f"No metadata provided for service '{service_name}'. Skipping metadata storage.")
            return

        try:
            metadata = service.metadata.model_dump()
            key = f"metadata/{service_name}"
            value = json.dumps(metadata)
            consul_client.kv.put(key, value)
            logger.info(f"Stored metadata at key: {key}")
        except ConsulException as e:
            logger.error(f"Failed to store metadata for service '{service_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error while storing metadata for service '{service_name}': {e}")
            raise


    async def store_endpoints_in_kv(self, service: ServiceRegistration):

        consul_client = await self.get_consul_client()
        service_name = service.service_name

        if not service.endpoints:
            logger.warning(f"No endpoints provided for service '{service_name}'. Skipping metadata storage.")
            return
        try:
            for endpoint in service.endpoints:
                endpoint_data = endpoint.model_dump()
                key = f"endpoints/{service_name}/{endpoint.name}" 
                value = json.dumps(endpoint_data)
                consul_client.kv.put(key, value)
                logger.info(f"Stored endpoint at key: {key}")
            
        except ConsulException as e:
            logger.error(f"Failed to store endpoints for service '{service_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error while storing endpoints for service '{service_name}': {e}")
            raise

    async def store_deployments_in_kv(self, service: ServiceRegistration):

        consul_client = await self.get_consul_client()
        service_name = service.service_name

        if not service.endpoints:
            logger.warning(f"No deployment configurations provided for service '{service_name}'. Skipping metadata storage.")
            return
        try:
            deployment_data = service.deployment.model_dump()
            kv_key = f"deployment/{service_name}"            
            kv_value = json.dumps(deployment_data)
            consul_client.kv.put(kv_key, kv_value)
            logger.info(f"Stored deployment config for service '{service_name}' at key: {kv_key}")
            
        except ConsulException as e:
            logger.error(f"Failed to store deployment configuration for service '{service_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error while storing deployment configuration for service '{service_name}': {e}")
            raise
        

    async def store_authentication_in_kv(self, service: ServiceRegistration):
        consul_client = await self.get_consul_client()
        service_name = service.service_name

        if not service.authentication:
            logger.warning(f"No deployment configurations provided for service '{service_name}'. Skipping metadata storage.")
            return
        try:
            auth_data = service.authentication.model_dump()
            kv_key = f"authentication/{service_name}"
            kv_value = json.dumps(auth_data)
            consul_client.kv.put(kv_key, kv_value)
            logger.info(f"Stored authentication credentials for service '{service_name}' at key: {kv_key}")
            
        except ConsulException as e:
            logger.error(f"Failed to store deployment configuration for service '{service_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error while storing deployment configuration for service '{service_name}': {e}")
            raise   
