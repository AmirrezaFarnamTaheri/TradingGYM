"""Canonical market, instrument, data-routing, catalogue, and workload contracts."""

from trading_gym.market.backtest_policy import (
    BacktestLimitError,
    BacktestPolicy,
    validate_backtest_window,
)
from trading_gym.market.catalog import (
    DatasetCatalog,
    DatasetConflict,
    DatasetRecord,
    DatasetRegistration,
    DatasetState,
)
from trading_gym.market.data_router import (
    DataProviderRouter,
    MarketDataEnvelope,
    MarketDataRequest,
)
from trading_gym.market.fundamental_asof import (
    FundamentalFact,
    FundamentalQuery,
    assess_fundamentals_asof,
)
from trading_gym.market.models import AssetClass, InstrumentSpec
from trading_gym.market.registry import InstrumentConflict, InstrumentRegistry
from trading_gym.market.calendar import (
    CalendarException,
    ExecutionWindowPlan,
    ExecutionWindowRequest,
    LocalSession,
    MaintenanceWindow,
    MarketCalendarSpec,
    MarketSessionReport,
    SessionInterval,
    SessionStatus,
    inspect_market_session,
    plan_execution_window,
)

__all__ = [
    "AssetClass",
    "FundamentalFact",
    "FundamentalQuery",
    "assess_fundamentals_asof",
    "BacktestLimitError",
    "BacktestPolicy",
    "CalendarException",
    "DataProviderRouter",
    "DatasetCatalog",
    "DatasetConflict",
    "DatasetRecord",
    "DatasetRegistration",
    "DatasetState",
    "InstrumentConflict",
    "InstrumentRegistry",
    "InstrumentSpec",
    "ExecutionWindowPlan",
    "ExecutionWindowRequest",
    "LocalSession",
    "MaintenanceWindow",
    "MarketDataEnvelope",
    "MarketDataRequest",
    "MarketCalendarSpec",
    "MarketSessionReport",
    "SessionInterval",
    "SessionStatus",
    "inspect_market_session",
    "plan_execution_window",
    "validate_backtest_window",
]
