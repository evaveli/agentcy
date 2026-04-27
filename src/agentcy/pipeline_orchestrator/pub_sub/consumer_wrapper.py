#src/agentcy/pipeline_orchestrator/pub_sub/consumer_wrapper.py

import asyncio
import copy
import json
import logging
import os

from aio_pika import ExchangeType
import aio_pika
from agentcy.pipeline_orchestrator.pub_sub.control_channels import control_channel_names
from agentcy.pipeline_orchestrator.pub_sub.pika_queue import AsyncPipelineConsumerManager
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.forwarder import ForwarderInterface
logger = logging.getLogger(__name__)

RUN_SUFFIX_ENABLED = os.getenv("PIPELINE_RUN_SUFFIX", "0") != "0"
EXIT_ON_STOP = os.getenv("AGENT_RUNTIME_EXIT_ON_STOP", "1") != "0"

# Helper exposed for tests (patched)
def get_final_config(rm, username: str, pipeline_id: str) -> dict:
    if hasattr(rm, "pipeline_store") and rm.pipeline_store:
        return rm.pipeline_store.get_final_config(username, pipeline_id)
    raise RuntimeError("ResourceManager missing pipeline_store; cannot load final config")


def get_tasks_for_service_name(service_name: str, fc: dict) -> list[str]:
        """
        Given a service_name (e.g. 'service_6'), return all task IDs 
        whose 'service_name' field matches it (e.g. ['task_2', 'task_6']).
        """
        matching_tasks = []
        task_dict: dict = fc.get("task_dict", {})
        for task_id, info in task_dict.items():
            if info.get("available_services") == service_name or info.get("service_name") == service_name:
                matching_tasks.append(task_id)
        return matching_tasks

def get_subscribe_queues_for_task(task_id: str, fc: dict) -> list[str]:
    """
    Return all queue names from final_config["queues"] that have 'to_task' == task_id.
    Example: if 'to_task' == 'task_4', we might see queue_task_1_to_task_4, etc.
    """
    subscribe_queues = []
    queues_dict : dict = fc.get("queues", {})
    for queue_name, qinfo in queues_dict.items():
        if qinfo["to_task"] == task_id:
            subscribe_queues.append(queue_name)
   
    return subscribe_queues


def generate_final_config_for(service_id: str, queue_names: list[str], base_config: dict) -> dict:
    """
    Given:
      - service_id: e.g. "service_6"
      - queue_names: a list of queue names that deliver to tasks belonging to 'service_id'
      - base_config: your full final_config, containing "queues", "rabbitmq_configs", "fan_in_metadata", etc.
    
    Returns a minimal final_config *compatible with AsyncPipelineConsumerManager* that includes:
      1) A "queues" mapping for each queue name in queue_names
      2) The "rabbitmq_configs" entries relevant to those queues
      3) The "fan_in_metadata" for tasks that belong to service_id (so aggregator can do fan-in)
    
    This ensures aggregator-based consumption will work without missing exchange info or fan-in requirements.
    """

    queues_subset = {}
    all_queues = base_config.get("queues", {})
    for qn in queue_names:
        if qn in all_queues:
            queues_subset[qn] = dict(all_queues[qn])

    rabbitmq_subset = []
    for entry in base_config.get("rabbitmq_configs", []):
        if entry["rabbitmq"].get("queue") in queue_names:
            rabbitmq_subset.append(entry)

    # 3) Build 'fan_in_metadata' subset for tasks of this service
    fan_in_subset = {}
    task_dict = base_config.get("task_dict", {})
    # tasks owned by this service (support both 'available_services' and legacy 'service_name')
    service_tasks = [
        t_id for t_id, tinfo in task_dict.items()
        if tinfo.get("available_services") == service_id or tinfo.get("service_name") == service_id
    ]

    for t_id in service_tasks:
        fi_data = base_config.get("fan_in_metadata", {}).get(t_id)
        if fi_data:
            fan_in_subset[t_id] = dict(fi_data)

    subscribers_subset = {}
    base_subs = base_config.get("subscribers", {})
    for t_id in service_tasks:
        if t_id in base_subs:
            subscribers_subset[t_id] = copy.deepcopy(base_subs[t_id])

    # 5) Minimal task_dict subset (only our tasks)
    task_subset = {t_id: copy.deepcopy(task_dict[t_id]) for t_id in service_tasks}

    return {
        "queues": queues_subset,
        "rabbitmq_configs": rabbitmq_subset,
        "fan_in_metadata": fan_in_subset,
        "subscribers": subscribers_subset,
        "task_dict": task_subset,
    }

