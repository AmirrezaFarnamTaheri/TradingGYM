"""
Async wrapper around EnhancedAbanClient.

Provides a minimal interface used by the poller and other async callers
without forcing the official SDK to be async. Routes all requests through
the global rate limiter for proper throttling.
"""

import asyncio
from typing import Any, Dict, Optional

from .enhanced_aban_client import EnhancedAbanClient
from trading_gym.rate_limit.global_limiter import GlobalRateLimiter
from trading_gym.rate_limit.models import RequestPriority


class RateLimitedAbanSDK:
    """
    Minimal async facade for EnhancedAbanClient.

    Only implements the methods currently used by apps/poller/main.py.
    Extend as needed for new async use cases.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        self._client = EnhancedAbanClient(api_key=api_key, base_url=base_url)

    async def get_ticker(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Fetch ticker(s) asynchronously with rate limiting.

        If symbol is None, returns all tickers; otherwise returns selected.
        All requests are routed through the global rate limiter.
        """

        async def _call() -> Dict[str, Any]:
            """Async wrapper that runs the blocking API call in a thread pool."""
            return await asyncio.to_thread(self._client.get_ticker, symbol)  # type: ignore[arg-type]

        # Route through global rate limiter with HIGH priority for market data
        limiter = GlobalRateLimiter.get_instance()
        return await limiter.execute_request(_call, priority=RequestPriority.HIGH)
