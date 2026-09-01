import sys
import types
import importlib.util
from pathlib import Path
import pytest
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub heavy optional dependencies (torch / stable_baselines3) ONLY IF NOT INSTALLED
# This avoids loading GPU DLLs on constrained environments but allows real tests if packages exist.

if "torch" not in sys.modules:
    if importlib.util.find_spec("torch") is None:
        torch_module = types.ModuleType("torch")
        cuda_module = types.ModuleType("torch.cuda")
        amp_module = types.ModuleType("torch.cuda.amp")

        class _GradScaler:
            def __init__(self, enabled: bool = False) -> None:
                self.enabled = enabled

            def scale(self, loss):
                return loss

            def step(self, optimizer):
                if optimizer is not None:
                    optimizer.step()

            def update(self) -> None:
                return None

            def unscale_(self, optimizer) -> None:
                return None

        class _AutocastCtx:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        def _autocast(*args, **kwargs):
            return _AutocastCtx()

        amp_module.GradScaler = _GradScaler  # type: ignore
        amp_module.autocast = _autocast  # type: ignore

        cuda_module.is_available = lambda: False  # type: ignore
        cuda_module.amp = amp_module  # type: ignore

        torch_module.cuda = cuda_module  # type: ignore
        torch_module.nn = types.SimpleNamespace(  # type: ignore
            utils=types.SimpleNamespace(clip_grad_norm_=lambda *a, **k: None)
        )

        sys.modules["torch"] = torch_module
        sys.modules["torch.cuda"] = cuda_module
        sys.modules["torch.cuda.amp"] = amp_module
    else:
        import torch  # noqa: F401


if "stable_baselines3" not in sys.modules:
    if importlib.util.find_spec("stable_baselines3") is None:
        sb3_module = types.ModuleType("stable_baselines3")
        common_module = types.ModuleType("stable_baselines3.common")
        callbacks_module = types.ModuleType("stable_baselines3.common.callbacks")
        vec_env_module = types.ModuleType("stable_baselines3.common.vec_env")
        utils_module = types.ModuleType("stable_baselines3.common.utils")
        evaluation_module = types.ModuleType("stable_baselines3.common.evaluation")
        base_class_module = types.ModuleType("stable_baselines3.common.base_class")

        class _BaseCallback:
            def __init__(self, verbose: int = 0) -> None:
                self.verbose = verbose

            def _on_step(self) -> bool:
                return True

        class _EvalCallback(_BaseCallback):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__()
                self.best_mean_reward = 0.0
                self.last_mean_reward = float("nan")

            def __call__(self, *args, **kwargs):
                return True

        class _DummyVecEnv:
            def __init__(self, env_fns):
                self.envs = [fn() for fn in env_fns]

            def close(self) -> None:
                for env in self.envs:
                    close = getattr(env, "close", None)
                    if callable(close):
                        close()

        class _BaseAlgorithm:
            def predict(self, observation, deterministic=True):
                return [0.0], {}

            @classmethod
            def load(cls, *args, **kwargs):
                return cls()

        def _explained_variance(*args, **kwargs) -> float:
            return 0.0

        def _evaluate_policy(*args, **kwargs):
            raise RuntimeError("evaluate_policy is not available in the test stub.")

        class _StubPPO(_BaseAlgorithm):
            def __init__(self, *args, **kwargs) -> None:
                pass

        callbacks_module.BaseCallback = _BaseCallback  # type: ignore
        callbacks_module.EvalCallback = _EvalCallback  # type: ignore
        vec_env_module.DummyVecEnv = _DummyVecEnv  # type: ignore
        utils_module.explained_variance = _explained_variance  # type: ignore
        evaluation_module.evaluate_policy = _evaluate_policy  # type: ignore
        base_class_module.BaseAlgorithm = _BaseAlgorithm  # type: ignore
        sb3_module.PPO = _StubPPO  # type: ignore

        sys.modules["stable_baselines3"] = sb3_module
        sys.modules["stable_baselines3.common"] = common_module
        sys.modules["stable_baselines3.common.callbacks"] = callbacks_module
        sys.modules["stable_baselines3.common.vec_env"] = vec_env_module
        sys.modules["stable_baselines3.common.utils"] = utils_module
        sys.modules["stable_baselines3.common.evaluation"] = evaluation_module
        sys.modules["stable_baselines3.common.base_class"] = base_class_module
    else:
        import stable_baselines3  # noqa: F401

