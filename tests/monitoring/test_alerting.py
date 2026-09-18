"""
Tests for the alerting system.

Tests cover all alert channels (Telegram, Webhook, Desktop) and the alert manager functionality.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from trading_gym.monitoring.alerting import (
    Alert,
    AlertLevel,
    AlertManager,
    SendOutcome,
    TelegramChannel,
    TelegramConfig,
    WebhookChannel,
    WebhookConfig,
    DesktopChannel,
    DesktopConfig,
    RateLimiter,
    send_alert,
    get_alert_manager,
)


class TestAlertLevel:
    """Test AlertLevel enum."""

    def test_priority_ordering(self):
        """Test that priority levels are correctly ordered."""
        assert AlertLevel.DEBUG.priority < AlertLevel.INFO.priority
        assert AlertLevel.INFO.priority < AlertLevel.WARNING.priority
        assert AlertLevel.WARNING.priority < AlertLevel.ERROR.priority
        assert AlertLevel.ERROR.priority < AlertLevel.CRITICAL.priority

    def test_emoji_mapping(self):
        """Test that each level has an emoji."""
        for level in AlertLevel:
            assert level.emoji, f"{level} should have an emoji"

    def test_color_mapping(self):
        """Test that each level has a color code."""
        for level in AlertLevel:
            assert level.color.startswith("#"), f"{level} should have a hex color"
            assert len(level.color) == 7, f"{level} color should be 7 chars (#RRGGBB)"


class TestAlert:
    """Test Alert message structure."""

    def test_alert_creation(self):
        """Test creating an alert."""
        alert = Alert(
            title="Test Alert", message="This is a test", level=AlertLevel.WARNING
        )
        assert alert.title == "Test Alert"
        assert alert.message == "This is a test"
        assert alert.level == AlertLevel.WARNING
        assert alert.timestamp > 0
        assert alert.metadata == {}
        assert alert.source == "trading_gym"

    def test_alert_with_metadata(self):
        """Test alert with metadata."""
        metadata = {"key1": "value1", "key2": 123}
        alert = Alert(
            title="Test", message="Test", level=AlertLevel.INFO, metadata=metadata
        )
        assert alert.metadata == metadata

    def test_alert_format_short(self):
        """Test short format."""
        alert = Alert(
            title="Test Alert", message="Test message", level=AlertLevel.ERROR
        )
        formatted = alert.format_short()
        assert "Test Alert" in formatted
        assert alert.level.emoji in formatted
        assert "ERROR" in formatted.upper()

    def test_alert_format_long(self):
        """Test long format with metadata."""
        alert = Alert(
            title="Test",
            message="Message",
            level=AlertLevel.INFO,
            metadata={"foo": "bar"},
        )
        formatted = alert.format_long()
        assert "Test" in formatted
        assert "Message" in formatted
        assert "foo" in formatted
        assert "bar" in formatted

    def test_alert_to_dict(self):
        """Test conversion to dictionary."""
        alert = Alert(
            title="Test",
            message="Message",
            level=AlertLevel.WARNING,
            metadata={"key": "value"},
        )
        d = alert.to_dict()
        assert d["title"] == "Test"
        assert d["message"] == "Message"
        assert d["level"] == "warning"
        assert d["metadata"] == {"key": "value"}
        assert "timestamp" in d


class TestRateLimiter:
    """Test rate limiting functionality."""

    def test_allows_within_limit(self):
        """Test that alerts are allowed within the limit."""
        limiter = RateLimiter(max_alerts=5, window_seconds=60)

        # Should allow first 5
        for _ in range(5):
            assert limiter.should_allow() is True

        # 6th should be blocked
        assert limiter.should_allow() is False

    def test_suppressed_count(self):
        """Test tracking suppressed alerts."""
        limiter = RateLimiter(max_alerts=2, window_seconds=60)

        limiter.should_allow()  # 1st: allowed
        limiter.should_allow()  # 2nd: allowed
        limiter.should_allow()  # 3rd: blocked
        limiter.should_allow()  # 4th: blocked

        assert limiter.get_suppressed_count() == 2
        # After getting count, it should reset
        assert limiter.get_suppressed_count() == 0


class TestTelegramChannel:
    """Test Telegram bot channel."""

    def test_telegram_config(self):
        """Test Telegram configuration."""
        config = TelegramConfig(
            enabled=True, bot_token="123456:ABC-DEF", chat_id="123456789"
        )
        assert config.enabled is True
        assert config.bot_token
        assert config.chat_id

    @patch("trading_gym.monitoring.alerting._request_http")
    def test_telegram_send(self, mock_request_http):
        """Test sending Telegram alert."""
        config = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="123")
        channel = TelegramChannel(config)

        alert = Alert(title="Test", message="Test message", level=AlertLevel.ERROR)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request_http.return_value = mock_response

        result = channel.send(alert)

        assert result is True


class TestWebhookChannel:
    """Test generic webhook channel."""

    def test_webhook_config(self):
        """Test webhook configuration."""
        config = WebhookConfig(
            enabled=True,
            url="https://example.com/webhook",
            headers={"Authorization": "Bearer TOKEN"},
        )
        assert config.enabled is True
        assert config.url
        assert "Authorization" in config.headers

    @patch("trading_gym.monitoring.alerting.validate_base_url")
    @patch("trading_gym.monitoring.alerting._request_http")
    def test_webhook_send(self, mock_request_http, mock_validate_base_url):
        """Test sending webhook alert."""
        config = WebhookConfig(enabled=True, url="https://example.com/webhook")
        channel = WebhookChannel(config)

        alert = Alert(title="Test", message="Test message", level=AlertLevel.WARNING)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request_http.return_value = mock_response
        mock_validate_base_url.return_value = "https://example.com/webhook"

        result = channel.send(alert)

        assert result is True

    @patch("trading_gym.monitoring.alerting.validate_base_url")
    @patch("trading_gym.monitoring.alerting._request_http")
    def test_webhook_revalidates_destination_and_disables_redirect_semantics(
        self, mock_request_http, mock_validate_base_url
    ):
        mock_validate_base_url.return_value = "https://example.com/webhook"
        mock_response = MagicMock(status_code=204)
        mock_request_http.return_value = mock_response
        channel = WebhookChannel(
            WebhookConfig(enabled=True, url="https://example.com/webhook")
        )

        assert channel.send(Alert("Risk", "Limit reached", AlertLevel.CRITICAL)) is True
        mock_validate_base_url.assert_called_once_with(
            "https://example.com/webhook",
            allow_private=False,
            allow_loopback=False,
            resolve_dns=True,
        )
        assert mock_request_http.call_args.kwargs["timeout"] == 5

    def test_webhook_rejects_private_destinations_and_managed_headers(self):
        with pytest.raises(ValueError, match="managed"):
            WebhookConfig(
                enabled=True,
                url="https://example.com",
                headers={"Host": "internal"},
            )
        channel = WebhookChannel(
            WebhookConfig(enabled=True, url="http://127.0.0.1/hook")
        )
        assert channel.send(Alert("Risk", "Limit reached", AlertLevel.ERROR)) is False


class TestDesktopChannel:
    """Test desktop notification channel."""

    def test_desktop_config(self):
        """Test desktop configuration."""
        config = DesktopConfig(enabled=True, min_level=AlertLevel.ERROR)
        assert config.enabled is True
        assert config.min_level == AlertLevel.ERROR

    def test_failing_notifier_reports_failed_not_delivered(self):
        """A non-zero exit must surface as FAILED, not DELIVERED.

        ``check=False`` silently counted an undelivered CRITICAL alert as
        delivered, so an operator's triage signal was wrong.
        """
        import subprocess

        config = DesktopConfig(enabled=True, min_level=AlertLevel.CRITICAL)
        channel = DesktopChannel(config)
        alert = Alert(
            level=AlertLevel.CRITICAL, title="halt", message="drawdown", source="risk"
        )
        error = subprocess.CalledProcessError(3, "notify-send")
        with patch("platform.system", return_value="Linux"), patch(
            "subprocess.run", side_effect=error
        ):
            assert channel.send_with_outcome(alert) is SendOutcome.FAILED
        with patch("platform.system", return_value="Darwin"), patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "osascript")
        ):
            assert channel.send_with_outcome(alert) is SendOutcome.FAILED


class TestAlertManager:
    """Test AlertManager."""

    def test_manager_creation(self):
        """Test creating an alert manager."""
        manager = AlertManager()
        assert manager.channels == []
        assert manager.default_source == "trading_gym"

    def test_add_channel(self):
        """Test adding a channel."""
        manager = AlertManager()
        config = WebhookConfig(enabled=True, url="http://example.com")
        channel = WebhookChannel(config)

        manager.add_channel(channel)
        assert len(manager.channels) == 1

    @patch("trading_gym.monitoring.alerting.validate_base_url")
    @patch("trading_gym.monitoring.alerting._request_http")
    def test_send_alert(self, mock_request_http, mock_validate_base_url):
        """Test sending alert through manager."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request_http.return_value = mock_response
        mock_validate_base_url.return_value = "http://example.com"

        manager = AlertManager()
        config = WebhookConfig(enabled=True, url="http://example.com")
        manager.add_channel(WebhookChannel(config))

        success_count = manager.send_alert(
            title="Test", message="Test message", level=AlertLevel.WARNING
        )

        assert success_count == 1
        assert len(manager.alert_history) == 1

    def test_get_stats(self):
        """Test getting alert statistics."""
        manager = AlertManager()

        # Send some test alerts (no channels, so they'll just be logged)
        manager.send_alert("Test 1", "Message", AlertLevel.INFO)
        manager.send_alert("Test 2", "Message", AlertLevel.WARNING)
        manager.send_alert("Test 3", "Message", AlertLevel.ERROR)

        stats = manager.get_stats()
        assert stats["total"] == 3
        assert stats["total_info"] == 1
        assert stats["total_warning"] == 1
        assert stats["total_error"] == 1

    def test_get_recent_alerts(self):
        """Test getting recent alerts."""
        manager = AlertManager()

        for i in range(15):
            manager.send_alert(f"Alert {i}", "Message", AlertLevel.INFO)

        recent = manager.get_recent_alerts(count=5)
        assert len(recent) == 5
        assert recent[-1].title == "Alert 14"  # Most recent

    def test_from_env_empty(self):
        """Test creating manager from empty environment."""
        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager.from_env()
            # Should have no channels when nothing is configured
            assert manager.channels == []

    def test_from_env_with_telegram(self):
        """Test creating manager from environment with Telegram configured."""
        env = {
            "ALERT_TELEGRAM_ENABLED": "true",
            "ALERT_TELEGRAM_BOT_TOKEN": "123:ABC",
            "ALERT_TELEGRAM_CHAT_ID": "123456",
        }
        with patch.dict(os.environ, env, clear=True):
            manager = AlertManager.from_env()
            assert len(manager.channels) == 1
            assert isinstance(manager.channels[0], TelegramChannel)

    def test_from_env_with_webhook(self):
        """Test creating manager from environment with Webhook configured."""
        env = {
            "ALERT_WEBHOOK_ENABLED": "true",
            "ALERT_WEBHOOK_URL": "https://example.com/webhook",
        }
        with patch.dict(os.environ, env, clear=True):
            manager = AlertManager.from_env()
            assert len(manager.channels) == 1
            assert isinstance(manager.channels[0], WebhookChannel)

    def test_from_env_with_desktop(self):
        """Test creating manager from environment with Desktop configured."""
        env = {
            "ALERT_DESKTOP_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            manager = AlertManager.from_env()
            assert len(manager.channels) == 1
            assert isinstance(manager.channels[0], DesktopChannel)

    def test_min_level_filtering(self):
        """Test that alerts below min_level are filtered."""
        config = WebhookConfig(
            enabled=True,
            url="http://example.com",
            min_level=AlertLevel.ERROR,  # Only ERROR and CRITICAL
        )
        channel = WebhookChannel(config)

        # INFO alert should be filtered
        alert_info = Alert("Test", "Message", AlertLevel.INFO)
        assert channel.should_send(alert_info) is False

        # ERROR alert should pass
        alert_error = Alert("Test", "Message", AlertLevel.ERROR)
        assert channel.should_send(alert_error) is True

        # CRITICAL alert should pass
        alert_critical = Alert("Test", "Message", AlertLevel.CRITICAL)
        assert channel.should_send(alert_critical) is True


class TestGlobalAlertManager:
    """Test global alert manager functions."""

    def test_get_alert_manager(self):
        """Test getting global alert manager."""
        # Reset global state
        import trading_gym.monitoring.alerting as alerting_module

        alerting_module._global_manager = None

        manager1 = get_alert_manager()
        manager2 = get_alert_manager()

        # Should be the same instance
        assert manager1 is manager2

    def test_send_alert_global(self):
        """Test global send_alert function."""
        # Reset global state
        import trading_gym.monitoring.alerting as alerting_module

        alerting_module._global_manager = None

        # Should not raise even with no channels
        count = send_alert("Test", "Message", AlertLevel.INFO)
        assert count == 0  # No channels configured


class TestTriStateTelemetry:
    """Delivered / skipped / failed must be distinguishable, not collapsed."""

    def test_disabled_channel_is_skipped_not_failed(self):
        channel = TelegramChannel(TelegramConfig(enabled=False))
        alert = Alert("Test", "Message", AlertLevel.CRITICAL)

        assert channel.send_with_outcome(alert) is SendOutcome.SKIPPED
        assert channel.send(alert) is False

    def test_below_minimum_level_is_skipped_not_failed(self):
        channel = TelegramChannel(
            TelegramConfig(enabled=True, min_level=AlertLevel.ERROR)
        )
        alert = Alert("Test", "Message", AlertLevel.INFO)

        assert channel.send_with_outcome(alert) is SendOutcome.SKIPPED

    def test_rate_limited_alert_is_skipped_not_failed(self):
        """A suppressed alert is an intentional throttling decision, not a
        delivery failure -- reporting it as one would hide real errors."""
        channel = TelegramChannel(
            TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="123")
        )
        # Exhaust the ordinary (non-critical) quota.
        for _ in range(10):
            assert channel.rate_limiter.should_allow() is True

        alert = Alert("Flood", "Message", AlertLevel.ERROR)
        assert channel.send_with_outcome(alert) is SendOutcome.SKIPPED
        assert channel.send(alert) is False

    def test_delivery_error_is_reported_as_failed(self):
        channel = TelegramChannel(TelegramConfig(enabled=True))  # no token/chat
        alert = Alert("Test", "Message", AlertLevel.ERROR)

        assert channel.send_with_outcome(alert) is SendOutcome.FAILED
        assert channel.error_count == 1
        assert channel.send(alert) is False

    def test_manager_counts_skipped_separately_from_failed(self):
        """The manager must not bill an intentional suppression as a failure."""
        channel = TelegramChannel(
            TelegramConfig(enabled=True, min_level=AlertLevel.ERROR)
        )
        manager = AlertManager()
        manager.add_channel(channel)

        # INFO is below the channel's minimum level: skipped, not failed.
        manager.send_alert("Test", "Message", AlertLevel.INFO)
        stats = manager.get_stats()
        assert stats["TelegramChannel_skipped"] == 1
        assert "TelegramChannel_failed" not in stats
        assert "TelegramChannel_success" not in stats

    def test_manager_counts_failures_when_delivery_errors(self):
        channel = TelegramChannel(TelegramConfig(enabled=True))  # no token/chat
        manager = AlertManager()
        manager.add_channel(channel)

        manager.send_alert("Test", "Message", AlertLevel.ERROR)
        stats = manager.get_stats()
        assert stats["TelegramChannel_failed"] == 1
        assert "TelegramChannel_skipped" not in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
