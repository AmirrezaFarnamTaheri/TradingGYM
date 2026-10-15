"""Jeeva/Vibe funding-accounting cross-implementation contracts."""
from dataclasses import replace
from decimal import Decimal
import pytest
from trading_gym.accounting.funding_reconciliation import (
    FundingDue, FundingReceipt, reconcile_funding,
)

HASH_A = 'a'*64
HASH_B = 'b'*64


def due(event='fund-001', **kw):
    v = dict(event_id=event, account_id='account-001', venue_id='hyperliquid',
             instrument_id='BTC-USDT-PERP', currency='USDT', direction='long',
             notional_quote=Decimal('1000'), funding_rate=Decimal('0.0001'),
             effective_at_unix=1000, source_sha256=HASH_A)
    v.update(kw)
    return FundingDue(**v)


def receipt(event='fund-001', **kw):
    v = dict(receipt_id='receipt-001', event_id=event, account_id='account-001',
             venue_id='hyperliquid', instrument_id='BTC-USDT-PERP',
             currency='USDT', settled_at_unix=1010,
             cash_delta_quote=Decimal('-0.10'), source_sha256=HASH_B)
    v.update(kw)
    return FundingReceipt(**v)


def check(dues, receipts, *, as_of=2000, tol=Decimal('0.01')):
    return reconcile_funding(dues=dues, receipts=receipts,
       account_id='account-001', as_of_unix=as_of, allowed_absolute_delta=tol)


def test_long_pays_when_positive_and_short_receives_when_positive():
    ds = [due('fund-001'), due('fund-002', direction='short')]
    rs = [receipt(), receipt('fund-002', receipt_id='receipt-002', cash_delta_quote=Decimal('0.1'))]
    report = check(ds, rs)
    assert report['consistent'] is True
    assert report['expected_cash_by_currency'] == {'USDT': '0'}
    assert report['observed_cash_by_currency'] == {'USDT': '0'}
    assert report['unexplained_cash_by_currency'] == {'USDT': '0'}
    assert report['funded_live_approved'] is False
    assert report['mutation_performed'] is False


def test_negative_rate_reverses_long_and_short_conventions():
    ds = [due('fund-001', funding_rate=Decimal('-0.001')),
          due('fund-002', direction='short', funding_rate=Decimal('-0.001'))]
    rs = [receipt(cash_delta_quote=Decimal('1')),
          receipt('fund-002', receipt_id='receipt-002', cash_delta_quote=Decimal('-1'))]
    assert check(ds, rs)['consistent'] is True


def test_wrong_cash_amount_is_reported_as_unreconciled_not_auto_adjusted():
    report = check([due()], [receipt(cash_delta_quote=Decimal('-0.25'))])
    assert report['consistent'] is False
    assert any(x.startswith('funding_amount_mismatch') for x in report['blockers'])
    assert report['unexplained_cash_by_currency'] == {'USDT': '-0.15'}


def test_boundary_tolerance_is_inclusive():
    assert check([due()], [receipt(cash_delta_quote=Decimal('-0.11'))])['consistent'] is True
    assert check([due()], [receipt(cash_delta_quote=Decimal('-0.111'))])['consistent'] is False


def test_missing_receipt_and_unmatched_payment_fail_closed():
    a = check([due()], [])
    b = check([], [receipt()])
    assert a['consistent'] is False and 'missing_funding_receipt:fund-001' in a['blockers']
    assert b['consistent'] is False and 'unmatched_funding_receipt:receipt-001' in b['blockers']


def test_empty_claims_never_prove_a_clean_funding_settlement():
    result = check([], [])
    assert result['consistent'] is False
    assert 'missing_funding_evidence' in result['blockers']


def test_duplicate_payment_to_one_obligation_is_rejected_as_ambiguous():
    result = check([due()], [receipt(), receipt(receipt_id='receipt-002')])
    assert result['consistent'] is False
    assert 'duplicate_funding_settlement:fund-001' in result['blockers']


def test_mismatched_source_scope_is_not_accepted_as_a_payment():
    for changes in [dict(currency='USD'),dict(venue_id='binance'),dict(instrument_id='ETH-USDT-PERP')]:
        result = check([due()], [receipt(**changes)])
        assert not result['consistent']
        assert any('funding_scope_mismatch' in x for x in result['blockers'])


