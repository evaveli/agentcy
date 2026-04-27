#src/agentcy/api_service/services/nexus_pypi.py
import asyncio, os, sys, contextlib
from typing import Tuple

NEXUS_PYPI_URL = os.getenv("NEXUS_PYPI_URL")
NEXUS_USERNAME = os.getenv("NEXUS_USERNAME")
NEXUS_PASSWORD = os.getenv("NEXUS_PASSWORD")

class UploadError(Exception):
    pass

async def twine_upload(wheel_path: str, timeout: float | None = None) -> Tuple[int, str]:
    if not (NEXUS_PYPI_URL and NEXUS_USERNAME and NEXUS_PASSWORD):
        raise UploadError("Nexus PyPI credentials or URL missing")

    env = os.environ.copy()
    env["TWINE_REPOSITORY_URL"] = NEXUS_PYPI_URL
    env["TWINE_USERNAME"] = NEXUS_USERNAME
    env["TWINE_PASSWORD"] = NEXUS_PASSWORD

    cmd = [sys.executable, "-m", "twine", "upload"]
    if os.getenv("TWINE_SKIP_EXISTING") == "1":
        cmd.append("--skip-existing")
    cmd.append(wheel_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        # ensure the subprocess is actually reaped
        await proc.wait()
        raise UploadError("twine upload timed out")

    # ✅ typed as int, satisfies Pylance
    rc: int = await proc.wait()
    return rc, (stdout or b"").decode(errors="replace")
