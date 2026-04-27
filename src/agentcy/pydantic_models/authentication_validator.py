#src/agentcy/pydantic_models/authentication_validator.py
from pydantic import BaseModel, Field, HttpUrl, constr
from typing import Union
from enum import Enum
import pydantic



class AuthenticationTypeEnum(str, Enum):
    NONE = "None"
    API_KEY = "API_KEY"
    OAUTH2 = "OAUTH2"
    BASIC = "BASIC"

class ApiKeyCredentials(BaseModel):
  
    type: AuthenticationTypeEnum = Field(AuthenticationTypeEnum.API_KEY)
    api_key: constr(strip_whitespace=True) = Field(..., description="API key for services using API_Key authentication.") # type: ignore

    class Config:
        json_schema_extra = {
            "example": {
                "type": "API_Key",
                "api_key": "12345-ABCDE"
            }
        }

class OAuth2Credentials(BaseModel):
    type: AuthenticationTypeEnum = Field(AuthenticationTypeEnum.OAUTH2)
    token_url: HttpUrl = Field(..., description="Token URL for OAuth2 authentication.")
    client_id: constr(strip_whitespace=True) = Field(..., description="Client ID for OAuth2 authentication.") # type: ignore
    client_secret: constr(strip_whitespace=True) = Field(..., description="Client Secret for OAuth2 authentication.") # type: ignore

    class Config:
        json_schema_extra = {
            "example": {
                "type": "OAuth2",
                "token_url": "https://example.com/oauth2/token",
                "client_id": "my_client_id",
                "client_secret": "my_client_secret"
            }
        }

class BasicCredentials(BaseModel):
    type: AuthenticationTypeEnum = Field(AuthenticationTypeEnum.BASIC)
    client_id: constr(strip_whitespace=True) = Field(..., description="Client ID for Basic authentication.") # type: ignore
    client_secret: constr(strip_whitespace=True) = Field(..., description="Client Secret for Basic authentication.") # type: ignore

    class Config:
        json_schema_extra = {
            "example": {
                "type": "Basic",
                "client_id": "basic_client_id",
                "client_secret": "basic_client_secret"
            }
        }

# Define the Union type for Authentication Credentials
AuthenticationCredentials = Union[ApiKeyCredentials, OAuth2Credentials, BasicCredentials]

