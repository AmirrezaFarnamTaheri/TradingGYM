from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from trading_gym.monitoring import alerting
from trading_gym.monitoring.alerting import (
    Alert,
    AlertChannel,
    AlertLevel,
    RateLimiter,
    TelegramConfig,
    WebhookChannel,
    WebhookConfig,
)


class RecordingChannel(AlertChannel):
    def _send_impl(self, alert: Alert) -> None:
        return None


def test_alert_payload_redacts_metadata_and_carries_stable_event_id() -> None:
    alert = Alert(
        title="Risk",
        message="Limit reached",
        level=AlertLevel.ERROR,
        metadata={"api_key": "secret", "nested": {"password": "hidden", "value": 7}},
    )
    payload = alert.to_dict()
    assert payload["metadata"] == {
        "api_key": "***",
        "nested": {"password": "***", "value": 7},
    }
    assert payload["event_id"] == alert.event_id
    assert len(alert.event_id) == 32


def test_channel_config_representations_hide_credentials() -> None:
    telegram = TelegramConfig(enabled=True, bot_token="telegram-secret", chat_id="chat-secret")
    webhook = WebhookConfig(
        enabled=True,
        url="https://example.test/hook",
        headers={"Authorization": "Bearer webhook-secret"},
    )
    assert "telegram-secret" not in repr(telegram)
    assert "chat-secret" not in repr(telegram)
    assert "webhook-secret" not in repr(webhook)
    assert "example.test" not in repr(webhook)


def test_rate_limiter_is_thread_safe_and_parameter_bounded() -> None:
    limiter = RateLimiter(max_alerts=7, window_seconds=60)
    with ThreadPoolExecutor(max_workers=16) as executor:
        allowed = list(executor.map(lambda _: limiter.should_allow(), range(100)))
    assert sum(allowed) == 7
    assert limiter.get_suppressed_count() == 93
    with pytest.raises(ValueError):
        RateLimiter(max_alerts=0)
    with pytest.raises(ValueError):
        RateLimiter(window_seconds=0)


def test_critical_alerts_remain_bounded() -> None:
    channel = RecordingChannel(alerting.AlertChannelConfig(enabled=True))
    results = [
        channel.send(Alert("Risk", "Limit", AlertLevel.CRITICAL))
        for _ in range(35)
    ]
    assert sum(results) == 30
    assert results[-1] is False


def test_webhook_metadata_is_redacted_and_event_id_is_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = MagicMock(return_value=MagicMock(status_code=204))
    monkeypatch.setattr(alerting, "_request_http", request)
    monkeypatch.setattr(
        alerting,
        "validate_base_url",
        lambda *args, **kwargs: "https://example.test/hook",
    )
    channel = WebhookChannel(WebhookConfig(enabled=True, url="https://example.test/hook"))
    alert = Alert(
        "Risk",
        "Limit",
        AlertLevel.ERROR,
        metadata={"token": "must-not-leak"},
    )
    assert channel.send(alert) is True
    kwargs = request.call_args.kwargs
    assert kwargs["headers"]["X-Trading-Gym-Alert-ID"] == alert.event_id
    assert b"must-not-leak" not in kwargs["content"]
    assert b'"token":"***"' in kwargs["content"]


def test_generated_alert_id_header_cannot_be_overridden() -> None:
    with pytest.raises(ValueError, match="managed"):
        WebhookConfig(
            enabled=True,
            url="https://example.test/hook",
            headers={"X-Trading-Gym-Alert-ID": "attacker-controlled"},
        )
