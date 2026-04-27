#src/agentcy/parsing_layer/validate_container_image.py

import re
import logging
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from agentcy.parsing_layer.image_validation_settings import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variable configuration
# In production, consider a config management tool or Pydantic Settings
# Now use settings instead of reading from os.getenv at module level
REGISTRY_URL = settings.REGISTRY_URL.strip() if settings.REGISTRY_URL else ""
REGISTRY_USERNAME = settings.REGISTRY_USERNAME.strip() if settings.REGISTRY_USERNAME else ""
REGISTRY_PASSWORD = settings.REGISTRY_PASSWORD.strip() if settings.REGISTRY_PASSWORD else ""
AUTH_TYPE = settings.AUTH_TYPE.strip().upper() if settings.AUTH_TYPE else ""

REGISTRY_API_BASE_URL = f"https://{REGISTRY_URL}/v2"
IMAGE_TAG_REGEX = r"^(?:(?P<registry>[\w\-.]+(?::\d+)?)/)?(?P<repository>[\w\-.]+/[\w\-.]+):(?P<tag>[\w\-.]+)$"
async def get_docker_token(repository: str, action: str = "pull") -> str:
    """
    Get an authentication token from Docker Hub for the specified repository and action.
    """
    auth_url = "https://auth.docker.io/token"
    params = {
        "service": "registry.docker.io",
        "scope": f"repository:{repository}:{action}"
    }
    auth = (REGISTRY_USERNAME, REGISTRY_PASSWORD)
    
    async with httpx.AsyncClient() as client:
        response = await client.get(auth_url, params=params, auth=auth)
        if response.status_code == 200:
            return response.json().get("token")
        else:
            logger.error(
                "Failed to authenticate with Docker Hub. Status: %s. Detail: %s",
                response.status_code, response.text
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to authenticate with Docker Hub."
            )




def parse_image_tag(image_tag: str) -> dict:
    """
    Parse a Docker image tag into registry, repository, and tag components.
    Expected format: registry/namespace/repository:tag
    """
    match = re.match(IMAGE_TAG_REGEX, image_tag)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image_tag format. Expected 'registry/namespace/repo:tag'."
        )
        
    return match.groupdict()


def normalize_registry_url(url: str) -> str:
    """
    Normalize a registry URL to a consistent hostname[:port] format.
    """
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    hostname = parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{hostname}{port}"

async def validate_image_in_registry(image_tag: str):
    """
    Validate that the given image_tag exists in the configured private registry.
    
    Steps:
    - Parse the image tag to extract registry, repository, and tag.
    - Ensure the registry matches the configured REGISTRY_URL.
    - Make an authenticated GET request to the registry's v2 API to fetch the manifest.
    - If the manifest is retrieved (200 OK), image is considered valid.
    - Otherwise, raise a 400 or 500 HTTPException accordingly.

    Raises:
        HTTPException: If image tag format is invalid, registry doesn't match, image not found, or registry unreachable.
    """
    logger.info("Validating image in registry: %s", image_tag)
    parsed = parse_image_tag(image_tag)
    registry_part = parsed["registry"] or REGISTRY_URL
    repository = parsed["repository"]
    tag = parsed["tag"]

    # Normalize and compare registry URLs
    normalized_registry = normalize_registry_url(REGISTRY_URL)
    normalized_registry_part = normalize_registry_url(registry_part)

    if normalized_registry_part != normalized_registry:
        detail_msg = f"Image registry {registry_part} does not match expected registry {REGISTRY_URL}."
        logger.warning(detail_msg)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail_msg
        )
    token = await get_docker_token(repository)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.docker.distribution.manifest.v2+json"
    }
    url = f"{REGISTRY_API_BASE_URL}/{repository}/manifests/{tag}"

    logger.info("Checking image manifest at: %s", url)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            # If we reach here, image manifest was successfully retrieved
            logger.info("Image '%s' validated successfully in registry '%s'.", image_tag, REGISTRY_URL)
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                # Client error: Image not found or unauthorized
                detail_msg = (f"Client error when accessing registry: {e.response.status_code}. "
                              f"Detail: {e.response.text}")
                logger.error(detail_msg)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=detail_msg
                )
            elif e.response.status_code >= 500:
                # Server error: Registry issue
                detail_msg = (f"Server error when accessing registry: {e.response.status_code}. "
                              f"Detail: {e.response.text}")
                logger.error(detail_msg)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=detail_msg
                )
            else:
                # Unexpected status code scenario
                detail_msg = (f"Unexpected HTTP status {e.response.status_code} from registry. "
                              f"Response: {e.response.text}")
                logger.error(detail_msg)
                raise HTTPException(
                    status_code=status.e,
                    detail=detail_msg
                )
        except httpx.RequestError as e:
            # Network problem, DNS issue, or TLS error
            detail_msg = f"Registry is unreachable. Error: {str(e)}"
            logger.error(detail_msg)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail_msg
            )


