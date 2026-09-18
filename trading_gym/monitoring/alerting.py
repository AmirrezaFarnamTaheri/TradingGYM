"""
Trading Gym Alerting System
============================

Lightweight alerting system that supports multiple notification channels:
- Telegram (bot)
- Generic webhooks
- Desktop notifications

Configuration via environment variables or config dict.
Includes rate limiting to prevent alert spam and supports alert priorities.

Example:
    >>> from trading_gym.monitoring.alerting import AlertManager, AlertLevel
    >>>
    >>> manager = AlertManager()
    >>> manager.send_alert(
    ...     title="Circuit Breaker Tripped",
    ...     message="Maximum loss threshold exceeded: 10.5%",
    ...     level=AlertLevel.CRITICAL,
    ...     metadata={"nav": 8950, "loss_pct": 10.5}
    ... )
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from uuid import uuid4
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from trading_gym.common.security import (
    bounded_json_dumps,
    redact_secrets,
    validate_base_url,
)

logger = logging.getLogger(__name__)

MAX_ALERT_TITLE_CHARS = 240
MAX_ALERT_MESSAGE_CHARS = 8_192
MAX_ALERT_PAYLOAD_BYTES = 256 * 1024
MAX_WEBHOOK_HEADERS = 32
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_FORBIDDEN_WEBHOOK_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "x-trading-gym-alert-id",
}


def _request_http(
    method: str,
    url: str,
    *,
    content: bytes,
    headers: Dict[str, str],
    timeout: float,
) -> httpx.Response:
    """Issue one bounded request without proxies or redirects."""

    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return client.request(method, url, content=content, headers=headers)


def _validated_headers(headers: Dict[str, str]) -> Dict[str, str]:
    if len(headers) > MAX_WEBHOOK_HEADERS:
        raise ValueError(f"Webhook headers exceed {MAX_WEBHOOK_HEADERS} entries")
    output: Dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name)
        value = str(raw_value)
        if not _HEADER_NAME.fullmatch(name):
            raise ValueError("Webhook header name is invalid")
        if name.lower() in _FORBIDDEN_WEBHOOK_HEADERS:
            raise ValueError(f"Webhook header is managed by the HTTP client: {name}")
        if any(char in value for char in "\r\n\x00") or len(value) > 4096:
            raise ValueError(f"Webhook header value is invalid: {name}")
        output[name] = value
    return output


class AlertLevel(str, Enum):
    """Alert severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def priority(self) -> int:
        """Get numeric priority (higher = more urgent)."""
        return {
            "debug": 0,
            "info": 1,
            "warning": 2,
            "error": 3,
            "critical": 4,
        }[self.value]

    @property
    def emoji(self) -> str:
        """Get emoji representation for the alert level."""
        return {
            "debug": "🔍",
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
        }[self.value]

    @property
    def color(self) -> str:
        """Get color code (hex)."""
        return {
            "debug": "#808080",  # Gray
            "info": "#0099ff",  # Blue
            "warning": "#ffaa00",  # Orange
            "error": "#ff0000",  # Red
            "critical": "#990000",  # Dark Red
        }[self.value]