def test_funding_paid_before_its_effective_time_is_flagged():
    result=check([due()], [receipt(settled_at_unix=999)])
    assert result['consistent'] is False
    assert 'funding_settlement_before_due:fund-001' in result['blockers']


def test_reject_future_claims_and_as_of_lookahead():
    for ds,rs,at in [([due(effective_at_unix=2001)],[receipt()],2000),
                      ([due()],[receipt(settled_at_unix=2001)],2000)]:
        with pytest.raises(ValueError, match='as_of'):
            check(ds,rs,as_of=at)


def test_duplicate_evidence_identity_is_not_silently_deduplicated():
    with pytest.raises(ValueError, match='duplicate'):
        check([due(), due()], [receipt()])
    with pytest.raises(ValueError, match='duplicate'):
        check([due()], [receipt(),receipt()])


@pytest.mark.parametrize('patch', [
    dict(funding_rate=True),dict(funding_rate=0.01),
    dict(funding_rate=Decimal('NaN')),dict(funding_rate=Decimal('Infinity')),
    dict(notional_quote=Decimal('0')),dict(notional_quote=Decimal('-1')),
    dict(source_sha256='not-sha'),dict(direction='buy'),
    dict(effective_at_unix=True),dict(account_id='different-account'),
])
def test_bad_due_is_rejected_without_mutation(patch):
    with pytest.raises(ValueError):
        check([due(**patch)], [receipt()])


@pytest.mark.parametrize('patch', [
    dict(cash_delta_quote=True),dict(cash_delta_quote=0.3),
    dict(cash_delta_quote=Decimal('NaN')),dict(source_sha256='bad'),
    dict(receipt_id=''),dict(settled_at_unix=-1),
    dict(account_id='different-account')
])
def test_bad_cash_receipt_is_rejected_without_mutation(patch):
    with pytest.raises(ValueError):
        check([due()], [receipt(**patch)])


def test_replay_report_hash_is_order_invariant_and_sensitive_to_cash_evidence():
    a,b=due(),due('fund-002',direction='short')
    x,y=receipt(),receipt('fund-002',receipt_id='receipt-002',cash_delta_quote=Decimal('0.1'))
    first=check([a,b],[x,y])
    assert first == check([b,a],[y,x])
    assert first['report_sha256'] != check([a,b],[x,replace(y, cash_delta_quote=Decimal('0.11'))])['report_sha256']
    assert len(first['report_sha256']) == 64


def test_invalid_tolerance_and_account_scope_rejected():
    with pytest.raises(ValueError):
        check([due()],[receipt()],tol=Decimal('-0.01'))
    with pytest.raises(ValueError):
        check([due()],[receipt()],tol=Decimal('NaN'))
    with pytest.raises(ValueError):
        reconcile_funding(dues=[due()], receipts=[receipt()], account_id='other', as_of_unix=2000)


def test_small_per_event_funding_rounding_errors_cannot_hide_material_net_drift():
    # A reconciler may tolerate one 0.01-unit rounding difference,
    # but must not report all-clear when a sequence accumulates 0.02.
    obligations = [due("fund-001"), due("fund-002")]
    settled = [
        receipt("fund-001", cash_delta_quote=Decimal("-0.11")),
        receipt(
            "fund-002",
            receipt_id="receipt-002",
            cash_delta_quote=Decimal("-0.11"),
        ),
    ]
    report = check(obligations, settled, tol=Decimal("0.01"))
    assert report["unexplained_cash_by_currency"] == {"USDT": "-0.02"}
    assert report["consistent"] is False
    assert "funding_aggregate_mismatch:USDT" in report["blockers"]


def test_aggregate_funding_tolerance_remains_inclusive():
    obligations = [due("fund-001"), due("fund-002")]
    settled = [
        receipt("fund-001", cash_delta_quote=Decimal("-0.11")),
        receipt(
            "fund-002",
            receipt_id="receipt-002",
            cash_delta_quote=Decimal("-0.10"),
        ),
    ]
    result = check(obligations, settled, tol=Decimal("0.01"))
    assert result["consistent"] is True
    assert result["unexplained_cash_by_currency"] == {"USDT": "-0.01"}
