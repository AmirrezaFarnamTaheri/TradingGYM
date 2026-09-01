"""
Logging Utilities for Trading Gym

This module provides comprehensive logging configuration for the Trading Gym
application, including:

- Structured logging with consistent formatting
- Environment-based configuration
- File and console handlers
- Log level management
- Performance logging helpers

Usage:
    from trading_gym.common.logging import configure_logging, get_logger

    # Configure once at application startup
    configure_logging(level="INFO")

    # Get a logger for your module
    logger = get_logger(__name__)
    logger.info("Application started")

Environment Variables:
    TG_LOG_LEVEL: Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    TG_LOG_FILE: Path to log file (optional)
    TG_LOG_FORMAT: Log format string (optional)

Version: 4.2.0
"""

from __future__ import annotations

import logging
import os
import sys
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any
import time

# =============================================================================
# Constants
# =============================================================================

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

# =============================================================================
# Core Functions
# =============================================================================


def configure_logging(
    level: int | str = logging.INFO,
    *,
    force: bool = False,
    log_file: Optional[str | Path] = None,
    detailed: bool = False,
) -> None:
    """
    Configure application-wide logging with a consistent formatter.

    This function sets up the root logger with appropriate handlers and
    formatting. Call this once at application startup.

    Parameters
    ----------
    level : int | str
        Logging level or level name. Defaults to ``logging.INFO``.
        Accepted strings: DEBUG, INFO, WARNING, ERROR, CRITICAL
    force : bool
        When ``True`` the root handlers are reconfigured even if logging has
        already been set up elsewhere.
    log_file : str | Path, optional
        Path to a log file. If provided, logs will also be written to this file.
    detailed : bool
        If ``True``, use a more detailed format including line numbers and
        function names. Useful for debugging.

    Examples
    --------
    >>> configure_logging(level="DEBUG", detailed=True)
    >>> logger = get_logger(__name__)
    >>> logger.debug("Debug message")
    """
    resolved_level = _resolve_level(level)
    log_format = DETAILED_FORMAT if detailed else DEFAULT_FORMAT

    # Configure handlers
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Add file handler if requested
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
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
    """
    Configure logging using environment variables.

    Uses the following environment variables:
    - TG_LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - TG_LOG_FILE: Path to log file (optional)
    - TG_LOG_DETAILED: If "true", use detailed format

    Parameters
    ----------
    default : int | str
        Default log level if TG_LOG_LEVEL is not set.
    force : bool
        Reconfigure even if already configured.
    """
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
    """
    Get a logger with the specified name.

    This is a convenience function that returns a properly configured logger.
    Use it in modules to get a logger with the module name.

    Parameters
    ----------
    name : str
        Logger name, typically ``__name__`` for module-level loggers.

    Returns
    -------
    logging.Logger
        Configured logger instance.

    Examples
    --------
    >>> logger = get_logger(__name__)
    >>> logger.info("Processing started")
    """
    return logging.getLogger(name)


# =============================================================================
# Helper Functions
# =============================================================================


def _resolve_level(level: int | str) -> int:
    """
    Resolve a log level to its integer value.

    Parameters
    ----------
    level : int | str
        Log level as integer or string name.

    Returns
    -------
    int
        Resolved log level integer.
    """
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


# =============================================================================
# Performance Logging Decorators
# =============================================================================


def log_execution_time(
    logger: Optional[logging.Logger] = None,
    level: int = logging.DEBUG,
) -> Callable:
    """
    Decorator to log the execution time of a function.

    Parameters
    ----------
    logger : logging.Logger, optional
        Logger to use. If not provided, uses the module logger.
    level : int
        Log level to use for the timing message.

    Examples
    --------
    >>> @log_execution_time()
    ... def slow_function():
    ...     time.sleep(1)
    ...     return "done"
    """

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
    """
    Decorator to log function calls with optional argument and result logging.

    Parameters
    ----------
    logger : logging.Logger, optional
        Logger to use.
    level : int
        Log level to use.
    log_args : bool
        If True, log the function arguments.
    log_result : bool
        If True, log the function result.
    """

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


# =============================================================================
# Structured Logging Context
# =============================================================================


class LogContext:
    """
    Context manager for adding context to log messages.

    Useful for adding correlation IDs or other context information
    to all log messages within a block.

    Examples
    --------
    >>> with LogContext(request_id="abc123"):
    ...     logger.info("Processing request")  # Will include request_id context
    """

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


# =============================================================================
# Specialized Loggers
# =============================================================================


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


# =============================================================================
# Exports
# =============================================================================

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
