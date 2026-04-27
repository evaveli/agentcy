#src/agentcy/rabbitmq_workflow/workflow_config_parser.py
from typing import Dict, List, Tuple, Set, Any
from collections import defaultdict, deque
from fastapi import HTTPException, status
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import PipelineConfig
from datetime import datetime
import yaml
import os

class ConfigParser:
    def __init__(self, dag_config=None, pipeline_id=None):
        # Ensure dag_config is a plain dictionary.
        if dag_config is not None and not isinstance(dag_config, dict):
            try:
                dag_config = dag_config.model_dump()
            except AttributeError:
                dag_config = dict(dag_config)
        self.dag_config = dag_config
        self.pipeline_id = pipeline_id
        self.task_outputs = defaultdict(set)  # To track which tasks depend on each task

    def __call__(self, dag_config=None):
        if dag_config:
            self.dag_config = dag_config
        return self

    def infer_dependencies_and_outputs(self) -> List[Dict]:
        """
        Infers dependencies based on 'inputs.dependencies' and deduces outputs dynamically.
        """
        tasks = self.dag_config.get('tasks', [])
        task_ids = {task['id'] for task in tasks}  # Set of valid task IDs

        for task in tasks:
            task_id = task['id']
            task_inputs = task.get('inputs', {})
            dependencies = set(task_inputs.get('dependencies', []))  # Directly get dependencies

            # Validate dependencies
            for dep in dependencies:
                if dep not in task_ids:
                    raise ValueError(f"Task '{task_id}' references non-existent task '{dep}'.")

                # Update task_outputs: dep is a task that 'task_id' depends on
                self.task_outputs[dep].add(task_id)

            # Assign dependencies to the task
            task['dependencies'] = list(dependencies)

        for task in tasks:
            task_id = task['id']
            task['inferred_outputs'] = list(self.task_outputs[task_id])

        print("Inferred Task Outputs:", dict(self.task_outputs))
        return tasks

    def detect_cycles_kahn(self, tasks: List[Dict]) -> None:
        """
        Detect circular dependencies using Kahn's Algorithm.
        Raises ValueError if a cycle is detected.
        """
        graph = defaultdict(list)
        in_degree = defaultdict(int)

        # Build graph and in-degree
        for task in tasks:
            task_id = task['id']
            for dep in task.get('dependencies', []):
                graph[dep].append(task_id)  # Edge from dep to task_id
                in_degree[task_id] += 1
            in_degree[task_id] = in_degree.get(task_id, 0)  # Ensure all tasks are in in_degree

        # Initialize queue with tasks having zero in-degree
        queue = deque([task_id for task_id, degree in in_degree.items() if degree == 0])
        processed_count = 0

        while queue:
            current = queue.popleft()
            processed_count += 1
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed_count != len(tasks):
            raise ValueError("Circular dependency detected in the DAG configuration")
    
    def validate_no_solo_nodes(self, tasks: List[Dict], task_graph: Dict[str, List[str]]) -> None:
        solo_nodes = []
        for task in tasks:
            task_id = task['id']
            dependencies = task.get('dependencies', [])
            dependents = task_graph.get(task_id, [])
            if not dependencies and not dependents:
                solo_nodes.append(task_id)

        if solo_nodes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Solo nodes detected (tasks with no dependencies and no dependents): {solo_nodes}"
                )

    
    def validate_single_connected_dag(self, tasks: List[Dict], task_graph: Dict[str, List[str]]) -> None:
        """
        Validates that all tasks form a single connected DAG.
        Raises ValueError if multiple disconnected subgraphs (DAGs) are detected.
        """
        if not tasks:
            raise ValueError("No tasks found in the DAG configuration.")

        # Perform BFS to find all reachable tasks from the first task
        visited = set()
        queue = deque([tasks[0]['id']])

        while queue:
            current = queue.popleft()
            if current not in visited:
                visited.add(current)
                queue.extend(task_graph[current])

        # Check if all tasks are visited
        all_task_ids = set(task['id'] for task in tasks)
        if visited != all_task_ids:
            disconnected_tasks = all_task_ids - visited
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Disconnected tasks detected: {disconnected_tasks}. Ensure all tasks are connected."
                )
        
       

    def parse_dag(self) -> Tuple[List[str], Dict[str, List[str]]]:
        """
        Parses the DAG to determine execution order and build the task graph.
        """
        tasks = self.dag_config.get('tasks', [])
        graph = defaultdict(list)
        in_degree = defaultdict(int)

        # Build the graph
        task_dict = {task['id']: task for task in tasks}
        for task in tasks:
            task_id = task['id']
            dependencies = task.get('dependencies', [])
            for dep in dependencies:
                graph[dep].append(task_id)
                in_degree[task_id] += 1

            # Ensure all tasks are in in_degree
            in_degree[task_id] = in_degree.get(task_id, 0)

        # Find tasks with zero in-degree (no dependencies)
        zero_in_degree = [task_id for task_id in task_dict if in_degree[task_id] == 0]
        execution_order = []

        # Perform topological sort to determine execution order
        queue = deque(zero_in_degree)
        while queue:
            current_task_id = queue.popleft()
            execution_order.append(current_task_id)
            for dependent_task_id in graph.get(current_task_id, []):
                in_degree[dependent_task_id] -= 1
                if in_degree[dependent_task_id] == 0:
                    queue.append(dependent_task_id)

        return execution_order, graph

    def find_parallel_tasks(self, execution_order: List[str], task_graph: Dict[str, List[str]]) -> List[List[str]]:
        """
        Identifies sets of tasks that can run in parallel.
        """
        parallel_tasks = []
        task_levels = {}
        level = 0
        queue = deque()

        # Initialize in-degree
        in_degree = defaultdict(int)
        for deps in task_graph.values():
            for dep in deps:
                in_degree[dep] += 1

        # Find all tasks with no dependencies
        initial_tasks = [task for task in execution_order if in_degree[task] == 0]
        for task in initial_tasks:
            task_levels[task] = level
            queue.append(task)

        # BFS to assign levels
        while queue:
            current = queue.popleft()
            current_level = task_levels[current]
            for neighbor in task_graph.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    task_levels[neighbor] = current_level + 1
                    queue.append(neighbor)

        # Group tasks by level
        levels = defaultdict(list)
        for task, lvl in task_levels.items():
            levels[lvl].append(task)

        # Identify parallel tasks by level
        for lvl, tasks_in_level in levels.items():
            if len(tasks_in_level) > 1:
                parallel_tasks.append(tasks_in_level)

        return parallel_tasks

    def identify_fan_in_steps(self, task_graph: Dict[str, List[str]], task_dict: Dict[str, Dict]) -> List[str]:
        """
        Identifies steps that are fan-in points (steps with multiple dependencies).
        """
        fan_in_steps = []
        for task_id, task in task_dict.items():
            dependencies = task.get('dependencies', [])
            if len(dependencies) > 1:
                fan_in_steps.append(task_id)
        return fan_in_steps

    def suggest_exchange_types(self, task_graph: Dict[str, List[str]], task_dict: Dict[str, Dict], fan_in_steps: List[str]) -> Dict[str, Dict]:
        """
        Suggests RabbitMQ exchange types based on task dependencies.
        """
        exchange_suggestions = {}

        # First, process all tasks in the graph
        for task_id, dependents in task_graph.items():
            num_dependents = len(dependents)
            if num_dependents == 1:
                exchange_type = 'direct'
                reason = 'Single dependent task.'
            elif num_dependents > 1:
                exchange_type = 'fanout'
                reason = 'Multiple dependent tasks (broadcast).'
            else:
                exchange_type = 'direct'  # Default or no exchange needed
                reason = 'No dependent tasks or end of chain.'

            exchange_suggestions[task_id] = {
                'exchange_type': exchange_type,
                'reason': reason,
                'task_name': task_dict[task_id].get('name', '')
            }

        # Ensure all fan-in steps are in the suggestions
        for fan_in_step in fan_in_steps:
            if fan_in_step not in exchange_suggestions:
                exchange_suggestions[fan_in_step] = {
                    'exchange_type': 'fanout',  # Placeholder, to be updated below
                    'reason': '',
                    'task_name': task_dict.get(fan_in_step, {}).get('name', '')
                }

            # Update fan-in step specifics
            exchange_suggestions[fan_in_step]['exchange_type'] = 'fanout'
            exchange_suggestions[fan_in_step]['reason'] = 'Fan-in step requiring aggregation of multiple upstream steps.'

        return exchange_suggestions


    def identify_publishers_subscribers_queues(
        self, task_graph: Dict[str, List[str]], task_dict: Dict[str, Dict]
    ) -> Tuple[Dict[str, Dict], Dict[str, Dict], Dict[str, Dict]]:
        """
        Identifies publishers, subscribers, and queues based on task dependencies.
        """
        publishers = {}
        subscribers = {}
        queues = {}

        for task_id, task in task_dict.items():
            # Identify subscribers (tasks that have dependencies)
            if 'dependencies' in task and task['dependencies']:
                dependencies = task['dependencies']
                subscribers[task_id] = {
                    'task_name': task.get('name', ''),
                    'subscriptions': dependencies
                }
                # Each dependency implies a queue from the dependency to this task
                for dep in dependencies:
                    queue_name = f"queue_{dep}_to_{task_id}"
                    queues[queue_name] = {
                        'from_task': dep,
                        'to_task': task_id,
                        'queue_name': queue_name
                    }

            # Identify publishers (tasks that have dependents)
            dependents = task_graph.get(task_id, [])
            if dependents:
                publishers[task_id] = {
                    'task_name': task.get('name', ''),
                    'publications': dependents
                }

        return publishers, subscribers, queues

    def prepare_rabbitmq_configs(
        self, exchange_suggestions: Dict[str, Dict], queues: Dict[str, Dict]
    ) -> List[Dict]:
        """
        Prepares RabbitMQ configurations for exchanges and queues.
        """
        rabbitmq_configs = []
        end_tasks_processed = set()

        for queue_name, queue_info in queues.items():
            from_task = queue_info['from_task']
            to_task = queue_info['to_task']

            # Retrieve exchange type from exchange_suggestions
            exchange_info = exchange_suggestions.get(from_task)
            if not exchange_info:
                raise ValueError(f"No exchange suggestion found for publisher task '{from_task}'.")

            exchange_type = exchange_info['exchange_type']
            task_name = exchange_info['task_name']

            # Define exchange name based on publisher task ID
            exchange_name = f"exchange_{from_task}"

            # Determine routing key based on exchange type
            if exchange_type == 'direct':
                routing_key = queue_name  # Use queue name as routing key
            elif exchange_type == 'fanout':
                routing_key = ''  # Routing key is ignored for fanout
            elif exchange_type == 'headers':
                routing_key = ''  # Headers exchanges typically ignore routing keys
            else:
                # Handle other exchange types if necessary
                routing_key = queue_name  # Default to queue name

            config = {
                'task_id': from_task,
                'rabbitmq': {
                    'exchange': exchange_name,
                    'exchange_type': exchange_type,
                    'queue': queue_name,
                    'routing_key': routing_key
                }
            }

            rabbitmq_configs.append(config)
        
            if not self.task_outputs.get(to_task):
                if to_task not in end_tasks_processed:
                    # Assign a default exchange type if not already suggested
                    if to_task not in exchange_suggestions:
                        exchange_suggestions[to_task] = {
                            'exchange_type': 'direct',  # Default to 'direct' for end tasks
                            'reason': 'End task (subscriber with no dependents).',
                            'task_name': self.task_outputs[to_task].get('task_name', '')
                        }

                    # Define exchange name for the end task
                    exchange_name_end = f"exchange_{to_task}"

                    # Define routing key for the end task's queue
                    # For 'direct' exchanges, routing key should match the queue name
                    routing_key_end = queue_info['queue_name']

                    # Create RabbitMQ configuration for the end task's queue
                    config_end = {
                        'task_id': to_task,
                        'rabbitmq': {
                            'exchange': exchange_name_end,
                            'exchange_type': exchange_suggestions[to_task]['exchange_type'],
                            'queue': queue_info['queue_name'],
                            'routing_key': routing_key_end
                        }
                    }

                    rabbitmq_configs.append(config_end)
                    end_tasks_processed.add(to_task)  # Mark as processed to avoid duplicates

        return rabbitmq_configs

    def parse_fan_in_metadata(self, fan_in_steps: List[str]) -> Dict[str, Dict]:
        """
        Parses and prepares metadata for fan-in steps.
        """
        fan_in_metadata = {}

        for fan_in_step in fan_in_steps:
            # Find the task in the DAG config by matching the id
            task = next((t for t in self.dag_config['tasks'] if t['id'] == fan_in_step), None)
            if not task:
                raise ValueError(f"Fan-in step '{fan_in_step}' not found in DAG configuration.")

            dependencies = task.get('dependencies', [])

            fan_in_metadata[fan_in_step] = {
                'required_steps': dependencies,
                'correlation_id': f"corr-{fan_in_step}-{self.pipeline_id}",
                'timeout': self.dag_config.get('error_handling', {}).get('timeout', 300)  # Default timeout 5 minutes
            }
        return fan_in_metadata

    def visualize_task_graph(self, tasks: List[Dict], task_graph: Dict[str, List[str]]):
        """
        Optional: Visualize the task graph using Graphviz.
        """
        try:
            import graphviz
        except ImportError:
            print("Graphviz is not installed. Skipping visualization.")
            return

        dot = graphviz.Digraph(comment='Task Graph')
        for task in tasks:
            dot.node(task['id'], task['name'])

        for task_id, dependents in task_graph.items():
            for dependent in dependents:
                dot.edge(task_id, dependent)

        dot.render('task_graph', format='png', view=False)
        print("Task graph visualization saved as 'task_graph.png'.")


    def parse_graph(self) -> Dict:
        """
        The main method to infer dependencies and parse the entire DAG configuration.
        """
        # Step 1: Infer dependencies and outputs
        tasks = self.infer_dependencies_and_outputs()
        print("Inferred Tasks with Dependencies and Outputs:")
        for task in tasks:
            print(f"Task ID: {task['id']}, Dependencies: {task['dependencies']}, Inferred Outputs: {task['inferred_outputs']}")

        # Step 2: Validate for circular dependencies
        self.detect_cycles_kahn(tasks)

        # Step 3: Parse DAG as before
        execution_order, task_graph = self.parse_dag()
        self.validate_no_solo_nodes(tasks, task_graph)
        self.validate_single_connected_dag(tasks, task_graph)
        parallel_tasks = self.find_parallel_tasks(execution_order, task_graph)
        task_dict = {task['id']: task for task in self.dag_config.get('tasks', [])}
        fan_in_steps = self.identify_fan_in_steps(task_graph, task_dict)
        exchange_suggestions = self.suggest_exchange_types(task_graph, task_dict, fan_in_steps)
        publishers, subscribers, queues = self.identify_publishers_subscribers_queues(task_graph, task_dict)
        rabbitmq_configs = self.prepare_rabbitmq_configs(exchange_suggestions, queues)
        fan_in_metadata = self.parse_fan_in_metadata(fan_in_steps)

        entry_queue_name = f"pipeline_entry_queue_{self.pipeline_id}"
        entry_exchange_name = f"pipeline_entry_{self.pipeline_id}"
        exchange_suggestions[entry_exchange_name] = {
        'exchange_type': 'direct',
        'reason': 'Pipeline entry point for external messages/triggers',
        'task_name': entry_exchange_name
        }
        queues[entry_queue_name] = {
        'from_task': None,
        'to_task': 'entry',
        'queue_name': entry_queue_name
        }

        rabbitmq_configs.append({
        'task_id': 'entry',
        'rabbitmq': {
            'exchange': entry_exchange_name,
            'exchange_type': 'direct',
            'queue': entry_queue_name,
            'routing_key': entry_queue_name
        }
        })
        # Optional: Visualize the task graph
        self.visualize_task_graph(tasks, task_graph)

        
        final_config = {
            'version': 1,
            'last_updated': datetime.now(),
            'execution_order': execution_order,
            'task_graph': task_graph,
            'parallel_tasks': parallel_tasks,
            'task_dict': task_dict,
            'fan_in_steps': fan_in_steps,
            'exchange_suggestions': exchange_suggestions,
            'publishers': publishers,
            'subscribers': subscribers,
            'queues': queues,
            'rabbitmq_configs': rabbitmq_configs,
            'fan_in_metadata': fan_in_metadata,
            'task_outputs': dict(self.task_outputs) 
        }


        return final_config


def parse_additional_info(data_model: PipelineConfig) -> Dict:

    pre_dict = data_model.model_dump()
    pipeline_metadata = pre_dict.pop("dag")
    return pipeline_metadata