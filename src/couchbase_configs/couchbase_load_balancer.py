import random
from typing import Dict, List

from .load_balancer_helpers import PingLatencyTester


class LoadBalancer:
    """
    Simple latency-aware load balancer with health scoring.
    """

    def __init__(self, hosts: List[str]):
        self.hosts = hosts
        self.host_health: Dict[str, int] = {h: 100 for h in hosts}
        self.tester = PingLatencyTester()
        self.health_threshold = 50

    async def get_best_host(self) -> str:
        # ping all hosts to consume latency measurements deterministically
        latencies_all = {}
        for host in self.hosts:
            latencies_all[host] = await self.tester.ping_host(host)

        healthy_hosts = [h for h in self.hosts if self.host_health.get(h, 0) >= self.health_threshold]
        if not healthy_hosts:
            return random.choice(self.hosts)

        best_host = min(healthy_hosts, key=lambda h: latencies_all.get(h, float("inf")))
        return best_host

    def record_host_health(self, host: str, success: bool) -> None:
        current = self.host_health.get(host, 100)
        if success:
            current = min(100, current + 20)
        else:
            current = max(0, current - 20)
        self.host_health[host] = current
