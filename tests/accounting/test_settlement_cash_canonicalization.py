"""Settlement cash must be canonical, lossless, and never silently overwritten."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.execution.accounting.settlement import evaluate_settlement


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_duplicate_currency_aliases_do_not_overwrite_available_cash():
    with pytest.raises(ValueError, match="duplicate currency"):
        evaluate_settlement(
            as_of=NOW,
            settled_cash={
                "USD": Decimal("120.00"),
                "usd": Decimal("55.00"),
            },
            obligations=[],
        )


@pytest.mark.parametrize(
    "cash",
    [
        {"US$": Decimal("100")},
        {"ß": Decimal("100")},  # Unicode uppercases to ASCII "SS"
        {"U": Decimal("100")},
        {"": Decimal("100")},
        {123: Decimal("100")},
        {"USD": 100.0},
        {"USD": True},
        {"USD": Decimal("NaN")},
        {"USD": Decimal("Infinity")},
    ],
)
def test_invalid_cash_inputs_fail_with_stable_domain_error(cash):
    with pytest.raises(ValueError, match="settled cash"):
        evaluate_settlement(as_of=NOW, settled_cash=cash, obligations=[])


def test_single_lowercase_currency_is_normalized_without_value_loss():
    report = evaluate_settlement(
        as_of=NOW,
        settled_cash={"usd": Decimal("123.45")},
        obligations=[],
    )
    assert report.projected_available_cash == {"USD": Decimal("123.45")}
    assert report.blockers == []
    assert report.settlement_authority_granted is False
