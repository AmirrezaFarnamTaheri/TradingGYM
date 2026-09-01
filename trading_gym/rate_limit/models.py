from dataclasses import dataclass
from enum import IntEnum


class RequestPriority(IntEnum):
    LOW = 3
    NORMAL = 2
    HIGH = 1
    CRITICAL = 0


@dataclass
class RateLimitConfig:
    requests_per_minute: int
    burst_capacity: int
