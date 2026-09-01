import logging
import os
import time
from trading_gym.common.logging import (
    configure_logging,
    configure_logging_from_env,
    log_execution_time,
    log_function_call,
    LogContext
)


def test_configure_logging():
    """Test that logging is set up correctly."""
    configure_logging(force=True)
    logger = logging.getLogger("trading_gym")
    assert logger.getEffectiveLevel() == logging.INFO
    assert len(logging.getLogger().handlers) > 0


def test_configure_logging_from_env():
    """Test that logging is set up correctly from the environment."""
    os.environ["TG_LOG_LEVEL"] = "DEBUG"
    configure_logging_from_env(force=True)
    logger = logging.getLogger("trading_gym")
    assert logger.getEffectiveLevel() == logging.DEBUG
    os.environ.pop("TG_LOG_LEVEL")


def test_resolve_level():
    """Test that the log level is resolved correctly."""
    from trading_gym.common.logging import _resolve_level

    assert _resolve_level("INFO") == logging.INFO
    assert _resolve_level("DEBUG") == logging.DEBUG
    assert _resolve_level(logging.WARNING) == logging.WARNING


def test_log_execution_time(caplog):
    """Test that the log_execution_time decorator works."""
    caplog.set_level(logging.DEBUG)

    @log_execution_time(level=logging.DEBUG)
    def slow_function():
        time.sleep(0.01)
        return "done"

    assert slow_function() == "done"
    assert "slow_function executed in" in caplog.text


def test_log_function_call(caplog):
    """Test that the log_function_call decorator works."""
    caplog.set_level(logging.DEBUG)

    @log_function_call(level=logging.DEBUG, log_args=True, log_result=True)
    def my_func(a, b):
        return a + b

    assert my_func(1, 2) == 3
    assert "Calling test_log_function_call.<locals>.my_func with args=(1, 2)" in caplog.text
    assert "my_func returned 3" in caplog.text


def test_log_context():
    """Test that LogContext manages context correctly."""
    with LogContext(request_id="123"):
        ctx = LogContext.get_context()
        assert ctx["request_id"] == "123"

        with LogContext(user_id="abc"):
            ctx = LogContext.get_context()
            assert ctx["request_id"] == "123"
            assert ctx["user_id"] == "abc"

        ctx = LogContext.get_context()
        assert ctx["request_id"] == "123"
        assert "user_id" not in ctx

    ctx = LogContext.get_context()
    assert "request_id" not in ctx


def test_file_handler(tmp_path):
    log_file = tmp_path / "test.log"
    # Need to remove existing handlers to avoid conflicts with global logging config
    logging.getLogger().handlers = []

    configure_logging(force=True, log_file=str(log_file))

    logger = logging.getLogger("trading_gym.test_file")
    logger.info("Test message file")

    # Close handlers to flush
    for handler in logging.getLogger().handlers:
        handler.flush()
        handler.close()

    assert log_file.exists()
    content = log_file.read_text()
    assert "Test message file" in content
