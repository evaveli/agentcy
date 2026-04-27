#src/agentcy/pydantic_models/workflow_management_validation.py
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json

class Auth(BaseModel):
    scope: str

class Details(BaseModel):
    endpoint: str
    method: str
    headers: Dict[str, str] = {}
    query_params: Dict[str, str] = {}
    path_params: Dict[str, str] = {}
    timeout: int
    retries: int
    retry_delay: int
    auth: Auth


class InputSource(BaseModel):
    source_id: str
    source_type: str
    details: Details