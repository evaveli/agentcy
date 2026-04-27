#src/agentcy/pydantic_models/metadata_model.py
from pydantic import BaseModel, Field
from typing import List, Optional

class Metadata(BaseModel):
    version: Optional[str] = Field(
        None, 
        description="Version of the service."
    )
    tags: Optional[List[str]] = Field(
        None, 
        description="Tags or labels associated with the service."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "version": "1.0.0",
                "tags": ["authentication", "user-management"]
            }
        }
