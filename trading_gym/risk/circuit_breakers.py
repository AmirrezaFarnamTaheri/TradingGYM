"""
Risk Circuit Breakers
======================

Implements hard stops and circuit breakers to protect against:
- Excessive losses
- Loss velocity (rapid drawdowns)
- Slippage breaches
- API error bursts
- Margin ratio violations
- Trading stalls (no fills)

When tripped, circuit breakers:
- Flatten all positions
- Cancel open orders
- Disable new trades
- Send alerts
- Persist state to disk
- Optionally auto-reset after cooldown
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from trading_gym.common.config import BreakerConfig
from trading_gym.common.persistence import atomic_write_json, read_json

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    TRIPPED = "tripped"  # Breaker triggered
    COOLDOWN = "cooldown"  # Cooling down before reset
    DISABLED = "disabled"  # Manually disabled


@dataclass
class BreakerRule:
    """Defines a circuit breaker rule."""

    name: str
    check_func: Callable[[Dict[str, Any]], bool]
    description: str
    enabled: bool = True
    priority: int = 0  # Higher priority trips first


# Context keys each default rule consumes.  A rule listed here can be enabled
# and still never fire if the execution layer supplies none of its keys; see
# ``CircuitBreaker.get_status()["unwired_rules"]``.  Rules absent from this map
# read only state the breaker itself accumulates (nav history, margin ratios)
# and are always wired once any check has run.
_RULE_CONTEXT_KEYS: Dict[str, tuple[str, ...]] = {
    "single_trade_loss": ("trade_loss_pct",),
    "slippage_breach": ("slippage_breach",),
    "api_error_burst": ("api_error",),
    "rejection_rate": ("order_attempt", "order_rejected"),
    "stall": ("fill",),
}


@dataclass
class BreakerMetrics:
    """Metrics tracked by circuit breakers."""

    # Loss tracking
    nav_history: deque = field(default_factory=lambda: deque(maxlen=1000))
    loss_events: deque = field(default_factory=lambda: deque(maxlen=100))

    # Trade tracking
    trade_losses: deque = field(default_factory=lambda: deque(maxlen=100))
    slippage_breaches: deque = field(default_factory=lambda: deque(maxlen=100))

    # API tracking
    api_errors: deque = field(default_factory=lambda: deque(maxlen=100))
    order_rejections: deque = field(default_factory=lambda: deque(maxlen=100))
    order_attempts: deque = field(default_factory=lambda: deque(maxlen=100))

    # Fill tracking
    last_fill_time: Optional[float] = None

    # Margin tracking
    margin_ratios: deque = field(default_factory=lambda: deque(maxlen=100))


class CircuitBreaker:
    """
    Circuit breaker system for risk management.

    Example:
        >>> config = BreakerConfig(max_loss_pct=10.0)
        >>> breaker = CircuitBreaker(config)
        >>> breaker.register_rule("max_loss", lambda m: m["loss_pct"] > 10.0)
        >>>
        >>> # On each trade
        >>> metrics = {"nav": 9000, "initial_nav": 10000}
        >>> if breaker.check(metrics):
        ...     print("Circuit breaker tripped!")
        ...     breaker.handle_trip()
    """

    def __init__(self, config: BreakerConfig):
        self.config = config
        self.state = BreakerState.CLOSED
        self.metrics = BreakerMetrics()
        self.rules: List[BreakerRule] = []
        self.trip_time: Optional[float] = None
        self.trip_reason: Optional[str] = None
        self.initial_nav: Optional[float] = None
        self.persistence_degraded = False
        self.alert_delivery_degraded = False

        # Register default rules
        self._register_default_rules()

        # Load persisted state if available
        if config.state_file and config.state_file.exists():
            self._load_state()

        logger.info("Initialized CircuitBreaker with %d rules", len(self.rules))

    def register_rule(
        self,
        name: str,
        check_func: Callable[[Dict[str, Any]], bool],
        description: str = "",
        enabled: bool = True,
        priority: int = 0,
    ) -> None:
        """Register a bounded, uniquely named circuit-breaker rule."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", name):
            raise ValueError("rule name must be a safe identifier")
        if not callable(check_func):
            raise TypeError("check_func must be callable")
        if any(existing.name == name for existing in self.rules):
            raise ValueError(f"circuit-breaker rule already exists: {name}")
        if len(self.rules) >= 256:
            raise ValueError("circuit-breaker rule limit exceeded")
        description = str(description or name).strip()
        if not description or len(description) > 500:
            raise ValueError("rule description must be 1-500 characters")
        if not isinstance(priority, int) or not -10_000 <= priority <= 10_000:
            raise ValueError("rule priority must be an integer between -10000 and 10000")
        rule = BreakerRule(
            name=name,
            check_func=check_func,
            description=description,
            enabled=enabled,
            priority=priority,
        )
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)
        logger.info("Registered rule: %s (priority=%d)", name, priority)

    def check(
        self, context: Dict[str, Any], current_time: Optional[float] = None
    ) -> bool:
        """
        Check all rules and trip if any are violated.

        Args:
            context: Dictionary containing current metrics and state
            current_time: Optional simulation time. Defaults to wall clock time.

        Returns:
            True if breaker tripped, False otherwise
        """
        if not isinstance(context, dict):
            raise TypeError("circuit-breaker context must be a dictionary")
        now = float(current_time if current_time is not None else time.time())
        if not math.isfinite(now):
            self._trip("invalid_context", "Current time is not finite", current_time=time.time())
            return True

        if self.state == BreakerState.DISABLED:
            return False

        if self.state == BreakerState.TRIPPED:
            # Check if cooldown period has elapsed
            if self.config.auto_reset and self.trip_time:
                elapsed = now - self.trip_time
                if elapsed > self.config.cooldown_minutes * 60:
                    self._reset(current_time=now)
                    # If reset, verify if we should continue to check rules or return False.
                    # Falling through allows immediate re-trip check or returns False if safe.
                else:
                    return True
            else:
                return True

        # Malformed or non-finite safety metrics must never become permission to
        # trade. Validate before mutating rolling history and fail closed.
        try:
            self._validate_context(context)
            self._update_metrics(context, current_time=now)
        except Exception as exc:
            self._trip(
                "invalid_context",
                f"Safety metric validation failed ({type(exc).__name__})",
                current_time=now,
            )
            return True

        # Add current_time to context for rules to use
        context_with_time = {**context, "current_time": now}

        # Check all rules
        for rule in self.rules:
            if not rule.enabled:
                continue

            try:
                if rule.check_func(context_with_time):
                    self._trip(rule.name, rule.description, current_time=now)
                    return True
            except Exception as exc:
                logger.critical(
                    "Circuit-breaker rule evaluation failed rule=%s error_type=%s",
                    rule.name,
                    type(exc).__name__,
                )
                self._trip(
                    f"rule_error.{rule.name}",
                    f"Rule evaluation failed ({type(exc).__name__})",
                    current_time=now,
                )
                return True

        return False

    def handle_trip(self) -> None:
        """Execute actions when circuit breaker trips."""
        if self.state != BreakerState.TRIPPED:
            return

        logger.critical("Circuit breaker TRIPPED: %s", self.trip_reason)

        # Alerting
        self._send_alert()

        if self.config.state_file:
            self._save_state()

    def reset(self, manual: bool = True) -> None:
        """
        Reset circuit breaker to normal operation.

        Args:
            manual: If True, requires manual intervention (not auto-reset)
        """
        if manual or self.config.auto_reset:
            self._reset()
        else:
            logger.warning("Auto-reset disabled, manual reset required")

    def disable(self) -> None:
        """Disable circuit breaker (use with caution)."""
        previous = self.state
        self.state = BreakerState.DISABLED
        if self.config.state_file and not self._save_state():
            self.state = previous
            raise RuntimeError("Circuit breaker could not persist disabled state")
        logger.warning("Circuit breaker DISABLED")

    def enable(self) -> None:
        """Enable circuit breaker."""
        if self.state == BreakerState.DISABLED:
            self.state = BreakerState.CLOSED
            if self.config.state_file and not self._save_state():
                self.state = BreakerState.DISABLED
                raise RuntimeError("Circuit breaker could not persist enabled state")
            logger.info("Circuit breaker ENABLED")

    def is_tripped(self) -> bool:
        """Check if breaker is currently tripped."""
        return self.state == BreakerState.TRIPPED

    def get_status(self) -> Dict[str, Any]:
        """Get current breaker status.

        ``enabled_rules`` names rules that are switched on.  A rule can be
        enabled yet never fire because the execution layer supplies none of
        the context keys it reads — the protection surface is then narrower
        than the config and this status imply.  Those rules are reported
        separately as ``unwired_rules`` so an operator reading this status
        cannot mistake a configured knob (notably ``max_no_fill_minutes``)
        for a protection that is actually in force.
        """
        return {
            "state": self.state.value,
            "trip_time": self.trip_time,
            "trip_reason": self.trip_reason,
            "persistence_degraded": self.persistence_degraded,
            "alert_delivery_degraded": self.alert_delivery_degraded,
            "enabled_rules": [r.name for r in self.rules if r.enabled],
            "unwired_rules": self._unwired_rule_names(),
            "metrics": self._get_metrics_summary(),
        }

    def _unwired_rule_names(self) -> List[str]:
        """Enabled rules whose context keys have never been supplied.

        A rule counts as wired once its key has been observed at least once,
        so a live deployment that does feed these keys reports them as wired
        and only a genuinely non-producing path is flagged.
        """
        observed = {
            "trade_loss_pct": bool(self.metrics.trade_losses),
            "slippage_breach": bool(self.metrics.slippage_breaches),
            "api_error": bool(self.metrics.api_errors),
            "order_attempt": bool(self.metrics.order_attempts),
            "order_rejected": bool(self.metrics.order_rejections),
            "fill": self.metrics.last_fill_time is not None,
        }
        unwired = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            keys = _RULE_CONTEXT_KEYS.get(rule.name)
            if keys and not all(observed.get(key) for key in keys):
                unwired.append(rule.name)
        return unwired

    # ------------------------------------------------------------------ #
    # Internal methods
    # ------------------------------------------------------------------ #

    def _register_default_rules(self) -> None:
        """Register default circuit breaker rules."""

        # Max loss rule
        def check_max_loss(ctx: Dict[str, Any]) -> bool:
            nav = float(ctx.get("nav", 0.0))
            initial = ctx.get("initial_nav")

            if initial is None:
                if self.initial_nav is not None:
                    initial = self.initial_nav
                else:
                    initial = nav

            # Ensure we only set initial_nav once
            if self.initial_nav is None:
                self.initial_nav = float(initial)

            initial = float(initial)

            if initial <= 0:
                return False

            loss_pct = ((initial - nav) / initial) * 100
            # logger.debug(f"MaxLoss Check: Nav={nav}, Initial={initial}, Loss={loss_pct}%")
            return loss_pct > self.config.max_loss_pct

        self.register_rule(
            name="max_loss",
            check_func=check_max_loss,
            description=f"Loss exceeds {self.config.max_loss_pct}%",
            priority=10,
        )

        # Loss velocity rule
        def check_loss_velocity(ctx: Dict[str, Any]) -> bool:
            if len(self.metrics.nav_history) < 2:
                return False

            now = ctx.get("current_time", time.time())
            window = self.config.max_loss_window_minutes * 60
            recent = [
                (t, nav) for t, nav in self.metrics.nav_history if now - t < window
            ]

            if len(recent) < 2:
                return False

            _, nav_start = recent[0]
            _, nav_end = recent[-1]

            nav_start = float(nav_start)
            nav_end = float(nav_end)

            time_hours = (recent[-1][0] - recent[0][0]) / 3600

            if nav_start <= 0 or time_hours <= 0:
                return False

            loss_pct_per_hour = ((nav_start - nav_end) / nav_start) * 100 / time_hours
            return loss_pct_per_hour > self.config.max_loss_velocity_pct_per_hour

        self.register_rule(
            name="loss_velocity",
            check_func=check_loss_velocity,
            description=f"Loss velocity exceeds {self.config.max_loss_velocity_pct_per_hour}%/hour",
            priority=9,
        )

        # Single trade loss rule
        def check_single_trade_loss(ctx: Dict[str, Any]) -> bool:
            trade_loss_pct = ctx.get("trade_loss_pct", 0.0)
            return abs(trade_loss_pct) > self.config.max_single_trade_loss_pct

        self.register_rule(
            name="single_trade_loss",
            check_func=check_single_trade_loss,
            description=f"Single trade loss exceeds {self.config.max_single_trade_loss_pct}%",
            priority=8,
        )

        # Slippage breach rule
        def check_slippage_breaches(ctx: Dict[str, Any]) -> bool:
            now = ctx.get("current_time", time.time())
            window = self.config.slippage_breach_window_minutes * 60
            recent = [t for t in self.metrics.slippage_breaches if now - t < window]
            return len(recent) > self.config.max_slippage_breach_count

        self.register_rule(
            name="slippage_breach",
            check_func=check_slippage_breaches,
            description=f"Slippage breaches exceed {self.config.max_slippage_breach_count}",
            priority=7,
        )

        # API error burst rule
        def check_api_errors(ctx: Dict[str, Any]) -> bool:
            now = ctx.get("current_time", time.time())
            window = self.config.api_error_window_minutes * 60
            recent = [t for t in self.metrics.api_errors if now - t < window]
            return len(recent) > self.config.max_api_error_count

        self.register_rule(
            name="api_error_burst",
            check_func=check_api_errors,
            description=f"API errors exceed {self.config.max_api_error_count}",
            priority=6,
        )

        # Order rejection rate rule
        def check_rejection_rate(ctx: Dict[str, Any]) -> bool:
            if len(self.metrics.order_attempts) < 10:
                return False

            now = ctx.get("current_time", time.time())
            window = 300  # 5 minutes
            recent_attempts = [
                t for t in self.metrics.order_attempts if now - t < window
            ]
            recent_rejections = [
                t for t in self.metrics.order_rejections if now - t < window
            ]

            if len(recent_attempts) == 0:
                return False

            rejection_rate = (len(recent_rejections) / len(recent_attempts)) * 100
            return rejection_rate > self.config.max_rejection_rate_pct

        self.register_rule(
            name="rejection_rate",
            check_func=check_rejection_rate,
            description=f"Rejection rate exceeds {self.config.max_rejection_rate_pct}%",
            priority=5,
        )

        # Margin ratio floor rule
        def check_margin_floor(ctx: Dict[str, Any]) -> bool:
            if not self.config.margin_check_enabled:
                return False
            margin_ratio = ctx.get("margin_ratio", float("inf"))
            return margin_ratio < self.config.margin_ratio_floor

        self.register_rule(
            name="margin_floor",
            check_func=check_margin_floor,
            description=f"Margin ratio below {self.config.margin_ratio_floor}",
            enabled=self.config.margin_check_enabled,
            priority=10,  # High priority
        )

        # No fill stall rule
        def check_stall(ctx: Dict[str, Any]) -> bool:
            if not self.config.stall_check_enabled:
                return False
            if self.metrics.last_fill_time is None:
                return False

            now = ctx.get("current_time", time.time())
            time_since_fill = (now - self.metrics.last_fill_time) / 60
            return time_since_fill > self.config.max_no_fill_minutes

        self.register_rule(
            name="stall",
            check_func=check_stall,
            description=f"No fills for {self.config.max_no_fill_minutes} minutes",
            enabled=self.config.stall_check_enabled,
            priority=4,
        )

    @staticmethod
    def _validate_context(context: Dict[str, Any]) -> None:
        """Reject non-finite safety metrics before rule evaluation."""

        for key in ("nav", "initial_nav", "trade_loss_pct", "margin_ratio"):
            if key not in context:
                continue
            value = float(context[key])
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if "nav" in context and float(context["nav"]) < 0:
            raise ValueError("nav must not be negative")
        if "initial_nav" in context and float(context["initial_nav"]) <= 0:
            raise ValueError("initial_nav must be positive")
        if "margin_ratio" in context and float(context["margin_ratio"]) < 0:
            raise ValueError("margin_ratio must not be negative")

    def _update_metrics(self, context: Dict[str, Any], current_time: float) -> None:
        """Update tracked metrics from context."""
        now = current_time

        # Track NAV
        if "nav" in context:
            self.metrics.nav_history.append((now, float(context["nav"])))
            if self.initial_nav is None:
                self.initial_nav = float(context["nav"])

        # Track trade losses
        if "trade_loss_pct" in context:
            self.metrics.trade_losses.append((now, float(context["trade_loss_pct"])))

        # Track slippage breaches
        if context.get("slippage_breach"):
            self.metrics.slippage_breaches.append(now)

        # Track API errors
        if context.get("api_error"):
            self.metrics.api_errors.append(now)

        # Track order attempts and rejections
        if context.get("order_attempt"):
            self.metrics.order_attempts.append(now)
        if context.get("order_rejected"):
            self.metrics.order_rejections.append(now)

        # Track fills
        if context.get("fill"):
            self.metrics.last_fill_time = now

        # Track margin ratio
        if "margin_ratio" in context:
            self.metrics.margin_ratios.append((now, context["margin_ratio"]))

    def _trip(
        self, rule_name: str, reason: str, current_time: Optional[float] = None
    ) -> None:
        """Trip the circuit breaker."""
        self.state = BreakerState.TRIPPED
        self.trip_time = current_time if current_time is not None else time.time()
        self.trip_reason = f"{rule_name}: {reason}"

        logger.critical("CIRCUIT BREAKER TRIPPED: %s", self.trip_reason)

        self.handle_trip()

    def _reset(self, current_time: Optional[float] = None) -> None:
        """Reset circuit breaker to normal state.

        The rolling evidence that tripped a rule is cleared as well.  Without
        that the cooldown is pointless: ``check`` falls straight through to the
        rules again and the stale burst still sitting in the window re-trips on
        the very next sample, so a breaker with ``auto_reset`` configured can
        never actually recover.  Every window is repopulated from the live
        context on the next check, so clearing costs one sample of history
        rather than any protection.  ``initial_nav`` and ``last_fill_time`` are
        deliberately kept: an absolute drawdown and a liveness stall are states
        rather than bursts, and a cooldown must not forgive them.
        """
        logger.info("Resetting circuit breaker from %s", self.state)
        previous = (self.state, self.trip_time, self.trip_reason)
        self.state = BreakerState.CLOSED
        self.trip_time = None
        self.trip_reason = None

        if self.config.state_file and not self._save_state():
            self.state, self.trip_time, self.trip_reason = previous
            raise RuntimeError("Circuit breaker reset was not durably persisted")

        # Only clear once the reset is durable, so a failed save leaves the
        # tripped breaker with its evidence intact.
        for window in (
            self.metrics.nav_history,
            self.metrics.loss_events,
            self.metrics.trade_losses,
            self.metrics.slippage_breaches,
            self.metrics.api_errors,
            self.metrics.order_rejections,
            self.metrics.order_attempts,
            self.metrics.margin_ratios,
        ):
            window.clear()

    def _send_alert(self) -> None:
        """Send alert notification via configured alert channels."""
        trip_timestamp = self.trip_time or time.time()
        trip_time_str = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(trip_timestamp)
        )

        logger.critical(
            "ALERT: Circuit breaker tripped at %s - %s",
            trip_time_str,
            self.trip_reason,
        )

        # Send alert via integrated alerting system
        try:
            from trading_gym.monitoring.alerting import send_alert, AlertLevel

            # Prepare metadata
            metadata = {
                "trip_time": trip_time_str,
                "trip_reason": self.trip_reason or "Unknown",
                "breaker_state": self.state.value,
            }

            # Add NAV information if available
            if self.initial_nav is not None:
                metadata["initial_nav"] = self.initial_nav

            # Send critical alert
            delivered = send_alert(
                title="🚨 Circuit Breaker Tripped",
                message=(
                    f"Trading has been halted due to circuit breaker activation.\n\n"
                    f"Reason: {self.trip_reason}\n"
                    f"Time: {trip_time_str}\n\n"
                    f"Please investigate immediately and take appropriate action."
                ),
                level=AlertLevel.CRITICAL,
                metadata=metadata,
                source="circuit_breaker",
            )

            self.alert_delivery_degraded = not bool(delivered)
            if delivered:
                logger.info("Circuit breaker alert accepted event_state=delivered")
            else:
                logger.warning("Circuit breaker alert was not accepted by any channel")

        except ImportError:
            self.alert_delivery_degraded = True
            logger.warning(
                "Alerting system not available. Install trading_gym.monitoring.alerting "
                "to enable email, Slack, PagerDuty, and other alert channels."
            )
        except Exception as exc:
            self.alert_delivery_degraded = True
            logger.error("Circuit breaker alert failed error_type=%s", type(exc).__name__)

    def _save_state(self) -> bool:
        """Persist breaker state to disk atomically."""
        if not self.config.state_file:
            return True

        state = {
            "format": "trading-gym-circuit-breaker",
            "version": 1,
            "saved_at": time.time(),
            "state": self.state.value,
            "trip_time": self.trip_time,
            "trip_reason": self.trip_reason,
            "initial_nav": self.initial_nav,
        }

        try:
            atomic_write_json(self.config.state_file, state)
            self.persistence_degraded = False
            logger.info("Saved breaker state to %s", self.config.state_file)
            return True
        except Exception as exc:
            self.persistence_degraded = True
            logger.critical("Failed to save breaker state error_type=%s", type(exc).__name__)
            return False

    def _load_state(self) -> None:
        """Load persisted breaker state from disk."""
        if not self.config.state_file or not self.config.state_file.exists():
            return

        try:
            state = read_json(self.config.state_file, default={})
            if not isinstance(state, dict):
                raise ValueError("breaker state must be an object")
            # Legacy state files did not carry a format marker; they remain
            # readable during upgrade. Any declared format/version must match.
            if state.get("format") not in {None, "trading-gym-circuit-breaker"}:
                raise ValueError("unsupported breaker state format")
            if state.get("version") not in {None, 1}:
                raise ValueError("unsupported breaker state version")

            self.state = BreakerState(state.get("state", "closed"))
            self.trip_time = (
                float(state["trip_time"]) if state.get("trip_time") is not None else None
            )
            self.trip_reason = (
                str(state["trip_reason"])[:1000]
                if state.get("trip_reason") is not None
                else None
            )
            self.initial_nav = (
                float(state["initial_nav"])
                if state.get("initial_nav") is not None
                else None
            )
            self.persistence_degraded = False

            logger.info("Loaded breaker state state=%s", self.state.value)

            # If tripped and past cooldown, auto-reset
            if self.state == BreakerState.TRIPPED and self.config.auto_reset:
                if self.trip_time:
                    elapsed = time.time() - self.trip_time
                    if elapsed > self.config.cooldown_minutes * 60:
                        self._reset()

        except Exception as exc:
            # A corrupt or unreadable persisted safety state must never be
            # interpreted as permission to trade. Fail closed until a human
            # repairs the state and explicitly resets the breaker.
            self.state = BreakerState.TRIPPED
            self.trip_time = time.time()
            self.trip_reason = f"persisted_state_invalid:{type(exc).__name__}"
            self.persistence_degraded = True
            logger.critical(
                "Failed to load breaker state; breaker forced TRIPPED error_type=%s",
                type(exc).__name__,
            )

    def _last_observed_time(self) -> float:
        """The clock the metrics were stamped with.

        Falls back to wall clock when no context has been observed yet, so a
        breaker that never saw a ``check()`` still reports a sane summary.
        """
        candidates = [
            self.metrics.last_fill_time,
            self.metrics.api_errors[-1] if self.metrics.api_errors else None,
            self.metrics.slippage_breaches[-1] if self.metrics.slippage_breaches else None,
            self.metrics.order_attempts[-1] if self.metrics.order_attempts else None,
            self.metrics.order_rejections[-1] if self.metrics.order_rejections else None,
            self.metrics.trade_losses[-1][0] if self.metrics.trade_losses else None,
            self.metrics.nav_history[-1][0] if self.metrics.nav_history else None,
        ]
        observed = [c for c in candidates if c is not None]
        if observed:
            return float(max(observed))
        # No event has ever been recorded; the summary windows are empty
        # regardless of the clock, so wall clock is a harmless default.
        return time.time()

    def _get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of current metrics.

        Rolling windows are filtered against the same clock that stamped the
        events: ``_update_metrics`` records with the caller's ``current_time``
        (a replay/backtest passes simulation time), so filtering with
        ``time.time()`` reports zero recent events and a 29-million-minute
        stall right after two fills.
        """
        now = self._last_observed_time()

        return {
            "nav_samples": len(self.metrics.nav_history),
            "recent_loss_events": len(
                [t for t in self.metrics.loss_events if now - t < 3600]
            ),
            "recent_api_errors": len(
                [t for t in self.metrics.api_errors if now - t < 300]
            ),
            "recent_slippage_breaches": len(
                [t for t in self.metrics.slippage_breaches if now - t < 600]
            ),
            "time_since_last_fill": (
                (now - self.metrics.last_fill_time) / 60
                if self.metrics.last_fill_time
                else None
            ),
        }
