#src/agentcy/pydantic_models/file_upload_model.py
from fastapi import File, UploadFile
from pydantic import BaseModel


ALLOWED_MIME_TYPES = {"application/x-tar", "application/vnd.oci.image.manifest.v1+json"}


class ImageUpload(BaseModel):
    image: UploadFile

def container_image(
    image: UploadFile = File(
        ...,
        description="File upload is not restricted by MIME types"
    ),
) -> ImageUpload:
    
    return ImageUpload(image=image)