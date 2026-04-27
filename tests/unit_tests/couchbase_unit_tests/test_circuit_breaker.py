import pytest
import time
from unittest.mock import patch
from src.couchbase_configs.couchbase_circuit_breaker import CircuitBreaker

def test_circuit_breaker_initialization():
    cb = CircuitBreaker()
    assert cb.failures == 0
    assert cb.max_failures == 5
    assert cb.reset_timeout == 60
    assert cb.open is False


def test_record_failure():
    cb = CircuitBreaker(max_failures=3)
    with patch('src.couchbase_configs.couchbase_circuit_breaker.logger') as mock_logger:
        cb.record_failure()
        assert cb.failures == 1
        cb.record_failure()
        assert cb.failures == 2
        cb.record_failure()
        assert cb.failures == 3
        assert cb.open is True
        mock_logger.error.assert_called_with("Circuit breaker opened after 3 failures")


def test_record_success():
    cb = CircuitBreaker(max_failures=3, success_threshold=3)
    cb.record_failure()
    cb.record_success()
    assert cb.successes == 1
    print(cb.successes)
    cb.record_success()
    print(cb.successes)
    assert cb.successes == 2
    assert cb.open is False 


def test_is_open():
    cb = CircuitBreaker(max_failures=3, reset_timeout=60)
    with patch('time.time', return_value=1000):
        cb.record_failure()
        assert cb.is_open() is False
        
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open() is True

        with patch('time.time', return_value=1070):
            assert cb.is_open() is False


def test_reset():
    cb = CircuitBreaker()
    cb.failures = 3
    cb.successes = 2
    cb.open = True
    cb.reset()
    assert cb.failures == 0
    assert cb.successes == 0
    assert cb.open is False
