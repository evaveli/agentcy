import base64
import json
import logging
import os
import threading
import time
import subprocess
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import quote

import httpx
import requests
from docker.utils.utils import parse_bytes


DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
DOCKER_API_VERSION = os.getenv("DOCKER_API_VERSION", "v1.41")
LOG_MAX_BYTES = int(os.getenv("AGENT_LOG_MAX_BYTES", "200000"))  # 200 KB default
_CLIENT_CACHE: Dict[Tuple[str, float, int, float, str, str], httpx.Client] = {}
_CLIENT_CACHE_LOCK = threading.Lock()


def _network_exists(name: Optional[str]) -> bool:
    if not name:
        return False
    try:
        subprocess.run(
            ["docker", "network", "inspect", name],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _client(
    timeout: int = 60,
    *,
    retries: int = 3,
    backoff_factor: float = 0.25,
    logger: Optional[logging.Logger] = None,
    use_cache: bool = True,
) -> httpx.Client:
    """
    Build an HTTP/HTTPS/Unix/SSH client pointed at the Docker daemon.
    The client is cached per (host, timeout, retries, backoff, logger) tuple so callers
    can opt into pooled connections across invocations.
    """
    host = DOCKER_HOST
    logger_name = logger.name if logger else ""
    cache_key = (host, timeout, retries, backoff_factor, logger_name, str(use_cache))

    if use_cache:
        with _CLIENT_CACHE_LOCK:
            cached = _CLIENT_CACHE.get(cache_key)
            if cached is not None:
                return cached

    client = _build_client(host, timeout, retries, backoff_factor, logger)

    if use_cache:
        with _CLIENT_CACHE_LOCK:
            _CLIENT_CACHE[cache_key] = client
    return client


def _build_client(
    host: str,
    timeout: int,
    retries: int,
    backoff_factor: float,
    logger: Optional[logging.Logger],
) -> httpx.Client:
    scheme = host.split("://", 1)[0].lower() if "://" in host else "tcp"
    event_hooks = _logging_hooks(logger)
    base_path = f"http://docker/{DOCKER_API_VERSION}/"

    if scheme == "unix":
        uds_path = host[len("unix://") :]
        transport = httpx.HTTPTransport(uds=uds_path, retries=retries)
        return httpx.Client(
            transport=transport,
            base_url=base_path,
            timeout=timeout,
            verify=False,
            event_hooks=event_hooks,
        )

    if scheme in {"tcp", "http", "https"}:
        base_url, verify, cert = _tcp_settings(host)
        # append API version
        if not base_url.endswith("/"):
            base_url += "/"
        base_url = base_url + DOCKER_API_VERSION + "/"
        transport = httpx.HTTPTransport(retries=retries)
        return httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            verify=verify,
            cert=cert,
            event_hooks=event_hooks,
        )

    if scheme == "ssh":
        transport = _requests_transport_for_ssh(
            host=host,
            timeout=timeout,
            retries=retries,
            backoff_factor=backoff_factor,
            logger=logger,
        )
        return httpx.Client(
            transport=transport,
            base_url="http://docker",
            timeout=timeout,
            verify=False,
        )

    if scheme == "npipe":
        transport = _requests_transport_for_npipe(
            host=host,
            timeout=timeout,
            retries=retries,
            backoff_factor=backoff_factor,
            logger=logger,
        )
        return httpx.Client(
            transport=transport,
            base_url="http://docker",
            timeout=timeout,
            verify=False,
        )

    raise ValueError(f"Unsupported DOCKER_HOST scheme: {host}")


def _tcp_settings(host: str) -> Tuple[str, Union[bool, str], Optional[Tuple[str, str]]]:
    tls_enabled = os.getenv("DOCKER_TLS_VERIFY") == "1"
    base_url = host
    if host.startswith("tcp://"):
        scheme = "https" if tls_enabled else "http"
        base_url = host.replace("tcp://", f"{scheme}://", 1)
    elif not host.startswith(("http://", "https://")):
        base_url = f"http://{host}"

    if tls_enabled or base_url.startswith("https://"):
        verify, cert = _tls_settings()
    else:
        verify, cert = True, None

    return base_url, verify, cert


