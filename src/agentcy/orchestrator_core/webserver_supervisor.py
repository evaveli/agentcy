#src/agentcy/orchestrator_core/webserver_supervisor.py

import asyncio, logging, random
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

async def supervise(
    name: str,
    coro_fn: Callable[..., Awaitable[None]],
    *args,
    initial_backoff: float = 1.0,
    max_backoff: float = 60.0,
    restart_on_exit: bool = True,
    **kwargs,
) -> None:
    backoff = initial_backoff
    first_run = True

    while True:
        try:
            logger.info("▶️  Starting %s", name)
            await coro_fn(*args, **kwargs)

            if not restart_on_exit:
                logger.info("%s completed; not restarting", name)
                return

            logger.warning("%s exited normally — restarting immediately", name)
            backoff = initial_backoff
        except asyncio.CancelledError:
            logger.info("%s cancelled; shutting down", name)
            raise
        except Exception:
            if first_run:
                raise                                    # fail fast on boot
            logger.exception("%s crashed — retry in %.1fs", name, backoff)
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, max_backoff)
        finally:
            first_run = False