if "typer" not in sys.modules:
    if importlib.util.find_spec("typer") is None:
        typer_module = types.ModuleType("typer")

        class _Typer:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def command(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def __call__(self, *args, **kwargs):
                return None

        class BadParameter(Exception):
            pass

        def Option(default=None, *args, **kwargs):
            return default

        def echo(message: str) -> None:
            print(message)

        typer_module.Typer = _Typer  # type: ignore
        typer_module.Option = Option  # type: ignore
        typer_module.BadParameter = BadParameter  # type: ignore
        typer_module.echo = echo  # type: ignore

        sys.modules["typer"] = typer_module
    else:
        import typer  # noqa: F401


@pytest.fixture(scope="session")
def app_config():
    """Load the project configuration once for the test suite."""
    config_module = pytest.importorskip(
        "trading_gym.common.config",
        reason="Trading Gym config helpers are required for integration tests",
    )
    return config_module.load_app_config(os.path.abspath("config/app.yaml"))


@pytest.fixture(scope="session")
def synthetic_data() -> dict:
    """Generate a reproducible synthetic price series for deterministic testing."""
    np = pytest.importorskip(
        "numpy", reason="numpy is required for synthetic market data fixtures"
    )
    pd = pytest.importorskip(
        "pandas", reason="pandas is required for synthetic market data fixtures"
    )
    rng = np.random.default_rng(seed=42)
    num_bars = 200
    dt = 1 / 288  # 5-minute bars in day fraction
    mu = 0.02
    sigma = 0.15

    prices = np.empty(num_bars, dtype=float)
    prices[0] = 40_000.0
    shocks = rng.normal(
        loc=(mu - 0.5 * sigma**2) * dt, scale=sigma * np.sqrt(dt), size=num_bars - 1
    )
    prices[1:] = prices[0] * np.exp(np.cumsum(shocks))

    index = pd.date_range("2024-01-01", periods=num_bars, freq="5min", tz="UTC")
    base = pd.DataFrame(
        {
            "BTCUSDT_close": prices,
            "BTCUSDT_mid": prices,
            "BTCUSDT_spread_bps": np.full(num_bars, 10.0),
            "BTCUSDT_buy_max": np.full(num_bars, 200_000.0),
            "BTCUSDT_sell_max": np.full(num_bars, 200_000.0),
            "BTCUSDT_volatility": np.full(num_bars, 0.01),
            "is_missing": np.zeros(num_bars, dtype=int),
        },
        index=index,
    )

    return {"5m": base}


@pytest.fixture
def trading_env(app_config, synthetic_data: dict):
    """Provision a deterministic environment for behavioural tests."""
    env_module = pytest.importorskip(
        "trading_gym.rl.margin_trading_environment",
        reason="MarginTradingEnv is required for RL environment tests",
    )
    frame = synthetic_data["5m"]
    cfg = app_config.env
    cfg.leverage.max_leverage = 2.0  # Use a more conservative leverage for testing

    # Create a dummy features dataframe
    pd = pytest.importorskip("pandas")
    features = pd.DataFrame(
        {
            "returns": [0.0] * len(frame),
            "vol20": [0.0] * len(frame),
            "momentum": [0.0] * len(frame),
            "spread_ratio": frame["BTCUSDT_spread_bps"].to_numpy() / 10000,
            "spread_velocity": [0.0] * len(frame),
            "capacity_util": [0.0] * len(frame),
            "vol_hint": frame["BTCUSDT_volatility"].to_numpy(),
            "missing_flag": frame["is_missing"].to_numpy(),
        }
    )

    return env_module.MarginTradingEnv(
        features=features,
        prices_mid=frame["BTCUSDT_mid"].to_numpy(),
        prices_high=frame["BTCUSDT_mid"].to_numpy(),  # Use mid for high/low in tests
        prices_low=frame["BTCUSDT_mid"].to_numpy(),
        cfg=cfg,
        initial_equity=app_config.training.initial_equity,
    )
