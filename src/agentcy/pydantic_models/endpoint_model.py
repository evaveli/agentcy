#src/agentcy/pydantic_models/endpoint_model.py
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional
from agentcy.pydantic_models.parameter_model import ParameterType
class ProtocolEnum(str, Enum):
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    GRPC = "gRPC"
    AMQP = "AMQP"
    MQTT = "MQTT"
    WEBSOCKET = "WebSocket"

class HttpMethod(str, Enum):
    GET     = "GET"
    POST    = "POST"
    PUT     = "PUT"
    PATCH   = "PATCH"
    DELETE  = "DELETE"
    HEAD    = "HEAD"
    OPTIONS = "OPTIONS"

class Endpoint(BaseModel):
    name: str = Field(..., description="Logical name of the endpoint (e.g., 'create_order').")
    path: str = Field(..., description="Relative path of the endpoint (e.g., '/api/v1/orders/create').")
    methods: List[HttpMethod] = Field(..., description="Supported HTTP methods for the endpoint.")
    description: Optional[str] = Field(None, description="Detailed description of the endpoint's functionality.")
    parameters: Optional[List[ParameterType]] = Field(None, description="List of parameters accepted by the endpoint.")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "create_order",
                "path": "/api/v1/orders/create",
                "methods": ["POST"],
                "description": "Endpoint to create a new order.",
                "parameters": [
                    {
                        "name": "Authorization",
                        "in": "header",
                        "type": "string",
                        "required": True,
                        "description": "Bearer token for authorization."
                    },
                    {
                        "name": "user_id",
                        "in": "path",
                        "type": "integer",
                        "required": True,
                        "description": "ID of the user placing the order."
                    }
                ]
            }
        }

    @classmethod
    def parse_obj(cls, obj):
        # No need for override unless additional processing is required
        return super().model_validate(obj)
