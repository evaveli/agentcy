# src/agentcy/pydantic_models/parameter_model.py

from pydantic import BaseModel, Field
from typing import Optional, Union
from enum import Enum

# Fetching the dynamic ENUM from the preloaded enums
class ParameterInEnum(str, Enum):
    QUERY = "query"
    HEADER = "header"
    PATH = "path"
    BODY = "body"

class PathParameter(BaseModel):
    in_param: ParameterInEnum = Field(ParameterInEnum.PATH, alias="in")
    name: str = Field(..., description="Name of the path parameter.")
    type: str = Field("string", description="Data type of the parameter (e.g., 'string').")
    required: bool = Field(True, description="Indicates if the path parameter is required.")
    description: Optional[str] = Field(None, description="Detailed description of the path parameter.")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "user_id",
                "in": "path",
                "type": "integer",
                "required": True,
                "description": "ID of the user."
            }
        }

class QueryParameter(BaseModel):
#    in_: PARAMETERINENUM = Field(PARAMETERINENUM.QUERY, const=True, alias="in")
    name: str = Field(..., description="Name of the query parameter.")
    type: str = Field(..., description="Data type of the query parameter (e.g., 'string').")
    required: Optional[bool] = Field(False, description="Indicates if the query parameter is required.")
    description: Optional[str] = Field(None, description="Detailed description of the query parameter.")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "page",
                "in": "query",
                "type": "integer",
                "required": False,
                "description": "Page number for pagination."
            }
        }

class HeaderParameter(BaseModel):
 #   in_: PARAMETERINENUM = Field(PARAMETERINENUM.HEADER, const=True, alias="in")
    name: str = Field(..., description="Name of the header parameter.")
    type: str = Field(..., description="Data type of the header parameter (e.g., 'string').")
    required: Optional[bool] = Field(False, description="Indicates if the header parameter is required.")
    description: Optional[str] = Field(None, description="Detailed description of the header parameter.")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "Authorization",
                "in": "header",
                "type": "string",
                "required": True,
                "description": "Bearer token for authorization."
            }
        }

class CookieParameter(BaseModel):
#    in_: PARAMETERINENUM = Field(PARAMETERINENUM.COOKIE, const=True, alias="in")
    name: str = Field(..., description="Name of the cookie parameter.")
    type: str = Field(..., description="Data type of the cookie parameter (e.g., 'string').")
    required: Optional[bool] = Field(False, description="Indicates if the cookie parameter is required.")
    description: Optional[str] = Field(None, description="Detailed description of the cookie parameter.")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "name": "session_id",
                "in": "cookie",
                "type": "string",
                "required": True,
                "description": "Session ID stored in the cookie."
            }
        }

# Define the Union type for different Parameter types
ParameterType = Union[PathParameter, QueryParameter, HeaderParameter, CookieParameter]

