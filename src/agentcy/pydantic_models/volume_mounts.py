#src/agentcy/pydantic_models/volume_mounts.py
from pydantic import BaseModel, Field, field_validator
import os

class VolumeMount(BaseModel):
    name: str = Field(..., min_length=1, max_length=253, description="The name of the volume. Must be DNS-1123 compliant.")
    claim_name: str = Field(..., min_length=1, max_length=253, description="The name of the PersistentVolumeClaim.")
    mount_path: str = Field(..., min_length=1, description="The path in the container where the volume will be mounted.")
    read_only: bool = Field(default=True, description="Indicates if the entity is active. Defaults to True.")
    @field_validator("mount_path")
    def validate_mount_path(cls, value):
        if not os.path.isabs(value):
            raise ValueError("The mount path must be an absolute directory path.")
        # Additional validation to ensure it's a valid path format
        return value

    class Config:
        json_schema_extra = {
            "example": {
                "name": "config-volume",
                "claim_name": "data-pvc",
                "mount_path": "/var/lib/auth-service/data"
            }
        }
