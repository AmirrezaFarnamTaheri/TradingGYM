"""
Health Checks
=============

Readiness and liveness probes for orchestration.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict


class HealthStatus(str, Enum):
    """Health check status."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    name: str
    status: HealthStatus
    message: str
    latency_ms: float
    details: Dict = None

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "details": self.details or {},
        }


class HealthCheck:
    """
    Health check system with readiness and liveness probes.

    Example:
        >>> health = HealthCheck()
        >>> health.register("api", check_api_connection)
        >>> health.register("model", check_model_loaded)
        >>>
        >>> # Check readiness
        >>> if health.is_ready():
        ...     print("Ready to serve traffic")
        >>>
        >>> # Get detailed status
        >>> status = health.get_status()
    """

    def __init__(self):
        self._checks: Dict[str, Callable[[], bool]] = {}
        self._last_results: Dict[str, HealthCheckResult] = {}

    def register(
        self,
        name: str,
        check_func: Callable[[], bool],
        required_for_readiness: bool = True,
    ) -> None:
        """
        Register a health check.

        Args:
            name: Check name
            check_func: Function that returns True if healthy
            required_for_readiness: If True, must pass for readiness
        """
        self._checks[name] = {
            "func": check_func,
            "required": required_for_readiness,
        }

    def run_checks(self) -> Dict[str, HealthCheckResult]:
        """Run all registered checks."""
        results = {}

        for name, check in self._checks.items():
            start = time.time()
            try:
                is_healthy = check["func"]()
                latency_ms = (time.time() - start) * 1000

                if is_healthy:
                    result = HealthCheckResult(
                        name=name,
                        status=HealthStatus.HEALTHY,
                        message="OK",
                        latency_ms=latency_ms,
                    )
                else:
                    result = HealthCheckResult(
                        name=name,
                        status=HealthStatus.UNHEALTHY,
                        message="Check failed",
                        latency_ms=latency_ms,
                    )
            except Exception as e:
                latency_ms = (time.time() - start) * 1000
                result = HealthCheckResult(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check error: {str(e)}",
                    latency_ms=latency_ms,
                )

            results[name] = result
            self._last_results[name] = result

        return results

    def is_ready(self) -> bool:
        """Check if system is ready (all required checks pass)."""
        results = self.run_checks()

        for name, check in self._checks.items():
            if check["required"]:
                result = results[name]
                if result.status != HealthStatus.HEALTHY:
                    return False

        return True

    def is_alive(self) -> bool:
        """Check if system is alive (at least one check passes)."""
        results = self.run_checks()
        return any(r.status == HealthStatus.HEALTHY for r in results.values())

    def get_status(self) -> Dict:
        """Get detailed health status."""
        results = self.run_checks()

        all_healthy = all(r.status == HealthStatus.HEALTHY for r in results.values())
        any_unhealthy = any(
            r.status == HealthStatus.UNHEALTHY for r in results.values()
        )

        if all_healthy:
            overall = HealthStatus.HEALTHY
        elif any_unhealthy:
            overall = HealthStatus.UNHEALTHY
        else:
            overall = HealthStatus.DEGRADED

        return {
            "status": overall.value,
            "checks": {name: result.to_dict() for name, result in results.items()},
            "timestamp": time.time(),
        }


# Common health check functions


def check_api_connectivity(api_client) -> bool:
    """Check API connectivity."""
    try:
        # Try to fetch balance or ticker
        api_client.get_balance()
        return True
    except Exception:
        return False


def check_model_loaded(model_path: str) -> bool:
    """Check if model is loaded."""
    from pathlib import Path

    return Path(model_path).exists()


def check_data_freshness(
    last_update_time: float, max_age_seconds: float = 60.0
) -> bool:
    """Check if data is fresh.

    A feed whose clock runs ahead of ours -- or a replay emitting future
    timestamps -- yields a negative age, which would pass the raw
    ``< max_age_seconds`` comparison and certify a "fresh" feed that is not.
    The elapsed window must be non-negative *and* bounded.
    """
    elapsed = time.time() - last_update_time
    return 0.0 <= elapsed < max_age_seconds


def check_circuit_breaker(breaker) -> bool:
    """Check if circuit breaker is not tripped."""
    return not breaker.is_tripped()


def check_memory_usage(max_mb: float = 2000.0) -> bool:
    """Check memory usage is within bounds."""
    try:
        from trading_gym.utils.memory import get_memory_usage_mb

        usage = get_memory_usage_mb()
        # The probe signals "unmeasurable" by value, not by exception: it
        # returns -1.0 when psutil is unavailable. `usage > 0 else True` lets
        # that sentinel pass the readiness gate, defeating the fail-closed
        # handler below. Treat any non-positive reading as unhealthy.
        return 0.0 < usage < max_mb
    except Exception:
        # Fail closed: a probe we cannot read must not certify the system
        # ready.  Returning True here would let an unmeasurable host
        # pass a readiness gate.
        return False
