import pytest
from unittest.mock import patch, AsyncMock
from src.couchbase_configs.couchbase_load_balancer import LoadBalancer
from src.couchbase_configs.load_balancer_helpers import PingLatencyTester

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.ping')
async def test_ping_host_success(mock_ping):
    
    mock_ping.return_value = 0.1  
    tester = PingLatencyTester()
    latency = await tester.ping_host('127.0.0.1', num_trials=3)

    assert pytest.approx(latency, 0.1) == 100
    assert mock_ping.call_count == 3

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.ping')
async def test_ping_host_failure(mock_ping):

    mock_ping.return_value = None  # Simulate ping failure
    tester = PingLatencyTester()
    latency = await tester.ping_host('127.0.0.1', num_trials=3)

    assert latency == 1000
    assert mock_ping.call_count == 3

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.ping')
async def test_ping_host_mixed(mock_ping):

    mock_ping.side_effect = [0.1, None, 0.2]
    tester = PingLatencyTester()
    latency = await tester.ping_host('127.0.0.1', num_trials=3)
    expected_latency = (100 + 1000 + 200) / 3

    assert pytest.approx(latency, 0.1) == expected_latency
    assert mock_ping.call_count == 3
    

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.PingLatencyTester.ping_host')
async def test_get_best_host_latency(mock_ping_host):

    mock_ping_host.side_effect = [0.1, 0.2, 0.15] 
    hosts = ['host1', 'host2', 'host3']
    lb = LoadBalancer(hosts)
    best_host = await lb.get_best_host()
    # host1 has the lowest latency, so it should be selected
    assert best_host == 'host1'

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.PingLatencyTester.ping_host')
async def test_get_best_host_health_affects_selection(mock_ping_host):

    mock_ping_host.side_effect = [0.1, 0.2, 0.15]
    hosts = ['host1', 'host2', 'host3']
    lb = LoadBalancer(hosts)
    lb.host_health['host1'] = 40
    best_host = await lb.get_best_host()

    assert best_host == 'host3'

@pytest.mark.asyncio
@patch('src.couchbase_configs.load_balancer_helpers.PingLatencyTester.ping_host')
async def test_get_best_host_no_healthy_host(mock_ping_host):

    mock_ping_host.side_effect = [0.1, 0.2, 0.15]
    hosts = ['host1', 'host2', 'host3']
    lb = LoadBalancer(hosts)
    lb.host_health['host1'] = 40
    lb.host_health['host2'] = 30
    lb.host_health['host3'] = 10

    with patch('random.choice', return_value='host2'):
        fallback_host = await lb.get_best_host()

    assert fallback_host == 'host2'


def test_record_host_health_success():

    hosts = ['host1']
    lb = LoadBalancer(hosts)
    lb.record_host_health('host1', success=True)
    assert lb.host_health['host1'] == 100

def test_record_host_health_failure():

    hosts = ['host1']
    lb = LoadBalancer(hosts)
    lb.record_host_health('host1', success=False)
    assert lb.host_health['host1'] == 80

def test_record_host_health_edges():

    hosts = ['host1']
    lb = LoadBalancer(hosts)
    lb.host_health['host1'] = 0
    lb.record_host_health('host1', success=False)
    assert lb.host_health['host1'] == 0
    lb.host_health['host1'] = 100
    lb.record_host_health('host1', success=True)
    assert lb.host_health['host1'] == 100
