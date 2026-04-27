#src/agentcy/parsing_layer/generate_rabbitmq_manifests.py

import json
import yaml
import os
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Constants
TEMPLATE_DIR = 'templates'
#TODO: No. 1 later this will just be saved into a database
OUTPUT_FILE = 'rabbitmq_config.yaml'
#PIPELINE_FILE = './src/parsing_layer/pipeline.json'
#PIPELINE_FILE = 'pipeline.json'
#VHOST = '/'  # Default vhost; modify if needed

class PipelineGenerator:

    """
    Generates RabbitMQ configuration and deployment manifests for a processing pipeline.
    
    The class follows a two-stage generation process:
    1. Entry Point Configuration: Creates initial access point for external systems
    2. Core Pipeline Configuration: Generates task-specific RabbitMQ components

    Key Responsibilities:
    - Creates RabbitMQ exchanges, queues, and bindings from pipeline definitions
    - Implements error handling patterns (DLX/DLQ) with configurable retry policies
    - Generates Kubernetes deployment specifications for pipeline services
    - Renders configuration through Jinja templates for infrastructure-as-code

    Flow:
    1. Initialization: Accepts pipeline configuration including DAG structure and error handling
    2. Entry Point Generation:
       - Direct exchange for pipeline input
       - Entry queue with no DLX/DLQ for initial messages
       - Binding with 'start' routing key
    3. Core Component Generation:
       - Task-specific exchanges based on suggested types
       - Queues with dead-letter handling and message TTL
       - Bindings with routing logic and fan-in support
    4. Template Rendering:
       - Combines entry and core components
       - Validates YAML output
       - Produces final infrastructure configuration

    Features:
    - Idempotent configuration generation
    - Template-safe YAML escaping
    - Pipeline-specific resource naming with UUIDs
    - Validation of rendered YAML syntax
    - Configurable vhost and retry policies

    Usage:
    >>> config = {...}  # Pipeline configuration dictionary
    >>> generator = PipelineGenerator(config)
    >>> generator.generate_rabbitmq_config()

    Output Structure:
    - Exchanges (entry point first)
    - Queues with DLX/DLQ configurations
    - Bindings (entry point first, then task-specific)
    - Kubernetes deployments for pipeline services

    Templates:
    - exchanges.yaml.j2: Exchange declarations (RabbitMQ CRDs)
    - queues.yaml.j2: Queue definitions with DLX/DLQ patterns
    - bindings.yaml.j2: Routing relationships between components
    """

    def __init__(self, pipeline_config: dict):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.template_dir = os.path.join(base_dir, TEMPLATE_DIR)
        self.pipeline_config = pipeline_config

    def load_pipeline(self, pipeline_file: str):
        with open(pipeline_file, 'r') as f:
            return json.load(f)

    def setup_jinja_environment(self, template_dir: str) -> Environment:
        return Environment(
            loader=FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=select_autoescape(['yaml', 'yml'])
        )

    def generate_exchanges(self, task_dict: dict, exchange_suggestions: dict) -> list:
        exchanges = []
        for task_id, suggestion in exchange_suggestions.items():
            exchanges.append({
                'name': f"exchange_{task_id}",
                'vhost': self.pipeline_config['vhost'],
                'type': suggestion['exchange_type'],
                'durable': True
            })
        return exchanges

    def generate_queues(self,queues_dict, error_handling):

        aggregated_queues = []
        message_ttl = error_handling.get('retry_policy', {}).get('message_ttl')

        for queue_key, details in queues_dict.items():
            main_queue_name = details['queue_name']
            
            # Generate DLX / DLQ names
            dlx_name = f"dlx_{main_queue_name}"
            dlq_name = f"{main_queue_name}_dlq"

            dlx_obj = {
                "name": dlx_name,
                "vhost": self.pipeline_config['vhost'],
                "type": "fanout",
                "durable": True,
                "arguments": {}
            }

            # Build the DLQ object
            dlq_obj = {
                "name": dlq_name,
                "vhost": self.pipeline_config['vhost'],
                "type": "queue",
                "durable": True,
                "arguments": {}
            }

            # Build the main queue object
            queue_arguments = {
                "x-dead-letter-exchange": dlx_name
            }
            if message_ttl:
                queue_arguments["x-message-ttl"] = message_ttl

            main_queue_obj = {
                "name": main_queue_name,
                "vhost": self.pipeline_config['vhost'],
                "type": "queue",
                "durable": True,
                "arguments": queue_arguments
            }

            # Combine them into a single dictionary describing this queue group
            aggregated_queues.append({
                "name": main_queue_name,  # Just a reference field for clarity
                "dlx": dlx_obj,
                "dlq": dlq_obj,
                "queue": main_queue_obj
            })

        return aggregated_queues


    def generate_bindings(self, rabbitmq_configs, fan_in_metadata, aggregated_queues):
        bindings = []
        for config in rabbitmq_configs:
            rabbitmq = config['rabbitmq']
            task_id = config['task_id']
            binding = {
                'name': f"binding_{task_id}_{rabbitmq['queue']}",
                'vhost': self.pipeline_config['vhost'],
                'source': rabbitmq['exchange'],
                'destination': rabbitmq['queue'],
                'destinationType': 'queue',
                'routingKey': rabbitmq.get('routing_key', '')
            }

            # If the task is a fan-in step, add binding arguments
            if task_id in fan_in_metadata:
                fan_in_data = fan_in_metadata[task_id]
                binding['routingKey'] = ""  # Adjust if necessary
                binding['arguments'] = {
                    'correlation_id': fan_in_data.get('correlation_id', ''),
                    'timeout': fan_in_data.get('timeout', 300)
                }
            bindings.append(binding)
            # ----------------------------------
            # 2) DLX -> DLQ Bindings
            # ----------------------------------
        for qgroup in aggregated_queues:
            dlx_name = qgroup["dlx"]["name"]  # e.g. "dlx_queue_task_1_to_task_2"
            dlq_name = qgroup["dlq"]["name"]  # e.g. "queue_task_1_to_task_2_dlq"

            # Create a binding from the DLX (fanout exchange) to the DLQ
            dlx_binding = {
                'name': f"binding_{dlx_name}_to_{dlq_name}",
                'vhost': qgroup["dlx"]["vhost"],  # should be "/"
                'source': dlx_name,
                'destination': dlq_name,
                'destinationType': 'queue',
                'routingKey': ""  # Usually empty for fanout
            }
            bindings.append(dlx_binding)
            
        return bindings

    def render_template(self, template_name, data):

        env = Environment(
            loader=FileSystemLoader(searchpath=self.template_dir),
            autoescape=select_autoescape(['yaml', 'yml'])
        )
        template = env.get_template(template_name)
        rendered_yaml = template.render(data)

        try:
            yaml.safe_load_all(rendered_yaml)
        except yaml.YAMLError as exc:
            print("Error in rendered YAML:", exc)
            raise
        return rendered_yaml

    def generate_rabbitmq_config(self, write_file: bool = False) -> dict:
        """
        Generate RabbitMQ topology configuration.

        Returns a dict containing:
        - topology: structured data (exchanges, queues, bindings)
        - yaml_manifest: rendered YAML string for Kubernetes CRDs

        Args:
            write_file: If True, also writes to OUTPUT_FILE (for backwards compatibility)
        """
        # 1. Load pipeline data
        pipeline_data = self.pipeline_config
        entry_exchanges, entry_queues, entry_bindings = self.generate_entry_point()
        # 2. Generate Exchanges, Queues, and Bindings
        exchanges = self.generate_exchanges(
            pipeline_data['task_dict'],
            pipeline_data['exchange_suggestions']
        )
        queue_collections = self.generate_queues(
            pipeline_data['queues'],
            pipeline_data['error_handling']
        )
        bindings = self.generate_bindings(
            pipeline_data['rabbitmq_configs'],
            pipeline_data['fan_in_metadata'],
            queue_collections
        )

        # Ensure entry_exchanges, entry_queues, entry_bindings are lists
        entry_exchanges = list(entry_exchanges) if not isinstance(entry_exchanges, list) else entry_exchanges
        entry_queues = list(entry_queues) if not isinstance(entry_queues, list) else entry_queues
        entry_bindings = list(entry_bindings) if not isinstance(entry_bindings, list) else entry_bindings

        all_exchanges = entry_exchanges + exchanges
        all_queues = entry_queues + queue_collections
        all_bindings = entry_bindings + bindings

        # 3. Setup Jinja environment (for partial or final outputs)
        env = self.setup_jinja_environment(self.template_dir)

        # Prepare data for each template
        context_exchanges = {'exchanges': all_exchanges}
        context_queues = {'queues': all_queues}
        context_bindings = {'bindings': all_bindings}

        # 4. Render templates (assuming you have exchanges.yaml.j2, queues.yaml.j2, bindings.yaml.j2)
        rendered_exchanges = self.render_template('exchanges.yaml.j2', context_exchanges)
        rendered_queues = self.render_template('queues.yaml.j2', context_queues)
        rendered_bindings = self.render_template('bindings.yaml.j2', context_bindings)

        # 5. Combine all rendered YAML
        combined_yaml = "\n".join([rendered_exchanges, rendered_queues, rendered_bindings])

        # Build result with structured topology data
        result = {
            "topology": {
                "exchanges": all_exchanges,
                "queues": all_queues,
                "bindings": all_bindings,
            },
            "yaml_manifest": combined_yaml,
        }

        # Write to file only if explicitly requested (backwards compatibility)
        if write_file:
            with open(OUTPUT_FILE, 'w') as f:
                f.write(combined_yaml)
            print(f"RabbitMQ configuration successfully generated in '{OUTPUT_FILE}'.")

        return result

    def generate_entry_point(self) -> tuple[list[dict], list[dict], list[dict]]:
        """Generates the initial exchange, queue, and binding for pipeline entry."""
        # Entry exchange configuration
        entry_exchange = {
            'name': f"pipeline_entry_{self.pipeline_config['pipeline_id']}",
            'vhost': self.pipeline_config['vhost'],
            'type': 'direct',
            'durable': True
        }

        # Entry queue configuration (without DLX/DLQ)
        entry_queue_group = {
            'name': f"pipeline_entry_queue_{self.pipeline_config['pipeline_id']}",
            'dlx': None,
            'dlq': None,
            'queue': {
                'name': f"pipeline_entry_queue_{self.pipeline_config['pipeline_id']}",
                'vhost': self.pipeline_config['vhost'],
                'type': 'queue',
                'durable': True,
                'arguments': {}
            }
        }

        # Binding between entry exchange and queue
        entry_binding = {
            'name': f"binding_entry_to_queue_{self.pipeline_config['pipeline_id']}",
            'vhost': self.pipeline_config['vhost'],
            'source': f"pipeline_entry_{self.pipeline_config['pipeline_id']}",
            'destination': f"pipeline_entry_queue_{self.pipeline_config['pipeline_id']}",
            'destinationType': 'queue',
            'routingKey': 'start'
        }

        return [entry_exchange], [entry_queue_group], [entry_binding]
            
