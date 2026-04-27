#src/agentcy/pydantic_models/json_schema_model.py
from pydantic import BaseModel, Field, ValidationError, model_validator, field_validator
from typing import Any, Dict, List, Optional, Union

class JSONSchema(BaseModel):
    type: str = Field(..., description="The type of the JSON Schema object, typically 'object'.")
    properties: Dict[str, Any] = Field(..., description="The properties defined in the schema.")
    required: Optional[List[str]] = Field(None, description="List of required properties.")
    additionalProperties: Optional[bool] = Field(True, description="Whether additional properties are allowed.")
    # Add other fields if necessary

    @field_validator('type')
    def validate_type(cls, value):
        if value != 'object':
            raise ValueError("Top-level 'type' must be 'object'")
        return value

    @field_validator('properties')
    def validate_properties(cls, value):
        allowed_types = {'string', 'number', 'integer', 'boolean', 'array', 'object'}
        max_depth = 3  # You can adjust the maximum depth as needed

        def check_property(prop, depth=1):
            if depth > max_depth:
                raise ValueError("Maximum nesting depth exceeded")
            if 'type' not in prop:
                raise ValueError("Each property must have a 'type'")
            if prop['type'] not in allowed_types:
                raise ValueError(f"Property type must be one of {allowed_types}")
            # Recursively validate nested objects and arrays
            if prop['type'] == 'object':
                if 'properties' in prop:
                    for sub_prop in prop['properties'].values():
                        check_property(sub_prop, depth + 1)
            elif prop['type'] == 'array':
                if 'items' in prop:
                    check_property(prop['items'], depth + 1)
        
        for prop in value.values():
            check_property(prop)
        return value

    @model_validator(mode="after")
    def check_required_properties_exist(cls, self):
        properties = self.properties or {}
        required = self.required or []
        for prop in required:
            if prop not in properties:
                raise ValueError(f"Required property '{prop}' must be defined in 'properties'")
        return self



class Schemas(BaseModel):
    input_schemas: Dict[str, JSONSchema] = Field(..., description="Input schemas per endpoint.")
    output_schemas: Dict[str, JSONSchema] = Field(..., description="Output schemas per endpoint.")

    class Config:
        validate_assignment = True
        extra = 'forbid'
        json_schema_extra = {
            "example": {
                "input_schemas": {
                    "/login": {
                        "type": "object",
                        "properties": {
                            "username": {"type": "string"},
                            "password": {"type": "string"}
                        },
                        "required": ["username", "password"],
                        "additionalProperties": False
                    }
                },
                "output_schemas": {
                    "/login": {
                        "type": "object",
                        "properties": {
                            "token": {"type": "string"}
                        },
                        "required": ["token"],
                        "additionalProperties": False
                    }
                }
            }
        }