def _tls_settings() -> Tuple[Union[bool, str], Optional[Tuple[str, str]]]:
    cert_dir = os.getenv("DOCKER_CERT_PATH")
    verify: Union[bool, str] = True
    client_cert: Optional[Tuple[str, str]] = None

    if cert_dir:
        ca_path = os.path.join(cert_dir, "ca.pem")
        if os.path.exists(ca_path):
            verify = ca_path
        cert_path = os.path.join(cert_dir, "cert.pem")
        key_path = os.path.join(cert_dir, "key.pem")
        if os.path.exists(cert_path) and os.path.exists(key_path):
            client_cert = (cert_path, key_path)

    return verify, client_cert


def _logging_hooks(
    logger: Optional[logging.Logger],
) -> Optional[Dict[str, List[Callable[[httpx.Response], None]]]]:
    if not logger:
        return None

    def _log_response(response: httpx.Response) -> None:
        duration = None
        try:
            elapsed = getattr(response, "elapsed", None)
            if elapsed is not None and hasattr(elapsed, "total_seconds"):
                duration = elapsed.total_seconds()
        except RuntimeError:
            # httpx raises if elapsed accessed before body read; ignore in logs.
            duration = None
        duration_text = f" ({duration:.3f}s)" if duration is not None else ""
        logger.debug(
            "Docker HTTP %s %s -> %s%s",
            response.request.method if response.request else "?",
            response.request.url if response.request else "?",
            response.status_code,
            duration_text,
        )

    return {"response": [_log_response]}


class _RequestsResponseStream(httpx.SyncByteStream):
    def __init__(self, response: requests.Response):
        self._response = response
        self._iterator = response.iter_content(chunk_size=65536)

    def __iter__(self):
        for chunk in self._iterator:
            if chunk:
                yield chunk

    def close(self) -> None:
        self._response.close()


class _RequestsTransport(httpx.BaseTransport):
    def __init__(
        self,
        session: requests.Session,
        timeout: float,
        retries: int,
        backoff_factor: float,
        logger: Optional[logging.Logger] = None,
    ):
        self._session = session
        self._timeout = timeout
        self._retries = max(0, retries)
        self._backoff_factor = max(0.0, backoff_factor)
        self._logger = logger

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # httpx guarantees Request.read() returns bytes for sync clients,
        # so this satisfies typing without depending on iterable semantics.
        body = request.read()
        if hasattr(request.stream, "close"):
            request.stream.close() # type: ignore
        attempt = 0
        while True:
            try:
                response = self._session.request(
                    method=request.method,
                    url=str(request.url),
                    headers=dict(request.headers),
                    data=body or None,
                    timeout=self._timeout,
                    stream=True,
                )
                break
            except requests.RequestException as exc:
                if attempt >= self._retries:
                    raise httpx.RequestError(
                        f"Docker request failed: {exc}", request=request
                    ) from exc
                attempt += 1
                delay = self._backoff_factor * (2 ** (attempt - 1))
                if delay:
                    time.sleep(delay)
        stream = _RequestsResponseStream(response)
        if self._logger:
            elapsed = getattr(response, "elapsed", None)
            if elapsed is not None and hasattr(elapsed, "total_seconds"):
                duration = elapsed.total_seconds()
            else:
                duration = None
            duration_text = f" ({duration:.3f}s)" if duration is not None else ""
            self._logger.debug(
                "Docker request %s %s -> %s%s",
                response.request.method if response.request else request.method,
                response.request.url if response.request else request.url,
                response.status_code,
                duration_text,
            )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers.items(),  # type: ignore
            stream=stream,
            request=request,
        )

    def close(self) -> None:
        self._session.close()


