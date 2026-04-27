import uvicorn
from fastapi import FastAPI
import os
from src import router
from fastapi.middleware.cors import CORSMiddleware
from src.api_spec import api_spec_editor
from fastapi.staticfiles import StaticFiles
import logging  # For logging
from dotenv import load_dotenv  # For environment variables
from pathlib import Path
from contextlib import asynccontextmanager



@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading Enums at application startup")

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
logging.basicConfig(level=LOG_LEVEL.upper())


app = FastAPI(lifespan=lifespan)




######################----------ROUTES
app.include_router(router.api_router)
######################----------ROUTES


######################----------DOCS
app.openapi_schema = api_spec_editor.set_openapi_spec(app.openapi_schema, app.routes)
######################----------DOCS

# app.add_middleware(IPFilterMiddleware, allowed_ips=allowed_ips_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)
logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more verbosity
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

# Optionally, adjust log levels for specific loggers
logging.getLogger('uvicorn').setLevel(logging.INFO)


@app.get("/")
def get_homepage():
    return {"Response": "This is the home page"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")