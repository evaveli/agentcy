import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, max_failures: int = 5, reset_timeout: int = 60, success_threshold: int = 3):
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold
        self.failures = 0
        self.successes = 0
        self.open = False
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        self.successes = 0
        if self.failures >= self.max_failures:
            if not self.open:
                # match test expectation (string literal without formatting args)
                logger.error(f"Circuit breaker opened after {self.max_failures} failures")
            self.open = True
            self._opened_at = time.time()

    def record_success(self) -> None:
        self.successes += 1
        if self.open and self.successes >= self.success_threshold:
            self.reset()

    def is_open(self) -> bool:
        if not self.open:
            return False
        if (time.time() - self._opened_at) >= self.reset_timeout:
            self.reset()
            return False
        return True

    def reset(self) -> None:
        self.failures = 0
        self.successes = 0
        self.open = False
        self._opened_at = 0.0
