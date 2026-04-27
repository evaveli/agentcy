# src/agentcy/api_service/routers/services_create_with_artifact.py
import logging, os, re, tempfile, json
from typing import Optional, Literal
from uuid import uuid4
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status
from agentcy.api_service.dependecies import get_rm, get_publisher, CommandPublisher, ResourceManager
from agentcy.pydantic_models.service_registration_model import ServiceRegistration, RuntimeEnum
from agentcy.pydantic_models.endpoint_model import Endpoint, HttpMethod
from agentcy.pydantic_models.commands import RegisterServiceCommand
from agentcy.api_service.services.ingest import ingest_wheel, ingest_oci

router = APIRouter()
log = logging.getLogger(__name__)
MAX_MB = int(os.getenv("ARTIFACT_MAX_MB", "500"))
WHEEL_RE = re.compile(r"^(?P<name>.+)-(?P<version>\d+(\.\d+)*([a-zA-Z0-9\.\-]+)?)-.+\.whl$")

@router.post("/services/{username}/create-with-artifact", status_code=status.HTTP_202_ACCEPTED)
async def create_with_artifact(
    username: str,
    kind: Literal["wheel","oci"] = Form(...),
    service_name: str = Form(...),
    status_env: Literal["dev","stg","prod"] = Form("prod"),
    # wheel
    entry: Optional[str] = Form(None),         # required for wheel
    wheel_name: Optional[str] = Form(None),
    wheel_version: Optional[str] = Form(None),
    requires_python: str = Form("~=3.11"),
    agentcy_abi: str = Form("1"),
    signatures_json: Optional[str] = Form(None),
    # oci
    repo: Optional[str] = Form(None),
    tag: Optional[str] = Form(None),
    # common
    env_json: Optional[str] = Form(None),      # optional: store in your ServiceStore if you want
    artifact: UploadFile = File(...),
    rm:  ResourceManager   = Depends(get_rm),
    pub: CommandPublisher  = Depends(get_publisher),
):
    # stream to disk
    tmpdir = tempfile.mkdtemp(prefix="svc_art_")
    path = os.path.join(tmpdir, artifact.filename or "artifact.bin")
    size=0
    with open(path, "wb") as out:
        while True:
            chunk = await artifact.read(1<<20)
            if not chunk: break
            size += len(chunk)
            if size > MAX_MB*(1<<20):
                raise HTTPException(413, f"Artifact exceeds {MAX_MB}MB limit")
            out.write(chunk)

    # ingest to Nexus + catalog
    if kind == "wheel":
        if not entry:
            raise HTTPException(400, "'entry' is required for wheel")

        if not (wheel_name and wheel_version):
            m = WHEEL_RE.match(artifact.filename or "")
            if not m:
                raise HTTPException(400, "Provide wheel_name & wheel_version or a PEP427 filename")
            wheel_name = wheel_name or m.group("name")
            wheel_version = wheel_version or m.group("version")

        # 🔒 Narrow Optionals to str (satisfies Pylance + runtime safety)
        assert wheel_name is not None, "internal: wheel_name should be set"
        assert wheel_version is not None, "internal: wheel_version should be set"
        assert entry is not None, "internal: entry should be set"

        pkg_name: str = wheel_name
        pkg_version: str = wheel_version
        entry_s: str = entry

        sigs: list[str] = []
        if signatures_json:
            try:
                sigs = json.loads(signatures_json)
            except Exception:
                raise HTTPException(400, "Invalid signatures_json")

        # ensure catalog store is available
        if rm.catalog_user_store is None:
            raise HTTPException(500, "Catalog store is not configured")

        try:
            ingest_res = await ingest_wheel(
                path,
                name=pkg_name,                 # ✅ str, not Optional
                version=pkg_version,           # ✅ str, not Optional
                entry=entry_s,                 # ✅ str, not Optional
                status=status_env,
                requires_python=requires_python,
                agentcy_abi=agentcy_abi,
                signatures=sigs,
                env=status_env,                # ✅ new required arg
                username=username,             # ✅ new required arg
                user_catalog_store=rm.catalog_user_store,  # ✅ new required arg
            )
        except Exception as e:
            log.exception("wheel ingest failed")
            raise HTTPException(502, f"Wheel ingest failed: {e}")

        artifact_ref = ingest_res["artifact"]
        runtime = RuntimeEnum.PYTHON_PLUGIN

    else:  # oci
        if not (repo and tag):
            raise HTTPException(400, "'repo' and 'tag' are required for kind=oci")
        
        if rm.catalog_user_store is None:
                raise HTTPException(500, "Catalog store is not configured")
        try:
            
            ingest_res = await ingest_oci(
                path,
                repo=repo,
                tag=tag,
                status=status_env,
                entry=entry,
                env=status_env,                      
                username=username,                   
                user_catalog_store=rm.catalog_user_store,
            )
        except KeyError as e:
            log.exception("oci ingest failed"); raise HTTPException(502, f"OCI ingest failed: {e}")
        except Exception as e:
            log.exception("oci ingest failed"); raise HTTPException(502, f"OCI ingest failed: {e}")
        artifact_ref = ingest_res["artifact"]
        runtime = RuntimeEnum.CONTAINER

    # optional env passthrough (store it wherever your store expects)
    env = {}
    if env_json:
        try: env = json.loads(env_json)
        except Exception: raise HTTPException(400, "Invalid env_json")

    # Build ServiceRegistration (v2)
    payload = ServiceRegistration(
        service_id=uuid4(),
        service_name=service_name,             # must pass DNS-1123 validation in model
        version=None,
        image_tag=None,
        runtime=runtime,
        artifact=artifact_ref,
        base_url=None,                         # resolved post-deploy
        healthcheck_endpoint=Endpoint(
            name="health", path="/health", methods=[HttpMethod.GET], description="Health endpoint", parameters=[]
        ),
        # keep description/image_tag out; artifact is the source of truth
    )

    # upsert + publish command
    store = rm.service_store
    if store is None:
        raise HTTPException(500, "Service store is not configured")
    sid = store.upsert(username, payload)
    await pub.publish("commands.register_service", RegisterServiceCommand(username=username, service=payload))

    return {
        "service_id": sid,
        "catalog_doc_id": ingest_res["catalog_doc_id"],
        "runtime": runtime.value,
        "artifact": artifact_ref,
        "env": env,   # include if you persist it in store
        "detail": "queued for processing"
    }
