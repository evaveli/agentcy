import asyncio
import random
from typing import Optional


def ping(host: str) -> Optional[float]:
    """
    Placeholder for an actual ping implementation.
    Returns latency in seconds or None on failure.
    """
    return None


class PingLatencyTester:
    async def ping_host(self, host: str, num_trials: int = 3) -> float:
        """
        Perform several ping attempts and return the average latency in milliseconds.
        If ping() returns None, treat it as 1000 ms.
        """
        latencies = []
        for _ in range(num_trials):
            # ping() is synchronous; wrap in executor to avoid blocking event loop.
            val = await asyncio.get_running_loop().run_in_executor(None, ping, host)
            if val is None:
                latencies.append(1000.0)
            else:
                latencies.append(float(val) * 1000.0)
        return sum(latencies) / len(latencies)


__all__ = ["PingLatencyTester", "ping", "random"]
