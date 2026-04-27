#src/agentcy/shared_lib/kv/backoff.py
import logging, functools, tenacity as tt

logger = logging.getLogger("backoff")

def with_backoff(
    *,
    msg: str = "kv‑operation",
    max_attempts: int = 3,
    wait: float = 0.3
):
    """
    Decorator that retries transient KV exceptions with exponential back‑off.
    """
    def decorator(fn):
        @functools.wraps(fn)
        @tt.retry(
            reraise=True,
            stop=tt.stop_after_attempt(max_attempts),
            wait=tt.wait_exponential(multiplier=wait),
        )
        async def async_wrapper(*a, **kw):  # (in case we wrap an async func)
            try:
                return await fn(*a, **kw)
            except Exception as exc:
                logger.warning("%s failed – retrying: %s", msg, exc)
                raise

        @functools.wraps(fn)
        def sync_wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as exc:
                logger.warning("%s failed – retrying: %s", msg, exc)
                raise

        return async_wrapper if tt._utils.is_coroutine_callable(fn) else sync_wrapper
    return decorator
