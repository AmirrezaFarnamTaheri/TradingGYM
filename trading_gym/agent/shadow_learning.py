"""Bounded CPU PPO challengers with an untouched chronological paper holdout."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import time
from pathlib import Path

import gymnasium as gym
import numpy as np
from filelock import FileLock, Timeout
from gymnasium import spaces

from trading_gym.agent.strategy_lab.overfitting import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from trading_gym.core.persistence import atomic_write_json, read_json


def observation(prices, position_fraction=0.0, equity_ratio=1.0):
    values = np.asarray(prices, dtype=float)
    returns = np.diff(values[-14:]) / values[-14:-1]
    out = np.array(
        [
            returns[-1] if len(returns) else 0,
            np.mean(returns[-3:]) if len(returns) else 0,
            np.std(returns) if len(returns) else 0,
            position_fraction,
            equity_ratio - 1,
        ],
        dtype=np.float32,
    )
    if not np.isfinite(out).all():
        raise ValueError("non-finite shadow observation")
    return np.clip(out, -10, 10)


class ShadowMarketEnv(gym.Env):
    """Training-only spot proxy; deployment is judged in the actual paper engine."""

    def __init__(self, prices, settings):
        self.prices = np.asarray(prices, dtype=float)
        if (
            len(self.prices) < 32
            or not np.isfinite(self.prices).all()
            or (self.prices <= 0).any()
        ):
            raise ValueError("complete positive training history required")
        self.settings = settings
        self.observation_space = spaces.Box(-10, 10, shape=(5,), dtype=np.float32)
        self.action_space = spaces.Box(0, 1, shape=(1,), dtype=np.float32)
        self.reset()

    def _obs(self):
        return observation(self.prices[: self.index + 1], self.exposure, self.equity)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.index, self.equity, self.exposure = 13, 1.0, 0.0
        return self._obs(), {}

    def step(self, action):
        target = float(np.clip(action[0], 0, 1)) * self.settings.max_position_fraction
        cost = (
            abs(target - self.exposure)
            * (self.settings.fee_bps + self.settings.slippage_bps)
            / 10000
        )
        realized = self.prices[self.index + 1] / self.prices[self.index] - 1
        factor = max(1e-9, 1 - cost + target * realized)
        self.equity *= factor
        self.exposure = target
        self.index += 1
        return (
            self._obs(),
            float(np.log(factor)),
            self.index >= len(self.prices) - 1,
            False,
            {},
        )


class ShadowLearner:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "learning.json"
        self.report = read_json(
            self.path,
            {
                "status": "waiting",
                "attempts": 0,
                "promotions": [],
                "last_evaluated_at": 0,
                "last_training_cutoff": 0,
            },
        )
        self.model = None
        self.symbol = self.report.get("active_symbol")
        active = self.home / "active.zip"
        if self.report.get("active_sha256"):
            if (
                not active.is_file()
                or hashlib.sha256(active.read_bytes()).hexdigest()
                != self.report["active_sha256"]
            ):
                raise ValueError("active paper policy checkpoint changed")
            from stable_baselines3 import PPO

            self.model = PPO.load(active, device="cpu")

    def train_and_evaluate(self, bars, symbol, settings):
        with FileLock(str(self.home / "training.lock"), timeout=0):
            self.report = read_json(self.path, self.report)
            return self._train_and_evaluate(bars, symbol, settings)

    def refresh(self):
        """Install only a complete published checkpoint, including after restart."""
        try:
            with FileLock(str(self.home / "training.lock"), timeout=0):
                report = read_json(self.path, self.report)
                if report.get("active_sha256") != self.report.get("active_sha256"):
                    active = self.home / "active.zip"
                    if (
                        hashlib.sha256(active.read_bytes()).hexdigest()
                        != report["active_sha256"]
                    ):
                        raise ValueError("paper checkpoint digest mismatch")
                    from stable_baselines3 import PPO

                    self.model = PPO.load(active, device="cpu")
                    self.symbol = report.get("active_symbol")
                self.report = report
        except Timeout:
            pass

    def _train_and_evaluate(self, bars, symbol, settings):
        """The final 30% is never passed to PPO.learn. One new holdout per trial."""
        from stable_baselines3 import PPO

        from trading_gym.rl.pipelines.training import _atomic_model_save

        values = [float(x[1]) for x in bars]
        cutoff = int(len(values) * 0.7)
        training = values[:cutoff]
        holdout = bars[cutoff - 14 :]
        if len(training) < 100 or len(holdout) - 14 < 60:
            raise ValueError("insufficient untouched holdout")
        if bars[cutoff - 1][0] <= self.report.get("training_cutoffs", {}).get(
            symbol, 0
        ):
            raise ValueError("a fresh chronological holdout is required")
        env = ShadowMarketEnv(training, settings)
        candidate = PPO(
            "MlpPolicy",
            env,
            device="cpu",
            n_steps=64,
            batch_size=32,
            n_epochs=3,
            learning_rate=3e-4,
            policy_kwargs={"net_arch": [32, 32]},
            seed=42,
            verbose=0,
        )
        candidate.learn(total_timesteps=settings.shadow_training_steps)

        async def evaluate(model):
            from trading_gym.agent.daemon import AutonomousTradingAgent, ClosedBar

            with tempfile.TemporaryDirectory(
                prefix="shadow-", dir=self.home
            ) as directory:
                config = settings.model_copy(
                    update={
                        "symbols": [symbol],
                        "llm_enabled": False,
                        "decision1_enabled": False,
                        "shadow_learning_enabled": False,
                        "execution_mode": "paper",
                    }
                )
                agent = AutonomousTradingAgent(
                    Path(directory),
                    config,
                    rl_model=model,
                    rl_symbol=symbol,
                    evaluation_only=True,
                )
                for stamp, price in holdout:
                    await agent.process_bar(
                        ClosedBar(
                            symbol, float(stamp), float(price), "shadow_evaluation"
                        )
                    )
                result = agent.snapshot()
                curve = np.asarray(
                    [p["equity"] for p in result["equity_curve"]][14:], dtype=float
                )
                returns = np.diff(curve) / curve[:-1]
                return result, returns

        incumbent, old_returns = asyncio.run(evaluate(self.model))
        challenger, new_returns = asyncio.run(evaluate(candidate))
        std = float(np.std(new_returns, ddof=1))
        sr = float(np.mean(new_returns) / std) if std > 1e-12 else 0.0
        old_std = float(np.std(old_returns, ddof=1))
        old_sr = float(np.mean(old_returns) / old_std) if old_std > 1e-12 else 0.0
        trials = int(self.report.get("attempts", 0)) + 1
        psr = (
            probabilistic_sharpe_ratio(new_returns, periods_per_year=365 * 24 * 12)
            if std > 1e-12
            else 0.0
        )
        dsr = (
            deflated_sharpe_ratio(sr, trials=trials, observations=len(new_returns))
            if std > 1e-12
            else 0.0
        )

        def drawdown(result):
            curve = np.asarray(
                [p["equity"] for p in result["equity_curve"]], dtype=float
            )
            return float(np.max(1 - curve / np.maximum.accumulate(curve)))

        old_dd, new_dd = drawdown(incumbent), drawdown(challenger)
        approved = (
            sr > old_sr
            and challenger["equity"] > incumbent["equity"]
            and new_dd <= old_dd
            and psr >= 0.95
            and dsr >= 0.95
            and not challenger["halted"]
        )
        result = {
            "at": time.time(),
            "symbol": symbol,
            "training_bars": len(training),
            "holdout_bars": len(holdout) - 14,
            "incumbent_equity": incumbent["equity"],
            "challenger_equity": challenger["equity"],
            "incumbent_sharpe_per_bar": old_sr,
            "challenger_sharpe_per_bar": sr,
            "incumbent_drawdown": old_dd,
            "challenger_drawdown": new_dd,
            "probabilistic_sharpe": psr,
            "deflated_sharpe": dsr,
            "promoted": approved,
            "scope": "paper_only",
            "source": "broker_closed_bars",
        }
        self.report.update(
            {
                "status": "evaluated",
                "attempts": trials,
                "last_evaluated_at": time.time(),
                "last_training_cutoff": bars[cutoff - 1][0],
                "last_result": result,
            }
        )
        self.report.setdefault("evaluations", []).append(result)
        self.report.setdefault("training_cutoffs", {})[symbol] = bars[cutoff - 1][0]
        self.report["evaluations"] = self.report["evaluations"][-30:]
        if approved:
            active = self.home / "active.zip"
            _atomic_model_save(candidate, active)
            self.report.update(
                {
                    "active_sha256": hashlib.sha256(active.read_bytes()).hexdigest(),
                    "active_symbol": symbol,
                }
            )
            self.report.setdefault("promotions", []).append(result)
            self.model, self.symbol = candidate, symbol
        atomic_write_json(self.path, self.report)
        env.close()
        return result