@dataclass
class Alert:
    """Alert message structure."""

    title: str
    message: str
    level: AlertLevel
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "trading_gym"
    event_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        self.title = str(self.title).strip()
        self.message = str(self.message)
        self.source = str(self.source).strip()
        if not self.title or len(self.title) > MAX_ALERT_TITLE_CHARS:
            raise ValueError(
                f"Alert title must be 1-{MAX_ALERT_TITLE_CHARS} characters"
            )
        if len(self.message) > MAX_ALERT_MESSAGE_CHARS:
            raise ValueError(
                f"Alert message exceeds {MAX_ALERT_MESSAGE_CHARS} characters"
            )
        if not self.source or len(self.source) > 120:
            raise ValueError("Alert source must be 1-120 characters")
        self.event_id = str(self.event_id).strip().lower()
        if not re.fullmatch(r"[a-f0-9]{32}", self.event_id):
            raise ValueError(
                "Alert event_id must be 32 lowercase hexadecimal characters"
            )
        # Validate serialisability and bound metadata before it reaches any channel.
        bounded_json_dumps(self.metadata, max_bytes=MAX_ALERT_PAYLOAD_BYTES)

    def format_short(self) -> str:
        """Format alert as short string."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        return f"[{ts}] {self.level.emoji} {self.level.value.upper()}: {self.title}"

    def format_long(self) -> str:
        """Format alert with full details."""
        lines = [
            self.format_short(),
            "",
            self.message,
        ]
        if self.metadata:
            lines.append("")
            lines.append("Metadata:")
            for key, value in self.metadata.items():
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "message": self.message,
            "level": self.level.value,
            "timestamp": self.timestamp,
            "metadata": redact_secrets(self.metadata),
            "source": self.source,
            "event_id": self.event_id,
        }


@dataclass
class AlertChannelConfig:
    """Configuration for an alert channel."""

    enabled: bool = False
    min_level: AlertLevel = (
        AlertLevel.WARNING
    )  # Only send alerts at this level or above


@dataclass
class TelegramConfig(AlertChannelConfig):
    """Telegram bot configuration."""

    bot_token: str = field(default="", repr=False)
    chat_id: str = field(default="", repr=False)


@dataclass
class WebhookConfig(AlertChannelConfig):
    """Generic webhook configuration."""

    url: str = field(default="", repr=False)
    headers: Dict[str, str] = field(default_factory=dict, repr=False)
    method: str = "POST"
    allow_private_network: bool = False

    def __post_init__(self) -> None:
        self.method = self.method.upper().strip()
        if self.method not in {"POST", "PUT", "PATCH"}:
            raise ValueError("Webhook method must be POST, PUT, or PATCH")
        self.headers = _validated_headers(self.headers)


@dataclass
class DesktopConfig(AlertChannelConfig):
    """Desktop notification configuration."""

    min_level: AlertLevel = AlertLevel.ERROR  # Only show critical alerts by default


class RateLimiter:
    """Thread-safe monotonic rate limiter for alerts."""

    def __init__(self, max_alerts: int = 10, window_seconds: float = 60):
        if not isinstance(max_alerts, int) or not 1 <= max_alerts <= 10_000:
            raise ValueError("max_alerts must be an integer between 1 and 10000")
        if not 0.1 <= float(window_seconds) <= 86_400:
            raise ValueError("window_seconds must be between 0.1 and 86400")
        self.max_alerts = max_alerts
        self.window_seconds = float(window_seconds)
        self.alert_times: deque[float] = deque()
        self.suppressed_count = 0
        self._lock = threading.Lock()

    def should_allow(self) -> bool:
        """Check if an alert should be allowed through."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            while self.alert_times and self.alert_times[0] < cutoff:
                self.alert_times.popleft()
            if len(self.alert_times) >= self.max_alerts:
                self.suppressed_count += 1
                return False
            self.alert_times.append(now)
            return True

    def get_suppressed_count(self) -> int:
        """Get and reset the suppressed count atomically."""
        with self._lock:
            count = self.suppressed_count
            self.suppressed_count = 0
            return count


class SendOutcome(str, Enum):
    """Tri-state result of one delivery attempt.

    A ``SKIPPED`` alert was deliberately not sent -- the channel is disabled,
    the alert is below the channel's minimum level, or it was suppressed by the
    rate limiter.  That is an intentional suppression, not a delivery failure,
    and reporting it as one would drown genuine errors in noise.
    """

    DELIVERED = "delivered"
    SKIPPED = "skipped"
    FAILED = "failed"


