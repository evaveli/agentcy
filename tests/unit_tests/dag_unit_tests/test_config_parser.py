#tests/unit_tests/dag_unit_tests/test_config_parser.py
import pytest
from collections import defaultdict, deque
from typing import Dict, List

from src.agentcy.rabbitmq_workflow.workflow_config_parser import ConfigParser



@pytest.fixture
def config_parser():
    """Fixture to create a ConfigParser instance."""
    return ConfigParser()

def test_parse_dag_empty_config(config_parser):
    """Test parse_dag with an empty configuration."""
    dag_config = {}
    # Correctly instantiate ConfigParser with `dag_config`
    config_parser = ConfigParser(dag_config)
    execution_order, graph = config_parser.parse_dag()
    assert execution_order == []
    assert graph == defaultdict(list)

def test_parse_dag_no_tasks_key(config_parser):
    """Test parse_dag when 'tasks' key is missing."""
    dag_config = {"other_key": []}
    execution_order, graph = config_parser(dag_config).parse_dag()
    assert execution_order == []
    assert graph == defaultdict(list)

def test_parse_dag_empty_tasks(config_parser):
    """Test parse_dag with an empty 'tasks' list."""
    dag_config = {"tasks": []}
    execution_order, graph = config_parser(dag_config).parse_dag()
    assert execution_order == []
    assert graph == defaultdict(list)

def test_parse_dag_no_dependencies():
    """Test parse_dag with tasks that have no dependencies."""
    config_parser = ConfigParser()  # Instantiate directly
    dag_config = {
        "tasks": [
            {"id": "task1", "name": "Task 1"},
            {"id": "task2", "name": "Task 2"},
            {"id": "task3", "name": "Task 3"},
        ]
    }
    execution_order, graph = config_parser(dag_config).parse_dag()
    print()
    # All tasks should be in execution_order, order is not guaranteed
    assert set(execution_order) == {"task1", "task2", "task3"}

    # Graph should have no edges (empty)
    expected_graph = defaultdict(list)
    assert graph == expected_graph

def test_parse_dag_linear_dependencies(config_parser):
    """Test parse_dag with linear dependencies (A → B → C)."""
    dag_config = {
        "tasks": [
            {"id": "A", "name": "Task A", "dependencies": []},
            {"id": "B", "name": "Task B", "dependencies": ["A"]},
            {"id": "C", "name": "Task C", "dependencies": ["B"]},
        ]
    }
    execution_order, graph = config_parser(dag_config).parse_dag()
    
    # Expected execution order: A, B, C
    assert execution_order == ["A", "B", "C"]
    
    # Expected graph:
    # A → B
    # B → C
    expected_graph = defaultdict(list)
    expected_graph["A"].append("B")
    expected_graph["B"].append("C")
    assert graph == expected_graph

def test_parse_dag_multiple_dependencies(config_parser):
    """Test parse_dag with tasks having multiple dependencies."""
    dag_config = {
        "tasks": [
            {"id": "A", "name": "Task A", "dependencies": []},
            {"id": "B", "name": "Task B", "dependencies": ["A"]},
            {"id": "C", "name": "Task C", "dependencies": ["A"]},
            {"id": "D", "name": "Task D", "dependencies": ["B", "C"]},
        ]
    }
    execution_order, graph = config_parser(dag_config).parse_dag()
    
    # Possible valid execution orders:
    # A, B, C, D
    # A, C, B, D
    assert execution_order[0] == "A"
    assert execution_order[-1] == "D"
    assert set(execution_order) == {"A", "B", "C", "D"}
    
    # Expected graph:
    # A → B, A → C
    # B → D, C → D
    expected_graph = defaultdict(list)
    expected_graph["A"].extend(["B", "C"])
    expected_graph["B"].append("D")
    expected_graph["C"].append("D")
    assert graph == expected_graph

def test_parse_dag_missing_dependencies(config_parser):
    """Test parse_dag with some tasks missing the 'dependencies' key."""
    dag_config = {
        "tasks": [
            {"id": "A", "name": "Task A"},
            {"id": "B", "name": "Task B", "dependencies": ["A"]},
            {"id": "C", "name": "Task C"},  # No dependencies
        ]
    }
    execution_order, graph = config_parser(dag_config).parse_dag()
    
    # Expected execution order: A, C, B or A, B, C
    assert execution_order[0] == "A"
    assert set(execution_order) == {"A", "B", "C"}
    # Depending on implementation, C can be before or after B
    
    # Expected graph:
    # A → B
    expected_graph = defaultdict(list)
    expected_graph["A"].append("B")
    assert graph == expected_graph


def test_parse_dag_complex_graph(config_parser):
    """Test parse_dag with a more complex DAG."""
    dag_config = {
        "tasks": [
            {"id": "A", "name": "Task A"},
            {"id": "B", "name": "Task B", "dependencies": ["A"]},
            {"id": "C", "name": "Task C", "dependencies": ["A"]},
            {"id": "D", "name": "Task D", "dependencies": ["B", "C"]},
            {"id": "E", "name": "Task E", "dependencies": ["C"]},
            {"id": "F", "name": "Task F", "dependencies": ["D", "E"]},
        ]
    }
    execution_order, graph = config_parser(dag_config).parse_dag()
    
    # Expected execution order could be:
    # A, B, C, D, E, F
    # A, C, B, D, E, F
    # A, C, E, B, D, F
    # etc., as long as dependencies are respected
    assert execution_order.index("A") < execution_order.index("B")
    assert execution_order.index("A") < execution_order.index("C")
    assert execution_order.index("B") < execution_order.index("D")
    assert execution_order.index("C") < execution_order.index("D")
    assert execution_order.index("C") < execution_order.index("E")
    assert execution_order.index("D") < execution_order.index("F")
    assert execution_order.index("E") < execution_order.index("F")
    
    # Expected graph:
    expected_graph = defaultdict(list)
    expected_graph["A"].extend(["B", "C"])
    expected_graph["B"].append("D")
    expected_graph["C"].extend(["D", "E"])
    expected_graph["D"].append("F")
    expected_graph["E"].append("F")
    assert graph == expected_graph
