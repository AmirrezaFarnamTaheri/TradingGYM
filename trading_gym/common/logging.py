"""Process-wide logging configuration and small timing decorators.

Set ``TG_LOG_LEVEL`` / ``TG_LOG_FILE`` / ``TG_LOG_DETAILED`` to configure logging
from the environment, or call :func:`configure_logging` directly at startup.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any
import time

DEFAULT_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s]: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DETAILED_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)-8s] [%(name)s:%(lineno)d] [%(funcName)s]: %(message)s"

# Log level mapping
LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}

def configure_logging(
    level: int | str = logging.INFO,
    *,
    force: bool = False,
    log_file: Optional[str | Path] = None,
    detailed: bool = False,
) -> None:
    """Install a consistent formatter on the root logger. Call once at startup."""
    resolved_level = _resolve_level(level)
    log_format = DETAILED_FORMAT if detailed else DEFAULT_FORMAT

    # Configure handlers
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Add file handler if requested
    if log_file:
        file_path = Path(log_file)
        # Every other persistence surface in this codebase is 0700 dir / 0600
        # file because logs carry redacted-but-sensitive operational detail;
        # the default mkdir/FileHandler modes left both world-readable on POSIX.
        # Create the log with a restrictive mode BEFORE the handler opens it:
        # chmod-after-open leaves a world-readable window, and a fresh file
        # never gets chmodded at all.
        file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(
                file_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            os.close(descriptor)
            # os.open honours the mode only on POSIX; on Windows the mode is
            # not a filesystem property, so also chmod to cover a POSIX mount.
            file_path.chmod(0o600)
        except OSError:
            # The filesystem may ignore the mode (Windows); the handler below
            # still opens the file for our use only.
            pass
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handlers.append(file_handler)

    # Apply configuration
    logging.basicConfig(
        level=resolved_level,
        format=log_format,
        datefmt=DEFAULT_DATE_FORMAT,
        handlers=handlers,
        force=force,
    )

    # Set specific loggers to less verbose levels
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def configure_logging_from_env(
    default: int | str = logging.INFO,
    *,
    force: bool = False,
) -> None:
    """Configure logging from ``TG_LOG_LEVEL`` / ``TG_LOG_FILE`` / ``TG_LOG_DETAILED``."""
    env_level: Optional[str] = os.getenv("TG_LOG_LEVEL")
    log_file: Optional[str] = os.getenv("TG_LOG_FILE")
    detailed: bool = os.getenv("TG_LOG_DETAILED", "").lower() == "true"

    configure_logging(
        level=env_level or default,
        force=force,
        log_file=log_file,
        detailed=detailed,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name`` (usually ``__name__``)."""
    return logging.getLogger(name)


def _resolve_level(level: int | str) -> int:
    """Resolve a level name or integer to its numeric level."""
    if isinstance(level, int):
        return level

    if isinstance(level, str):
        upper_level = level.upper()
        if upper_level in LEVEL_MAP:
            return LEVEL_MAP[upper_level]
        # Try using logging's getLevelName
        val = logging.getLevelName(upper_level)
        return int(val) if isinstance(val, int) else logging.INFO

    return logging.INFO


def log_execution_time(
    logger: Optional[logging.Logger] = None,
    level: int = logging.DEBUG,
) -> Callable:
    """Decorate a function to log its wall-clock execution time."""

    def decorator(func: Callable) -> Callable:
        func_logger = logger or logging.getLogger(func.__module__)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter() - start_time
                func_logger.log(
                    level,
                    "%s executed in %.4f seconds",
                    func.__qualname__,
                    elapsed,
                )

        return wrapper

    return decorator


def log_function_call(
    logger: Optional[logging.Logger] = None,
    level: int = logging.DEBUG,
    log_args: bool = False,
    log_result: bool = False,
) -> Callable:
    """Decorate a function to log its calls, optionally with arguments and results."""

    def decorator(func: Callable) -> Callable:
        func_logger = logger or logging.getLogger(func.__module__)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if log_args:
                func_logger.log(
                    level,
                    "Calling %s with args=%s, kwargs=%s",
                    func.__qualname__,
                    args,
                    kwargs,
                )
            else:
                func_logger.log(level, "Calling %s", func.__qualname__)

            result = func(*args, **kwargs)

            if log_result:
                func_logger.log(
                    level,
                    "%s returned %s",
                    func.__qualname__,
                    result,
                )

            return result

        return wrapper

    return decorator


class LogContext:
    """Thread-local-ish context store; read it from your own formatters."""

    _context: dict[str, Any] = {}

    def __init__(self, **kwargs: Any):
        self._new_context = kwargs
        self._old_context: dict[str, Any] = {}

    def __enter__(self) -> "LogContext":
        self._old_context = LogContext._context.copy()
        LogContext._context.update(self._new_context)
        return self

    def __exit__(self, *args: Any) -> None:
        LogContext._context = self._old_context

    @classmethod
    def get_context(cls) -> dict[str, Any]:
        """Get the current logging context."""
        return cls._context.copy()


def get_trading_logger() -> logging.Logger:
    """Get a logger specifically for trading operations."""
    return get_logger("trading_gym.trading")


def get_training_logger() -> logging.Logger:
    """Get a logger specifically for training operations."""
    return get_logger("trading_gym.training")


def get_api_logger() -> logging.Logger:
    """Get a logger specifically for API operations."""
    return get_logger("trading_gym.api")


def get_data_logger() -> logging.Logger:
    """Get a logger specifically for data operations."""
    return get_logger("trading_gym.data")


__all__ = [
    "configure_logging",
    "configure_logging_from_env",
    "get_logger",
    "log_execution_time",
    "log_function_call",
    "LogContext",
    "get_trading_logger",
    "get_training_logger",
    "get_api_logger",
    "get_data_logger",
]
