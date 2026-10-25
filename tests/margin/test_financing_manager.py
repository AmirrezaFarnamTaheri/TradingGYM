import pytest
import math
from trading_gym.execution.margin.financing_manager import FinancingRateManager
from trading_gym.core.config import (
    AppConfig,
    LeverageConfig,
    FinancingConfig,
    EnvConfig,
    FeatureConfig,
    DataConfig,
    PPOHyperParameters,
    ShortsConfig,
    BreakerConfig,
    TrainingConfig,
    EvaluationConfig,
    PortfolioConfig,
    MonitoringConfig,
    PaperTraderConfig,
    SimulatorConfig,
    CostConfig,
    SlippageConfig,
    LiquidityConfig,
    CapacityConfig,
    VenueDecimals,
    WalkForwardConfig,
    RiskParityConfig,
    CorrelationBrakeConfig,
    EarlyStopConfig,
    TimeframeConfig,
    StandardizationConfig,
    AttentionConfig,
)


class TestFinancingRateManager:
    @pytest.fixture
    def real_config(self):
        # Create minimal valid config components
        financing = FinancingConfig(daily_pct=0.05, periods_per_day=288)
        leverage = LeverageConfig(
            min_leverage=1.0,
            max_leverage=10.0,
            max_short_leverage=5.0,
            warning_margin_ratio=0.2,
            liquidation_margin_ratio=0.1,
            financing=financing,
        )

        env_config = EnvConfig(
            symbol="BTCUSDT",
            quote_ccy="USDT",
            decimals=VenueDecimals(),
            leverage=leverage,
            shorts=ShortsConfig(),
            costs=CostConfig(
                taker_fee_bps=5.0, dead_zone_equity_frac=0.01, max_spread_bps=10.0
            ),
            slippage=SlippageConfig(
                volatility_lambda=0.1,
                liquidity_lambda=0.1,
                stress_multiplier=1.0,
                volume_reference_fraction=0.01,
            ),
            liquidity=LiquidityConfig(default_quote_cap=1000.0),
            capacity=CapacityConfig(conservative_default=100000.0),
            breaker=BreakerConfig(margin_ratio_floor=0.15),
        )

        # Training config requires PPOHyperParameters
        ppo = PPOHyperParameters(
            n_steps=128,
            batch_size=64,
            learning_rate=0.0003,
            gamma=0.99,
            gae_lambda=0.95,
            ent_coef=0.01,
            vf_coef=0.5,
            clip_range=0.2,
            max_grad_norm=0.5,
        )

        training = TrainingConfig(
            algo="ppo",
            device="cpu",
            total_timesteps=1000,
            eval_freq=100,
            n_envs=1,
            initial_equity=1000.0,
            ppo=ppo,
            early_stop=EarlyStopConfig(liquidation_rate=0.5, check_freq=100),
        )

        features = FeatureConfig(
            timeframes=[TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=10)],
            standardization=StandardizationConfig(),
            attention=AttentionConfig(),
        )

        evaluation = EvaluationConfig(
            walkforward=WalkForwardConfig(
                n_splits=3,
                min_train_fraction=0.5,
                lookahead_bars=100,
                stress_cost_multiplier=1.0,
            )
        )

        portfolio = PortfolioConfig(
            risk_parity=RiskParityConfig(
                target_vol_ann=0.2,
                weight_cap=0.5,
                stablecoin_min=0.1,
                window_days=30,
                shrinkage="ledoit_wolf",
                turnover_penalty=0.001,
            ),
            corr_brake=CorrelationBrakeConfig(
                warn=0.7, crit=0.9, release=0.5, cut_warn=0.5, cut_crit=1.0
            ),
        )

        monitoring = MonitoringConfig(metrics_port=8000)
        paper = PaperTraderConfig(trade_size_pct_equity=0.1)

        return AppConfig(
            env=env_config,
            data=DataConfig(),
            features=features,
            training=training,
            evaluation=evaluation,
            portfolio=portfolio,
            monitoring=monitoring,
            paper_trader=paper,
            simulator=SimulatorConfig(steps=100),
        )

    def test_initialization(self, real_config):
        manager = FinancingRateManager(real_config)
        assert manager.daily_rate_decimal == 0.0005  # 0.05 / 100
        assert manager.rates == {}

    def test_calculate_financing_simple(self, real_config):
        manager = FinancingRateManager(real_config)
        borrowed = 10000.0

        # 1 day (288 bars of 5 mins)
        bars = 24 * 60 // 5
        cost = manager.calculate_financing("BTC", borrowed, bars)

        # Compound formula: Principal * ((1+r)^t - 1)
        # Here t=1 day. The manager calculates rate_per_bar.
        # (1+r_bar)^bars = (1+r_daily)^1

        # So expected cost = 10000 * ((1 + 0.0005)^1 - 1) = 5.0
        assert math.isclose(cost, 5.0, rel_tol=1e-9)

    def test_calculate_financing_compound(self, real_config):
        manager = FinancingRateManager(real_config)
        borrowed = 10000.0

        # 2 days
        bars = 2 * (24 * 60 // 5)
        cost = manager.calculate_financing("BTC", borrowed, bars)

        # Expected: 10000 * ((1.0005)^2 - 1)
        expected = 10000 * ((1.0005) ** 2 - 1)
        assert math.isclose(cost, expected, rel_tol=1e-9)

    def test_calculate_financing_partial_day(self, real_config):
        manager = FinancingRateManager(real_config)
        borrowed = 10000.0
        bars = 144  # Half day

        cost = manager.calculate_financing("BTC", borrowed, bars)

        # Half day: 10000 * ((1.0005)^0.5 - 1)
        expected = 10000 * ((1.0005) ** 0.5 - 1)
        assert math.isclose(cost, expected, rel_tol=1e-9)

    def test_calculate_financing_from_minutes(self, real_config):
        manager = FinancingRateManager(real_config)
        borrowed = 10000.0
        minutes = 720  # Half day

        cost = manager.calculate_financing_from_minutes("BTC", borrowed, minutes)

        expected = 10000 * ((1.0005) ** 0.5 - 1)
        assert math.isclose(cost, expected, rel_tol=1e-9)

    def test_update_rate(self, real_config):
        manager = FinancingRateManager(real_config)
        new_rate = 0.001  # 0.1%

        manager.update_rate("ETH", new_rate)

        assert manager.get_rate("ETH") == 0.001
        assert manager.get_rate("BTC") == 0.0005  # Default remains
        assert "ETH" in manager.last_updated

        # Verify calculation uses new rate
        cost = manager.calculate_financing_from_minutes("ETH", 10000, 1440)
        assert math.isclose(cost, 10.0, rel_tol=1e-9)  # 10000 * 0.001

    def test_invalid_rate_update(self, real_config):
        manager = FinancingRateManager(real_config)
        with pytest.raises(ValueError):
            manager.update_rate("BTC", -0.01)

    @pytest.mark.parametrize("bad_rate", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_rate_update_is_rejected(self, real_config, bad_rate):
        # A NaN passes a `rate < 0` guard (all NaN comparisons are False) and
        # then poisons every financing cost derived from it; pin the rejection.
        manager = FinancingRateManager(real_config)
        with pytest.raises(ValueError):
            manager.update_rate("BTC", bad_rate)
        assert "BTC" not in manager.rates

    def test_zero_borrow_or_time(self, real_config):
        manager = FinancingRateManager(real_config)
        assert manager.calculate_financing("BTC", 0, 100) == 0.0
        assert manager.calculate_financing("BTC", 1000, 0) == 0.0
        assert manager.calculate_financing_from_minutes("BTC", 0, 100) == 0.0
        assert manager.calculate_financing_from_minutes("BTC", 1000, 0) == 0.0
