"""
Memory-Efficient Data Structures
=================================

Bounded collections to prevent memory growth in long-running processes.
"""

from collections import deque
from typing import Any, Dict, Generic, List, Optional, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """
    Fixed-size ring buffer using deque.

    When full, oldest items are automatically dropped.

    Example:
        >>> buffer = RingBuffer(maxlen=1000)
        >>> for i in range(2000):
        ...     buffer.append(i)
        >>> len(buffer)
        1000
    """

    def __init__(self, maxlen: int):
        self._buffer = deque(maxlen=maxlen)
        self.maxlen = maxlen

    def append(self, item: T) -> None:
        """Add item to buffer."""
        self._buffer.append(item)

    def extend(self, items: List[T]) -> None:
        """Add multiple items."""
        self._buffer.extend(items)

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self):
        return iter(self._buffer)

    def __getitem__(self, index):
        return self._buffer[index]

    def clear(self) -> None:
        """Clear all items."""
        self._buffer.clear()

    def to_list(self) -> List[T]:
        """Convert to list."""
        return list(self._buffer)


class BoundedDict(Generic[T]):
    """
    Dictionary with maximum size limit using LRU eviction.

    When full, least recently used items are removed.

    Example:
        >>> cache = BoundedDict(maxsize=100)
        >>> for i in range(200):
        ...     cache[f"key_{i}"] = i
        >>> len(cache)
        100
    """

    def __init__(self, maxsize: int):
        self._dict: Dict[Any, T] = {}
        self._access_order: deque = deque()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        # Remove if exists (to update access order)
        if key in self._dict:
            self._access_order.remove(key)

        # Add to dict and access order
        self._dict[key] = value
        self._access_order.append(key)

        # Evict if over limit
        if len(self._dict) > self.maxsize:
            oldest_key = self._access_order.popleft()
            del self._dict[oldest_key]

    def __getitem__(self, key):
        # Update access order
        if key in self._dict:
            self._access_order.remove(key)
            self._access_order.append(key)
        return self._dict[key]

    def __contains__(self, key):
        return key in self._dict

    def __len__(self):
        return len(self._dict)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self):
        """Clear all items."""
        self._dict.clear()
        self._access_order.clear()


def get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        import psutil
        import os

        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return -1.0


def check_memory_growth(
    baseline_mb: float,
    current_mb: Optional[float] = None,
    threshold_pct: float = 20.0,
) -> bool:
    """
    Check if memory has grown beyond threshold.

    Args:
        baseline_mb: Baseline memory in MB
        current_mb: Current memory (if None, will measure)
        threshold_pct: Max % growth allowed

    Returns:
        True if memory growth exceeds threshold
    """
    if current_mb is None:
        current_mb = get_memory_usage_mb()

    if baseline_mb <= 0 or current_mb <= 0:
        return False  # Can't measure

    growth_pct = ((current_mb - baseline_mb) / baseline_mb) * 100
    return growth_pct > threshold_pct
