"""Data-source registry with compliance and resilience metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DataSource:
    source_id: str
    name: str
    categories: tuple[str, ...]
    auth_env_var: str | None
    terms_url: str
    redistribution_allowed: bool
    primary: bool
    notes: str = ""


DEFAULT_SOURCES: tuple[DataSource, ...] = (
    DataSource("sec_edgar", "SEC EDGAR", ("filings", "company"), None, "https://www.sec.gov/os/accessing-edgar-data", True, True, "Respect SEC fair-access guidance and identify the client."),
    DataSource("fred", "Federal Reserve Economic Data", ("macro",), "FRED_API_KEY", "https://fred.stlouisfed.org/docs/api/terms_of_use.html", True, True),
    DataSource("world_bank", "World Bank Open Data", ("macro",), None, "https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets", True, True),
    DataSource("exchange", "Configured exchange market data", ("ohlcv", "orderbook"), "EXCHANGE_API_KEY", "operator-configured", False, True, "Redistribution depends on venue terms."),
    DataSource("news", "Configured news provider", ("news",), "NEWS_API_KEY", "operator-configured", False, False, "Store citations and derived facts, not licensed article bodies."),
)


def source_catalogue() -> list[dict[str, object]]:
    return [asdict(source) for source in DEFAULT_SOURCES]
