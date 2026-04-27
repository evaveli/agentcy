#src/agentcy/parsing_layer/image_validation_settings.py

import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv(override=False)  # Load env vars but respect ones already set




from typing import Optional

class Settings(BaseSettings):
    REGISTRY_URL: Optional[str] = os.getenv("REGISTRY_URL")
    REGISTRY_USERNAME: Optional[str] = os.getenv("REGISTRY_USERNAME")
    REGISTRY_PASSWORD: Optional[str] = os.getenv("REGISTRY_PASSWORD")
    AUTH_TYPE: str = "BASIC"

    class Config:
        env_file = ".env"  # This will automatically load variables from .env
        extra = "allow"
settings = Settings()
