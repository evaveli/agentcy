# config.py

import asyncio
import logging
import httpx
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
from typing import Dict, List, Optional
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ServiceDiscoveryError(Exception):
    """Custom exception for service discovery failures."""
    pass

class ConsulConfig(BaseSettings):
    consul_host: str = Field(default="localhost", env="CONSUL_HOST")
    consul_port: int = Field(default=8500, env="CONSUL_PORT")
    consul_scheme: str = Field(default="http", env="CONSUL_SCHEME")
    consul_token: Optional[str] = Field(default=None, env="CONSUL_TOKEN")
    request_timeout: float = Field(default=5.0, env="CONSUL_REQUEST_TIMEOUT")
    max_retries: int = Field(default=3, env="CONSUL_MAX_RETRIES")
    backoff_factor: float = Field(default=0.5, env="CONSUL_BACKOFF_FACTOR")

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

    @field_validator('consul_scheme')
    def validate_scheme(cls, v):
        if v not in ('http', 'https'):
            raise ValueError("CONSUL_SCHEME must be 'http' or 'https'")
        return v

class ServiceEndpoint(BaseModel):
    address: str
    port: int
    node: Optional[str] = None
    datacenter: Optional[str] = None

class ServiceDiscovery:
    def __init__(self, config: ConsulConfig):
        self.config = config
        headers = {}
        if self.config.consul_token:
            headers["X-Consul-Token"] = self.config.consul_token
        
        self.client = httpx.AsyncClient(
            base_url=f"{self.config.consul_scheme}://{self.config.consul_host}:{self.config.consul_port}",
            headers=headers,
            timeout=self.config.request_timeout,
        )


   

    async def discover_service_http(self, service_name: str, passing: bool = True) -> List[Dict[str, str]]:
        """
        Asynchronously discovers services registered with Consul using the HTTP API.

        Args:
            service_name (str): The name of the service to discover.
            passing (bool): If True, only returns services with passing health checks.

        Returns:
            List[Dict[str, str]]: A list of dictionaries containing service addresses and ports.

        Raises:
            ServiceDiscoveryError: If the API call fails or returns invalid data.
        """
        endpoint = f"/v1/health/service/{service_name}"
        params = {"passing": "true"} if passing else {}

        attempt = 0

        while attempt < self.config.max_retries:
            try:
                response = await self.client.get(endpoint, params=params)
                response.raise_for_status()
                services = response.json()

                service_endpoints = self.parse_services(services=services)
                if not service_endpoints:
                        logger.warning(f"No healthy instances found for service '{service_name}'.")
                    
                return service_endpoints

            except httpx.RequestError as e:
                logger.error(f"Request error during service discovery for '{service_name}': {e}")
                attempt += 1
                await self._backoff(attempt)
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error during service discovery for '{service_name}': {e}")
                raise ServiceDiscoveryError(f"HTTP error: {e}") from e
            except ValueError as e:
                logger.error(f"Invalid JSON response from Consul for service '{service_name}': {e}")
                raise ServiceDiscoveryError(f"Invalid response data: {e}") from e

        raise ServiceDiscoveryError(f"Failed to discover service '{service_name}' after {self.config.max_retries} attempts.")

    
    def parse_services(self, services: List[Dict], service_name: str) -> List[ServiceEndpoint]:

        service_endpoints = []
        for service_entry in services:
            service = service_entry.get('Service', {})
            node = service_entry.get('Node', {})
            service_id = service.get('ID', 'unknown')
            
            # Address extraction with priority
            address = service.get('Address')
            if not address:
                tagged_addresses = node.get('TaggedAddresses', {})
                address = (
                    tagged_addresses.get('lan_ipv4') or 
                    tagged_addresses.get('lan_ipv6') or 
                    tagged_addresses.get('lan') or 
                    tagged_addresses.get('wan_ipv4') or 
                    tagged_addresses.get('wan_ipv6') or 
                    tagged_addresses.get('wan') or 
                    node.get('Address')
                )
            
            port = service.get('Port')
            
            # Type Validation
            if not isinstance(address, str) or not address.strip():
                logger.warning(f"Invalid or empty address for service '{service_name}' (Service ID: {service_id}): {address}")
                continue
            if not isinstance(port, int):
                logger.warning(f"Invalid port type for service '{service_name}' (Service ID: {service_id}): {port}")
                continue
            
            # Create ServiceEndpoint instance
            try:
                endpoint = ServiceEndpoint(
                    address=address,
                    port=port,
                    node=node.get('Node'),
                    datacenter=node.get('Datacenter')
                )
                service_endpoints.append(endpoint)
            except ValueError as e:
                logger.warning(f"Invalid endpoint data for service '{service_name}' (Service ID: {service_id}): {e}")
        
        if not service_endpoints:
            logger.warning(f"No valid service endpoints found for '{service_name}'.")
        
        return service_endpoints

    async def _backoff(self, attempt: int):
        delay = self.config.backoff_factor * (2 ** (attempt - 1))
        logger.info(f"Retrying in {delay} seconds...")
        await asyncio.sleep(delay)

    async def close(self):
        await self.client.aclose()