class ConsumerManager:

    def __init__(
        self,
        service_name: str,
        rm: ResourceManager,
        username: str,
        pipeline_id: str,
        pipeline_run_id: str,
        *,
        shutdown_event: asyncio.Event | None = None,
    ):
        self.service_name = service_name
        self.rm = rm
        self.username = username
        self.pipeline_id = pipeline_id
        self.pipeline_run_id = pipeline_run_id
        self._shutdown_event = shutdown_event
        store = getattr(rm, "pipeline_store", None)

        # Fetch the final pipeline config (raises HTTPException if missing)
        if store is not None:
            base_cfg = store.get_final_config(self.username, self.pipeline_id)
        else:
            # helper can be patched in tests
            base_cfg = get_final_config(rm, self.username, self.pipeline_id)

        self._base_config = base_cfg
        self.final_config = self._build_final_config_for_service(service_name, self._base_config)
        self._manager = AsyncPipelineConsumerManager(self.final_config, rm)
        self._loop = asyncio.get_event_loop()

    async def start_async(self) -> None:
        logger.info(
            "Starting consumers for service=%s run=%s queues=%s",
            self.service_name,
            self.pipeline_run_id,
            [q for q in self.final_config.get("queues", {}).keys()],
        )
        await asyncio.gather(
            self._declare_shutdown_resource(),
            self._manager.start_consumers(),
        )



    def _build_final_config_for_service(
        self,
        service_name: str,
        base_config: dict,
    ) -> dict:
        tasks_for_service = get_tasks_for_service_name(service_name, base_config)

        all_queues: list[str] = []
        for t_id in tasks_for_service:
            all_queues.extend(get_subscribe_queues_for_task(t_id, base_config))
        all_queues = list(set(all_queues))

        mini_config = generate_final_config_for(service_name, all_queues, base_config)

        # keep only this service's fan-in metadata (optional but safer)
        fim_all = base_config.get("fan_in_metadata", {}) or {}
        mini_config["fan_in_metadata"] = {
            t_id: dict(fim_all[t_id]) for t_id in tasks_for_service if t_id in fim_all
        }

        # Deepcopy before mutating
        mini_config["queues"] = {qn: copy.deepcopy(q) for qn, q in mini_config.get("queues", {}).items()}
        mini_config["rabbitmq_configs"] = [copy.deepcopy(e) for e in mini_config.get("rabbitmq_configs", [])]

        # ---- (A) optional per-run suffixing (disabled by default for compatibility)
        if RUN_SUFFIX_ENABLED and self.pipeline_run_id:
            suffixed_queues = {}
            qname_map = {}  # old -> new
            for qn, qinfo in mini_config["queues"].items():
                new_qn = qn if qn.endswith(f".{self.pipeline_run_id}") else f"{qn}.{self.pipeline_run_id}"
                qname_map[qn] = new_qn
                qinfo["queue_name"] = new_qn
                qinfo["per_run"] = True
                suffixed_queues[new_qn] = qinfo
            mini_config["queues"] = suffixed_queues

            # ---- (B) update rabbit configs (queue + routing key)
            for cfg in mini_config["rabbitmq_configs"]:
                rb = cfg["rabbitmq"]
                old_qn = rb["queue"]
                rb["queue"] = qname_map.get(old_qn, old_qn)

                rk_base = (rb.get("routing_key") or old_qn)
                rb["routing_key"] = (
                    rk_base if rk_base.endswith(f".{self.pipeline_run_id}")
                    else f"{rk_base}.{self.pipeline_run_id}"
                )
                rb["per_run"] = True
        else:
            # keep queue names as-is, ensure queue_name set
            mini_config["queues"] = {
                qn: {**qinfo, "queue_name": qinfo.get("queue_name", qn)}
                for qn, qinfo in mini_config.get("queues", {}).items()
            }

        mini_config["run_id"] = self.pipeline_run_id
        mini_config["service_name"] = self.service_name
        mini_config["pipeline_id"] = self.pipeline_id
        mini_config["username"] = self.username

        logger.info(
            "Consumer config for %s: %d queues, %d rabbit bindings, fan-in for %s",
            self.service_name,
            len(mini_config.get("queues", {})),
            len(mini_config.get("rabbitmq_configs", [])),
            list(mini_config.get("fan_in_metadata", {}).keys()),
        )

        return mini_config



    
    def register_forwarder(self, forwarder: ForwarderInterface):
        """
        Register an externally provided forwarder.
        """
        self._manager.register_forwarder(forwarder)
    
    async def _declare_shutdown_resource(self):
        control_exchange_name, control_queue_name, routing_key = control_channel_names(
            service_name=self.service_name,
            username=self.username,
            pipeline_id=self.pipeline_id,
            pipeline_run_id=self.pipeline_run_id,
        )

        if self.rm.rabbit_mgr is None:
            raise RuntimeError("ResourceManager.rabbit_mgr is not initialized (None).")

        async with self.rm.rabbit_mgr.get_channel() as channel:
            exchange = await channel.declare_exchange(
            control_exchange_name,
            ExchangeType.DIRECT,
            durable=True
            )
            queue = await channel.declare_queue(
            control_queue_name,
            durable=True
            )
            await queue.bind(exchange, routing_key=routing_key)

            # Handler for incoming "stop" messages on this control queue
            async def _handle_shutdown_message(message: aio_pika.abc.AbstractIncomingMessage):
                async with message.process(requeue=False):
                    try:
                        body_str = message.body.decode("utf-8")
                        msg_data = json.loads(body_str)

                        if msg_data.get("command") == "stop":
                            # If your "stop" logic is to shut down all tasks in manager
                            print("[Control] Received stop command; shutting down consumers.")
                            await self._manager.stop_consumers()
                            if self._shutdown_event is not None and EXIT_ON_STOP:
                                logger.info(
                                    "Stop command received; shutting down runtime for service=%s run=%s",
                                    self.service_name,
                                    self.pipeline_run_id,
                                )
                                self._shutdown_event.set()
                    except Exception as e:
                        print(f"[Control] Error processing shutdown message: {e}")
            await queue.consume(_handle_shutdown_message, no_ack=False)

    def start(self) -> None:
        async def _bootstrap():
            await asyncio.gather(
                self._declare_shutdown_resource(),   # control-queue
                self._manager.start_consumers()      # launch all queue consumers
            )

        self._loop.run_until_complete(_bootstrap())