def _requests_transport_for_ssh(
    host: str,
    timeout: int,
    retries: int,
    backoff_factor: float,
    logger: Optional[logging.Logger],
) -> _RequestsTransport:
    try:
        from docker.transport import SSHHTTPAdapter
        from docker.constants import DEFAULT_MAX_POOL_SIZE, DEFAULT_NUM_POOLS_SSH
    except ImportError as exc:
        raise RuntimeError(
            "SSH DOCKER_HOST requires docker SDK optional dependencies (paramiko)."
        ) from exc

    session = requests.Session()
    adapter = SSHHTTPAdapter(
        base_url=host,
        timeout=timeout,
        pool_connections=DEFAULT_NUM_POOLS_SSH,
        max_pool_size=DEFAULT_MAX_POOL_SIZE,
        shell_out=False,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return _RequestsTransport(session, timeout, retries, backoff_factor, logger)


def _requests_transport_for_npipe(
    host: str,
    timeout: float,
    retries: int,
    backoff_factor: float,
    logger: Optional[logging.Logger],
) -> _RequestsTransport:
    try:
        from docker.transport import NpipeHTTPAdapter
        from docker.constants import DEFAULT_MAX_POOL_SIZE, DEFAULT_NUM_POOLS
        from docker import constants as docker_constants
    except ImportError as exc:
        raise RuntimeError(
            "npipe DOCKER_HOST requires docker SDK Windows dependencies."
        ) from exc

    if not docker_constants.IS_WINDOWS_PLATFORM:
        raise RuntimeError("npipe:// connections are only available on Windows hosts.")

    session = requests.Session()
    adapter = NpipeHTTPAdapter(
        base_url=host,
        timeout=timeout,  # type: ignore
        pool_connections=DEFAULT_NUM_POOLS,
        max_pool_size=DEFAULT_MAX_POOL_SIZE,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return _RequestsTransport(session, timeout, retries, backoff_factor, logger)


def ensure_image(
    image: str,
    *,
    auth: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Ensure the Docker image exists locally; pull it from registry if necessary.
    """
    client = _client(timeout=timeout, logger=logger)
    try:
        if _image_exists(client, image):
            return
        _pull_image(client, image, auth, logger)
        return
    except (httpx.HTTPError, httpx.RequestError) as exc:
        if logger:
            logger.warning("Docker API ensure_image failed (%s); falling back to docker CLI", exc)

    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI not available for ensure_image fallback") from exc

    if inspect.returncode == 0:
        return

    pull = subprocess.run(
        ["docker", "pull", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        err = pull.stderr.strip() or pull.stdout.strip()
        raise RuntimeError(f"Docker CLI pull failed for {image}: {err}")


def create_container(
    *,
    image: str,
    name: str,
    command: Optional[List[str]],
    environment: Dict[str, str],
    mem_limit: Optional[Union[str, int]] = None,
    nano_cpus: Optional[int] = None,
    network: Optional[str] = None,
    auto_remove: bool = True,
    timeout: int = 60,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Create a Docker container and return its ID.
    """
    client = _client(timeout=timeout, logger=logger)
    env_list = [f"{k}={v}" for k, v in environment.items()]

    host_config: Dict[str, Union[bool, int, str]] = {"AutoRemove": auto_remove}
    if mem_limit:
        host_config["Memory"] = _memory_limit(mem_limit)
    if nano_cpus:
        host_config["NanoCpus"] = nano_cpus
    if network:
        host_config["NetworkMode"] = network

    payload: Dict[str, Union[str, List[str], Dict[str, object]]] = {
        "Image": image,
        "Env": env_list,
        "Cmd": command,
        "HostConfig": host_config,
        "AttachStdout": False,
        "AttachStderr": False,
        "Tty": False,
    }

    if network:
        payload["NetworkingConfig"] = {
            "EndpointsConfig": {
                network: {}
            }
        }

    response = client.post("containers/create", params={"name": name}, json=payload)
    response.raise_for_status()
    data = response.json()
    warnings = data.get("Warnings")
    if warnings and logger:
        logger.warning("Docker container warnings for %s: %s", name, warnings)
    container_id = data.get("Id")
    if not container_id:
        raise RuntimeError("Docker did not return a container ID")
    return container_id


def start_container(
    container_id: str,
    *,
    timeout: int = 60,
    logger: Optional[logging.Logger] = None,
    name: Optional[str] = None,
) -> None:
    """
    Start the previously created container.
    """
    client = _client(timeout=timeout, logger=logger)
    try:
        response = client.post(f"containers/{container_id}/start")
        response.raise_for_status()
        return
    except httpx.HTTPStatusError as e:
        pass  # handled below
    except httpx.RequestError:
        pass

    # fallback to docker CLI for environments where HTTP start fails
    target = name or container_id
    try:
        proc = subprocess.run(
            ["docker", "start", target],
            check=True,
            capture_output=True,
            text=True,
        )
        if logger:
            logger.info("docker start fallback output: %s", proc.stdout.strip())
        return
    except Exception as cli_err:
        if logger:
            logger.error("Docker CLI start fallback failed: %s", cli_err)
        raise RuntimeError(f"Failed to start container {target}: {cli_err}") from cli_err


def run_container_cli(
    *,
    image: str,
    name: str,
    environment: Dict[str, str],
    command: Optional[List[str]] = None,
    network: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Best-effort docker CLI fallback when API-based create/start fails.
    """
    # try to clear any stale container with the same name
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)

    args = ["docker", "run", "-d", "--name", name, "--rm"]
    for k, v in environment.items():
        args.extend(["-e", f"{k}={v}"])
    if network:
        args.extend(["--network", network])

    args.append(image)
    if command:
        args.extend(command)

    proc = subprocess.run(args, check=True, capture_output=True, text=True)
    if logger:
        logger.info("docker run fallback output: %s", proc.stdout.strip())
    return proc.stdout.strip()


def stream_logs(
    container_id: str,
    *,
    stdout: bool = True,
    stderr: bool = True,
    follow: bool = True,
    tail: Union[str, int] = "all",
    timeout: Optional[float] = None,
    logger: Optional[logging.Logger] = None,
) -> Iterable[bytes]:
    """
    Stream container logs yielding raw chunks of bytes.
    """
    client = _client(timeout=int(timeout or 60), logger=logger)
    params = {
        "stdout": int(bool(stdout)),
        "stderr": int(bool(stderr)),
        "follow": int(bool(follow)),
        "tail": tail,
    }
    with client.stream(
        "GET",
        f"containers/{container_id}/logs",
        params=params,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if chunk:
                yield chunk


def _memory_limit(value: Union[str, int]) -> int:
    if isinstance(value, int):
        return value
    return parse_bytes(str(value))


def _image_exists(client: httpx.Client, image: str) -> bool:
    ref = _encode_image_ref(image)
    response = client.get(f"images/{ref}/json")
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return False


def _pull_image(
    client: httpx.Client,
    image: str,
    auth: Optional[Dict[str, str]],
    logger: Optional[logging.Logger],
) -> None:
    headers = {}
    if auth:
        headers["X-Registry-Auth"] = _encode_registry_auth(auth)
    response = client.post("images/create", params={"fromImage": image}, headers=headers)
    response.raise_for_status()
    # Drain the stream to free the connection.
    for chunk in response.iter_bytes():
        if chunk and logger:
            logger.debug("Docker pull %s: %s", image, chunk.decode("utf-8", "ignore").strip())


def _encode_registry_auth(auth: Dict[str, str]) -> str:
    payload = json.dumps(auth, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.b64encode(payload).decode("utf-8")


def _encode_image_ref(image: str) -> str:
    return quote(image, safe="/:@")
