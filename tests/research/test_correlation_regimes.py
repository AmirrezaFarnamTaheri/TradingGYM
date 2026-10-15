"""Vibe-Trading correlation hysteresis, TradingGYM time-integrity contracts."""
import numpy as np
import pandas as pd
import pytest
from trading_gym.research.correlation_regimes import (
    correlation_regimes, classify_density_timeline,
)

SHA = 'c'*64

def frame(n=10):
    timestamps = pd.date_range('2026-01-01', periods=n, freq='min', tz='UTC')
    base = [0.01,0.02,0.04,0.03,0.015,0.06,0.05,0.02,0.06,0.01] * ((n+9)//10)
    x = base[:n]
    return pd.DataFrame({'BTC':x,'ETH':x},index=timestamps)


def classify(returns, **kw):
    options = {'corr_window': 3, 'smooth_window': 1}
    options.update(kw)
    return correlation_regimes(returns, source_sha256=SHA,
         as_of_unix=int(returns.index[-1].timestamp()), **options)


def test_perfectly_collinear_returns_produce_fused_regime_after_warmup():
    result=classify(frame())
    assert result['regime'][:2] == ['unknown','unknown']
    assert result['regime'][2:] == ['fused']*8
    assert result['edge_density'][2:] == [1.0]*8
    assert result['edge_count'][2:] == [1]*8
    assert result['non_authoritative'] is True
    assert result['execution_authority_granted'] is False
    assert result['source_sha256'] == SHA


def test_constant_asset_makes_correlation_unknown_not_artificially_diverse():
    returns=frame();returns['FLAT']=0.01
    result=classify(returns)
    assert all(x is None for x in result['edge_density'])
    assert all(x == 'unknown' for x in result['regime'])
    assert result['unknown_bars']==len(returns)


def test_missing_pair_has_unknown_density_without_silently_dropping_asset():
    returns=frame();returns.loc[returns.index[5],'ETH']=np.nan
    result=classify(returns)
    assert result['edge_density'][5] is None
    assert result['regime'][5] == 'unknown'
    assert result['edge_density'][8] == 1.0


def test_causal_prefix_results_never_change_when_future_bars_arrive():
    full=frame(20);prefix=full.iloc[:10].copy()
    a=classify(full);b=classify(prefix)
    assert a['edge_density'][:10] == b['edge_density']
    assert a['regime'][:10] == b['regime']
    assert a['report_sha256'] != b['report_sha256']


def test_asset_permutation_does_not_change_report():
    original=classify(frame())
    swapped=classify(frame()[['ETH','BTC']])
    assert original == swapped


def test_hysteresis_retains_internal_state_across_unknown_evidence():
    out=classify_density_timeline([None,0.8,0.6,0.3,None,0.6,0.8],
       smooth_window=1,enter_threshold=0.7,exit_threshold=0.4)
    assert out['regime'] == ['unknown','fused','fused','diverse','unknown','diverse','fused']
    assert out['smoothed'][4] is None


def test_smoothing_is_trailing_never_uses_future_bars():
    a=classify_density_timeline([0.0,0.0,1.0,1.0],smooth_window=2,
           enter_threshold=0.75,exit_threshold=0.25)
    assert a['smoothed'] == [None,0.0,0.5,1.0]
    assert a['regime'] == ['unknown','diverse','diverse','fused']


@pytest.mark.parametrize('densities',[[-0.1,0.5],[0.2,1.1],[False,0.7],['0.2'],[float('inf')]])
def test_invalid_density_never_becomes_a_regime(densities):
    with pytest.raises(ValueError):
        classify_density_timeline(densities)


@pytest.mark.parametrize('params',[
    dict(enter_threshold=0.3,exit_threshold=0.5),
    dict(smooth_window=0),dict(edge_threshold=1.5),
    dict(corr_window=2),dict(corr_window=True),
])
def test_invalid_regime_configuration_rejected(params):
    with pytest.raises(ValueError):
        classify(frame(),**params)


def test_out_of_time_and_duplicate_market_observations_rejected():
    returns=frame()
    with pytest.raises(ValueError,match='as_of'):
        correlation_regimes(returns,source_sha256=SHA,as_of_unix=int(returns.index[-1].timestamp())-1,corr_window=3)
    bad=returns.copy();bad.index = list(bad.index[:-1])+[bad.index[-2]]
    with pytest.raises(ValueError,match='timestamps'):
        classify(bad)


def test_naive_index_and_nonfinite_prices_are_never_accepted():
    returns=frame();returns.index=returns.index.tz_localize(None)
    with pytest.raises(ValueError,match='timezone'):
        correlation_regimes(returns,source_sha256=SHA,as_of_unix=2000000000,corr_window=3)
    returns=frame();returns.iloc[3,0]=float('inf')
    with pytest.raises(ValueError,match='nonfinite'):
        classify(returns)


def test_source_hash_must_be_canonical_and_report_stable():
    with pytest.raises(ValueError):
        correlation_regimes(frame(),source_sha256='not-a-hash',as_of_unix=2000000000,corr_window=3)
    assert classify(frame())['report_sha256'] == classify(frame())['report_sha256']


def test_bounded_assets_and_histories_require_a_valid_return_matrix():
    many=frame()
    for i in range(15):many[f'asset{i:02d}']=list(many['BTC'])
    with pytest.raises(ValueError):
        classify(many)
    with pytest.raises(ValueError):
        classify(frame().iloc[:2])