class AlertChannel:
    """Base class for alert channels."""

    def __init__(self, config: AlertChannelConfig):
        """Initialize alert channel with configuration."""
        self.config = config
        self.rate_limiter = RateLimiter(max_alerts=10, window_seconds=60)
        # Critical alerts may bypass the ordinary quota, but never all bounds.
        self.critical_rate_limiter = RateLimiter(max_alerts=30, window_seconds=60)
        self.last_error_time: Optional[float] = None
        self.error_count = 0

    def should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent through this channel."""
        if not self.config.enabled:
            return False

        if alert.level.priority < self.config.min_level.priority:
            return False

        if alert.level == AlertLevel.CRITICAL:
            return self.critical_rate_limiter.should_allow()

        return self.rate_limiter.should_allow()

    def send_with_outcome(self, alert: Alert) -> SendOutcome:
        """
        Send alert through this channel, reporting a tri-state outcome.

        Returns:
            DELIVERED on success, SKIPPED when deliberately not sent (disabled,
            below the minimum level, or rate-limited), FAILED on error.
        """
        if not self.should_send(alert):
            return SendOutcome.SKIPPED

        try:
            self._send_impl(alert)
            self.error_count = 0
            logger.info(
                "Alert sent channel=%s level=%s source=%s event_id=%s",
                self.__class__.__name__,
                alert.level.value,
                alert.source,
                alert.event_id,
            )
            return SendOutcome.DELIVERED
        except Exception as exc:
            self.error_count += 1
            self.last_error_time = time.time()
            logger.error(
                "Alert send failed channel=%s error_count=%d error_type=%s",
                self.__class__.__name__,
                self.error_count,
                type(exc).__name__,
            )
            return SendOutcome.FAILED

    def send(self, alert: Alert) -> bool:
        """
        Send alert through this channel.

        Returns:
            True if delivered, False if skipped or failed.
        """
        return self.send_with_outcome(alert) is SendOutcome.DELIVERED

    def _send_impl(self, alert: Alert) -> None:
        """Implement in subclass."""
        pass  # Intentional no-op for base class if not abstract


class TelegramChannel(AlertChannel):
    """Telegram bot alert channel."""

    def __init__(self, config: TelegramConfig):
        """Initialize Telegram channel with configuration."""
        super().__init__(config)
        self.config: TelegramConfig = config

    def _send_impl(self, alert: Alert) -> None:
        """Send Telegram alert with safe Markdown escaping."""
        if not self.config.bot_token or not self.config.chat_id:
            raise ValueError("Telegram bot token or chat ID not configured")

        def escape_markdown(text: str) -> str:
            """Escape special Markdown characters for Telegram."""
            if not text:
                return ""
            # Escape Markdown special characters to prevent formatting injection
            replacements = {
                "_": r"\_",
                "*": r"\*",
                "[": r"\[",
                "]": r"\]",
                "(": r"\(",
                ")": r"\)",
                "~": r"\~",
                "`": r"\`",
                ">": r"\>",
                "#": r"\#",
                "+": r"\+",
                "-": r"\-",
                "=": r"\=",
                "|": r"\|",
                "{": r"\{",
                "}": r"\}",
                ".": r"\.",
                "!": r"\!",
            }
            return "".join(replacements.get(ch, ch) for ch in str(text))

        # Escape all user-provided content to prevent formatting injection
        title = escape_markdown(f"{alert.level.emoji} {alert.title}")
        level_str = escape_markdown(alert.level.value.upper())
        time_str = escape_markdown(
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert.timestamp))
        )
        message_str = escape_markdown(alert.message)

        # Build message with escaped content
        text = (
            f"*{title}*\n\n*Level:* {level_str}\n*Time:* {time_str}\n\n{message_str}\n"
        )

        safe_metadata = redact_secrets(alert.metadata)
        if safe_metadata:
            text += "\n*Metadata:*\n"
            for k, v in safe_metadata.items():
                # Escape both key and value to prevent injection
                text += f"• {escape_markdown(str(k))}: {escape_markdown(str(v))}\n"

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        data = urlencode(
            {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }
        ).encode("utf-8")

        try:
            response = _request_http(
                "POST",
                url,
                content=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Telegram API returned status {response.status_code}"
                )
        except Exception as e:
            # The Telegram token is embedded in the URL, so never log exception text.
            logger.error("Failed to send Telegram alert: %s", type(e).__name__)
            raise


class WebhookChannel(AlertChannel):
    """Generic webhook alert channel."""

    def __init__(self, config: WebhookConfig):
        """Initialize webhook channel with configuration."""
        super().__init__(config)
        self.config: WebhookConfig = config

    def _send_impl(self, alert: Alert) -> None:
        """Send webhook alert with retries and strict timeouts."""
        if not self.config.url:
            raise ValueError("Webhook URL not configured")

        # Resolve immediately before sending. Redirects and proxy environment
        # variables are disabled below, narrowing SSRF and credential-forwarding risk.
        destination = validate_base_url(
            self.config.url,
            allow_private=self.config.allow_private_network,
            allow_loopback=self.config.allow_private_network,
            resolve_dns=True,
        )
        data = bounded_json_dumps(
            alert.to_dict(), max_bytes=MAX_ALERT_PAYLOAD_BYTES
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Trading-Gym-Alert-ID": alert.event_id,
            **_validated_headers(self.config.headers),
        }

        # Retry with exponential backoff to handle transient failures
        attempts = 3
        timeout_sec = 5
        last_exc: Optional[Exception] = None

        for i in range(attempts):
            try:
                response = _request_http(
                    self.config.method,
                    destination,
                    content=data,
                    headers=headers,
                    timeout=timeout_sec,
                )
                if response.status_code not in (200, 201, 202, 204):
                    raise RuntimeError(
                        f"Webhook returned status {response.status_code}"
                    )
                return  # Success
            except Exception as e:
                last_exc = e
                if i < attempts - 1:  # Don't sleep on last attempt
                    # Backoff: 0.5s, 1s, 2s
                    backoff_time = 0.5 * (2**i)
                    logger.debug(
                        "Webhook attempt %d failed (%s), retrying in %.1fs",
                        i + 1,
                        type(e).__name__,
                        backoff_time,
                    )
                    time.sleep(backoff_time)

        # Surface the last exception after exhausting retries
        logger.error(
            "Webhook failed after %d attempts (%s)",
            attempts,
            type(last_exc).__name__ if last_exc else "unknown",
        )
        raise last_exc if last_exc else RuntimeError("Webhook send failed")


class DesktopChannel(AlertChannel):
    """Desktop notification alert channel."""

    def __init__(self, config: DesktopConfig):
        """Initialize desktop notification channel with configuration."""
        super().__init__(config)
        self.config: DesktopConfig = config

    def _send_impl(self, alert: Alert) -> None:
        """Send desktop notification."""
        try:
            # Try plyer first (cross-platform)
            from plyer import notification

            notification.notify(
                title=f"{alert.level.emoji} {alert.title}",
                message=alert.message[:256],  # Limit message length
                app_name="Trading Gym",
                timeout=10,
            )
        except ImportError:
            # Fallback to platform-specific methods
            self._send_platform_specific(alert)

    def _send_platform_specific(self, alert: Alert) -> None:
        """Send platform-specific notification."""
        import platform

        system = platform.system()

        if system == "Windows":
            self._send_windows(alert)
        elif system == "Darwin":  # macOS
            self._send_macos(alert)
        elif system == "Linux":
            self._send_linux(alert)
        else:
            logger.warning("Desktop notifications not supported on %s", system)

    def _send_windows(self, alert: Alert) -> None:
        """Send Windows toast notification."""
        try:
            from win10toast import ToastNotifier

            toaster = ToastNotifier()
            toaster.show_toast(
                f"{alert.level.emoji} {alert.title}",
                alert.message[:256],
                duration=10,
                threaded=True,
            )
        except ImportError:
            logger.warning("win10toast not installed, skipping desktop notification")

    def _send_macos(self, alert: Alert) -> None:
        """Send macOS notification with proper escaping."""
        import subprocess

        def escape_applescript(s: str) -> str:
            """Escape strings for AppleScript to prevent command injection."""
            # Replace backslashes first, then quotes, normalize newlines
            s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            return s

        title = f"{alert.level.emoji} {alert.title}"
        safe_title = escape_applescript(title)
        safe_message = escape_applescript(alert.message[:256])

        script = f'display notification "{safe_message}" with title "{safe_title}"'
        # check=True: a failing notifier must surface as FAILED, not DELIVERED.
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    def _send_linux(self, alert: Alert) -> None:
        """Send Linux notification via notify-send with safe arguments."""
        import subprocess

        def sanitize_notify_arg(s: str) -> str:
            """Sanitize arguments for notify-send to prevent option injection."""
            # Collapse newlines, trim, and prevent leading dash
            s = (s or "")[:256].replace("\n", " ").strip()
            # Prevent leading dash being parsed as an option
            if s.startswith("-"):
                s = " " + s
            return s

        title = f"{alert.level.emoji} {alert.title}"
        safe_title = sanitize_notify_arg(title)
        safe_message = sanitize_notify_arg(alert.message)

        # Use -- separator for defense-in-depth against option injection.
        # check=True so a failing notifier surfaces as FAILED to the caller:
        # check=False silently counted an undelivered CRITICAL as DELIVERED.
        subprocess.run(
            ["notify-send", "--", safe_title, safe_message],
            check=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )


class AlertManager:
    """
    Main alert manager that coordinates all alert channels.

    Example:
        >>> manager = AlertManager.from_env()
        >>> manager.send_alert(
        ...     title="Trading Error",
        ...     message="Failed to execute order",
        ...     level=AlertLevel.ERROR
        ... )
    """

    def __init__(
        self,
        channels: Optional[List[AlertChannel]] = None,
        default_source: str = "trading_gym",
    ):
        """
        Initialize alert manager.

        Args:
            channels: List of alert channels to use
            default_source: Default source identifier for alerts
        """
        self.channels = channels or []
        self.default_source = default_source
        self.alert_history: deque = deque(maxlen=1000)
        self.stats: Dict[str, int] = defaultdict(int)

    @classmethod
    def from_env(cls) -> AlertManager:
        """Create AlertManager from environment variables."""
        channels: List[AlertChannel] = []

        # Telegram
        if os.getenv("ALERT_TELEGRAM_ENABLED", "").lower() == "true":
            telegram_config = TelegramConfig(
                enabled=True,
                bot_token=os.getenv("ALERT_TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("ALERT_TELEGRAM_CHAT_ID", ""),
                min_level=AlertLevel(os.getenv("ALERT_TELEGRAM_MIN_LEVEL", "warning")),
            )
            channels.append(TelegramChannel(telegram_config))
            logger.info("Telegram alerting enabled")

        # Generic Webhook
        if os.getenv("ALERT_WEBHOOK_ENABLED", "").lower() == "true":
            headers_str = os.getenv("ALERT_WEBHOOK_HEADERS", "")
            headers = {}
            if headers_str:
                try:
                    headers = json.loads(headers_str)
                except json.JSONDecodeError:
                    logger.warning(
                        "Invalid ALERT_WEBHOOK_HEADERS JSON, using empty headers"
                    )

            webhook_config = WebhookConfig(
                enabled=True,
                url=os.getenv("ALERT_WEBHOOK_URL", ""),
                headers=headers,
                method=os.getenv("ALERT_WEBHOOK_METHOD", "POST"),
                min_level=AlertLevel(os.getenv("ALERT_WEBHOOK_MIN_LEVEL", "warning")),
            )
            channels.append(WebhookChannel(webhook_config))
            logger.info("Webhook alerting enabled")

        # Desktop
        if os.getenv("ALERT_DESKTOP_ENABLED", "").lower() == "true":
            desktop_config = DesktopConfig(
                enabled=True,
                min_level=AlertLevel(os.getenv("ALERT_DESKTOP_MIN_LEVEL", "error")),
            )
            channels.append(DesktopChannel(desktop_config))
            logger.info("Desktop alerting enabled")

        return cls(channels=channels)

    def add_channel(self, channel: AlertChannel) -> None:
        """Add an alert channel."""
        self.channels.append(channel)

    def send_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        metadata: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> int:
        """
        Send an alert through all configured channels.

        Args:
            title: Alert title
            message: Alert message
            level: Alert severity level
            metadata: Additional metadata dict
            source: Source identifier

        Returns:
            Number of channels that successfully sent the alert
        """
        alert = Alert(
            title=title,
            message=message,
            level=level,
            metadata=metadata or {},
            source=source or self.default_source,
        )

        self.alert_history.append(alert)
        self.stats[f"total_{level.value}"] += 1
        self.stats["total"] += 1

        # Log to standard logger
        log_func = {
            AlertLevel.DEBUG: logger.debug,
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.ERROR: logger.error,
            AlertLevel.CRITICAL: logger.critical,
        }[level]
        log_func(alert.format_short())

        # Send through channels.  A skipped channel (disabled, below the minimum
        # level, or rate-limited) is deliberately not a failure: reporting it as
        # one would hide real delivery errors behind intentional suppressions.
        success_count = 0
        for channel in self.channels:
            name = channel.__class__.__name__
            outcome = channel.send_with_outcome(alert)
            if outcome is SendOutcome.DELIVERED:
                success_count += 1
                self.stats[f"{name}_success"] += 1
            elif outcome is SendOutcome.SKIPPED:
                self.stats[f"{name}_skipped"] += 1
                # A suppressed CRITICAL is operationally invisible otherwise:
                # the alert is neither delivered nor failed, so nothing emits
                # and the only trace is a counter nobody watches.  Log it.
                if level is AlertLevel.CRITICAL:
                    logger.warning(
                        "Critical alert suppressed by channel %s: %s",
                        name,
                        alert.format_short(),
                    )
            else:
                self.stats[f"{name}_failed"] += 1

        return success_count

    def get_stats(self) -> Dict[str, int]:
        """Get alerting statistics."""
        return dict(self.stats)

    def get_recent_alerts(self, count: int = 10) -> List[Alert]:
        """Get recent alerts."""
        return list(self.alert_history)[-count:]


# Global alert manager instance (lazy-initialized)
_global_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get the global alert manager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = AlertManager.from_env()
    return _global_manager


def send_alert(
    title: str, message: str, level: AlertLevel = AlertLevel.INFO, **kwargs
) -> int:
    """Convenience function to send alert via global manager."""
    return get_alert_manager().send_alert(title, message, level, **kwargs)
