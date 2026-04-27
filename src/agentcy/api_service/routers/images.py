#src/agentcy/api_service/routers/images.py

import uuid, logging
from fastapi import APIRouter, UploadFile, File, HTTPException, status

router = APIRouter()
log     = logging.getLogger(__name__)


ALLOWED_MIME_TYPES = {"application/x-tar", "application/vnd.oci.image.manifest.v1+json"}

@router.post("/upload-image")
async def upload_container_image(
    image: UploadFile = File(
        ...,
        description="Upload a container image file (tarball or OCI manifest)",
    )
):
    # Validate MIME type
    if image.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {image.content_type}. Allowed: {ALLOWED_MIME_TYPES}"
        )

    content = await image.read()
    image_id = str(uuid.uuid4())
    # Save the file (example: local storage)
    file_path = f"/tmp/{image_id}.tar"  # Adjust extension based on MIME type if needed
    with open(file_path, "wb") as f:
        f.write(content)

    return {
        "image_id": image_id,
        "filename": image.filename,
        "content_type": image.content_type
    }