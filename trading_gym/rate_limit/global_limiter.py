import time
import threading
import logging
import asyncio
from collections import deque

logger = logging.getLogger(__name__)


class GlobalRateLimiter:
    """
    Thread-safe and Async-safe global rate limiter.
    Configured for Abantether: 50 requests/minute.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GlobalRateLimiter, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls):
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self.max_calls = 50
        self.period_seconds = 60
        self.calls = deque()
        self.lock = threading.Lock()
        self._initialized = True
        logger.info(
            f"GlobalRateLimiter: {self.max_calls} reqs / {self.period_seconds} sec"
        )

    def wait(self):
        """Blocking wait for synchronous code."""
        with self.lock:
            self._cleanup_calls()
            if len(self.calls) >= self.max_calls:
                sleep_time = self._get_sleep_time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                self._cleanup_calls()  # Cleanup again after sleep
            self.calls.append(time.time())

    async def execute_request(self, func, *args, **kwargs):
        """Execute an async function with rate limiting, using same lock for thread safety."""
        # Remove priority from kwargs (not used in current implementation)
        kwargs.pop("priority", None)

        # Acquire the same lock in an executor to avoid blocking the event loop
        await asyncio.to_thread(self.lock.acquire)
        try:
            self._cleanup_calls()
            if len(self.calls) >= self.max_calls:
                sleep_time = self._get_sleep_time()
                self.lock.release()  # release lock before sleeping
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                await asyncio.to_thread(self.lock.acquire)
                self._cleanup_calls()
            self.calls.append(time.time())
        finally:
            self.lock.release()
        return await func(*args, **kwargs)

    def _cleanup_calls(self):
        """Remove timestamps older than the period."""
        now = time.time()
        while self.calls and now - self.calls[0] > self.period_seconds:
            self.calls.popleft()

    def _get_sleep_time(self) -> float:
        """Calculate time to sleep if limit is reached."""
        if not self.calls:
            return 0.0
        now = time.time()
        return max(0.0, self.period_seconds - (now - self.calls[0]))

    def get_usage(self) -> float:
        with self.lock:
            self._cleanup_calls()
            return len(self.calls) / self.max_calls
