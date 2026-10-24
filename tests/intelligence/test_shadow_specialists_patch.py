from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import yaml
from trading_gym.common.config import EnvConfig,FeatureConfig
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.intelligence.shadow_specialists import Specialist,MarketFrame,run_shadow_specialists


def make_env():
    raw=yaml.safe_load((Path(__file__).parents[2]/"config/app.yaml").read_text())
    cfg=EnvConfig.model_validate(raw["env"])
    # An explicitly declared synthetic cost fixture, not a broker estimate.
    cfg=cfg.model_copy(update={"slippage":cfg.slippage.model_copy(update={"liquidity_lambda":0.,"volatility_lambda":0.}),
        "costs":cfg.costs.model_copy(update={"taker_fee_bps":1.})})
    features=pd.DataFrame({"spread_ratio":np.full(10,.0001),"volatility":np.zeros(10)},
        index=pd.date_range("2024-01-01",periods=10,freq="5min",tz="UTC"))
    price=np.arange(100.,110.)
    return MarginTradingEnv(features,price,price,price,cfg,FeatureConfig.model_validate(raw["features"]),
        training_stats={"center":{"spread_ratio":0,"volatility":0},"scale":{"spread_ratio":1,"volatility":1}})

def frame(env):
    timestamp=env.feature_df.index[env.t].timestamp()
    return MarketFrame(f"row:{env.t}",timestamp,timestamp)

def specs():return [Specialist("flat","rules-flat",lambda:lambda obs:0),Specialist("long","rules-long",lambda:lambda obs:1)]

def test_real_margin_env_accounts_costs_and_delayed_labels():
    report=run_shadow_specialists(specs(),env_factory=make_env,market_frame=frame,steps=5,horizon_steps=3)
    flat=[s for s in report.steps if s.source_id=="flat"]
    long=[s for s in report.steps if s.source_id=="long"]
    assert all(s.equity_after==10000 and s.turnover_quote==0 for s in flat)
    assert long[0].fee_cost>0 and long[0].equity_after<10000
    assert long[-1].equity_after>long[0].equity_after
    assert all(s.position_after>0 for s in long)
    assert not report.labels_as_of(long[1].decision_at)
    assert not report.labels_as_of(long[2].decision_at)
    assert len(report.labels_as_of(long[3].decision_at))==2
    assert sum(l.censored for l in report.labels)==4
    assert report.to_dict()["funded_eligible"] is False


def test_rejects_duplicate_source_and_shared_account():
    with pytest.raises(ValueError,match="duplicate"):
        run_shadow_specialists([specs()[0],specs()[0]],env_factory=make_env,market_frame=frame,steps=4,horizon_steps=2)
    env=make_env()
    with pytest.raises(ValueError,match="independent"):
        run_shadow_specialists(specs(),env_factory=lambda:env,market_frame=frame,steps=4,horizon_steps=2)


def test_real_environment_matches_independent_reference():
    result=run_shadow_specialists(specs(),env_factory=make_env,market_frame=frame,steps=5,horizon_steps=2)
    reference=make_env();reference.reset(seed=42)
    expected=[]
    for _ in range(5):
        reference.step(np.array([1.],dtype=np.float32));expected.append(float(reference.equity))
    assert [x.equity_after for x in result.steps if x.source_id=="long"]==expected
