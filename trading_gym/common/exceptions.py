"""
Trading System Exception Hierarchy
===================================

Structured exception taxonomy for clear error handling and alerting.

Exception hierarchy:
- TradingError (base)
  - APIError
    - AuthenticationError
    - RateLimitError
    - ConnectionError
    - TimeoutError
  - ValidationError
    - OrderValidationError
    - ConfigValidationError
  - ExecutionError
    - InsufficientBalanceError
    - LiquidityError
    - OrderRejectedError
  - RiskError
    - CircuitBreakerTrippedError
    - LeverageExceededError
    - MarginCallError
  - DataError
    - MissingDataError
    - CorruptDataError
"""

from typing import Any, Dict, Optional


class TradingError(Exception):
    """
    Base exception for all trading system errors.

    Attributes:
        message: Human-readable error message
        code: Machine-readable error code
        context: Additional context for debugging
    """

    def __init__(
        self,
        message: str,
        code: str = "TRADING_ERROR",
        context: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.code = code
        self.context = context or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        ctx_str = f" | context={self.context}" if self.context else ""
        return f"[{self.code}] {self.message}{ctx_str}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/alerting."""
        return {
            "error_type": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "context": self.context,
        }


# --- API Errors ---


class AbanAPIError(TradingError):
    """Base class for Abantether API errors."""

    def __init__(
        self,
        message: str,
        code: str = "API_ERROR",
        http_status: Optional[int] = None,
        endpoint: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        response: Optional[Dict[str, Any]] = None,
    ):
        context = {
            "http_status": http_status,
            "endpoint": endpoint,
            "payload": payload,
            "response": response,
        }
        super().__init__(message, code, context)
        self.http_status = http_status
        self.endpoint = endpoint


class AuthenticationError(AbanAPIError):
    """API authentication failed."""

    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(message, code="AUTH_ERROR", **kwargs)


class RateLimitError(AbanAPIError):
    """API rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, code="RATE_LIMIT", **kwargs)
        self.retry_after = retry_after
        if retry_after:
            self.context["retry_after"] = retry_after


class APIConnectionError(AbanAPIError):
    """Failed to connect to API."""

    def __init__(self, message: str = "Connection failed", **kwargs):
        super().__init__(message, code="CONNECTION_ERROR", **kwargs)


class APITimeoutError(AbanAPIError):
    """API request timed out."""

    def __init__(
        self,
        message: str = "Request timed out",
        timeout: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, code="TIMEOUT_ERROR", **kwargs)
        if timeout:
            self.context["timeout"] = timeout


# --- Validation Errors ---


class ValidationError(TradingError):
    """Base class for validation errors."""

    def __init__(self, message: str, code: str = "VALIDATION_ERROR", **kwargs):
        super().__init__(message, code, **kwargs)


class OrderValidationError(ValidationError):
    """Order failed pre-trade validation."""

    def __init__(
        self,
        message: str,
        code: str = "ORDER_VALIDATION",
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update(
            {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
            }
        )
        super().__init__(message, code, context=context)


class ConfigValidationError(ValidationError):
    """Configuration validation failed."""

    def __init__(
        self,
        message: str,
        config_path: Optional[str] = None,
        field: Optional[str] = None,
        **kwargs,
    ):
        context = {"config_path": config_path, "field": field}
        super().__init__(message, code="CONFIG_VALIDATION", context=context)


# --- Execution Errors ---


class ExecutionError(TradingError):
    """Base class for trade execution errors."""

    def __init__(
        self,
        message: str,
        code: str = "EXECUTION_ERROR",
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        qty: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"symbol": symbol, "side": side, "qty": qty})
        super().__init__(message, code, context=context)


class InsufficientBalanceError(ExecutionError):
    """Insufficient balance for trade."""

    def __init__(
        self,
        message: str = "Insufficient balance",
        symbol: Optional[str] = None,
        required: Optional[float] = None,
        available: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"required": required, "available": available})
        super().__init__(
            message,
            code="INSUFFICIENT_BALANCE",
            symbol=symbol,
            context=context,
            **kwargs,
        )


class LiquidityError(ExecutionError):
    """Insufficient liquidity for trade."""

    def __init__(
        self,
        message: str = "Insufficient liquidity",
        requested_notional: Optional[float] = None,
        available_liquidity: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update(
            {
                "requested_notional": requested_notional,
                "available_liquidity": available_liquidity,
            }
        )
        super().__init__(
            message, code="INSUFFICIENT_LIQUIDITY", context=context, **kwargs
        )


class OrderRejectedError(ExecutionError):
    """Order was rejected by exchange."""

    def __init__(
        self,
        message: str = "Order rejected",
        order_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"order_id": order_id, "rejection_reason": rejection_reason})
        super().__init__(message, code="ORDER_REJECTED", context=context, **kwargs)


# --- Risk Errors ---


class RiskError(TradingError):
    """Base class for risk management errors."""

    def __init__(
        self,
        message: str,
        code: str = "RISK_ERROR",
        equity: Optional[float] = None,
        nav: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"equity": equity, "nav": nav})
        super().__init__(message, code, context=context)


class CircuitBreakerTrippedError(RiskError):
    """Circuit breaker was tripped."""

    def __init__(
        self,
        message: str = "Circuit breaker tripped",
        breaker_name: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"breaker_name": breaker_name, "reason": reason})
        super().__init__(
            message, code="CIRCUIT_BREAKER_TRIPPED", context=context, **kwargs
        )


class LeverageExceededError(RiskError):
    """Leverage limit exceeded."""

    def __init__(
        self,
        message: str = "Leverage exceeded",
        current_leverage: Optional[float] = None,
        max_leverage: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update(
            {
                "current_leverage": current_leverage,
                "max_leverage": max_leverage,
            }
        )
        super().__init__(message, code="LEVERAGE_EXCEEDED", context=context, **kwargs)


class MarginCallError(RiskError):
    """Margin call triggered."""

    def __init__(
        self,
        message: str = "Margin call",
        margin_ratio: Optional[float] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"margin_ratio": margin_ratio, "threshold": threshold})
        super().__init__(message, code="MARGIN_CALL", context=context, **kwargs)


# --- Data Errors ---


class DataError(TradingError):
    """Base class for data-related errors."""

    def __init__(
        self,
        message: str,
        code: str = "DATA_ERROR",
        data_source: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"data_source": data_source})
        super().__init__(message, code, context=context)


class MissingDataError(DataError):
    """Required data is missing."""

    def __init__(
        self,
        message: str = "Missing data",
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"symbol": symbol, "timeframe": timeframe})
        super().__init__(message, code="MISSING_DATA", context=context, **kwargs)


class CorruptDataError(DataError):
    """Data is corrupted or invalid."""

    def __init__(self, message: str = "Corrupt data", **kwargs):
        super().__init__(message, code="CORRUPT_DATA", **kwargs)


# --- Model Errors ---


class ModelError(TradingError):
    """Base class for model-related errors."""

    def __init__(
        self,
        message: str,
        code: str = "MODEL_ERROR",
        model_version: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"model_version": model_version})
        super().__init__(message, code, context=context)


class ModelLoadError(ModelError):
    """Failed to load model."""

    def __init__(
        self,
        message: str = "Failed to load model",
        model_path: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context.update({"model_path": model_path})
        super().__init__(message, code="MODEL_LOAD_ERROR", context=context, **kwargs)


class ModelInferenceError(ModelError):
    """Model inference failed."""

    def __init__(self, message: str = "Model inference failed", **kwargs):
        super().__init__(message, code="MODEL_INFERENCE_ERROR", **kwargs)


# --- Utility functions ---


def wrap_api_error(func):
    """
    Decorator to wrap API calls and convert exceptions to AbanAPIError.

    Example:
        @wrap_api_error
        def get_balance(self):
            return self._client.balance()
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AbanAPIError:
            # Already wrapped, re-raise
            raise
        except Exception as e:
            # Wrap in AbanAPIError
            raise AbanAPIError(
                message=f"API call failed: {str(e)}",
                code="API_ERROR",
            ) from e

    return wrapper


def is_retryable_error(error: Exception) -> bool:
    """
    Check if an error is retryable.

    Retryable errors:
    - RateLimitError
    - APIConnectionError
    - APITimeoutError
    - 5xx HTTP errors
    """
    if isinstance(error, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True

    if isinstance(error, AbanAPIError):
        if error.http_status and 500 <= error.http_status < 600:
            return True

    return False


class StoreLimitExceeded(TradingError):
    """A durable store refused a write because it would exceed a hard limit.

    Raised by the persistence layer when a document would cross the byte cap,
    and by stores when a configured capacity limit is reached. Callers can catch
    this to distinguish "the request was too big" from "the store is corrupt" —
    a bare ValueError is indistinguishable from a programming error and reaches
    callers that only handle the domain conflict types.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: Optional[int] = None,
        attempted: Optional[int] = None,
        code: str = "STORE_LIMIT_EXCEEDED",
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx = dict(context or {})
        if limit is not None:
            ctx["limit"] = limit
        if attempted is not None:
            ctx["attempted"] = attempted
        super().__init__(message, code=code, context=ctx)
