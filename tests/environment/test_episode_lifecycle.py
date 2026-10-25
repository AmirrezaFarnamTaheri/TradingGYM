import pytest

from test_physical_clock import make_env
from trading_gym.execution.risk.circuit_breakers import BreakerState


def test_oversized_factory_assignment_stays_bounded():
    env = make_env(length=4)
    env.max_steps = 1000
    assert env.max_steps == 3
    env.reset(seed=8)
    for i in range(3):
        obs, reward, terminated, truncated, _ = env.step(0)
        assert obs.shape == env.observation_space.shape
        assert not terminated
        assert truncated is (i == 2)
    assert env.t == 3
    assert env.step(0)[3] is True


def test_training_reset_clears_breaker_and_live_reset_does_not():
    offline = make_env()
    offline.reset()
    offline.breaker.state = BreakerState.TRIPPED
    offline.breaker.trip_reason = "old_episode"
    offline.reset()
    assert offline.breaker.state is BreakerState.CLOSED
    assert offline.breaker.trip_reason is None
    live = make_env(live=True)
    live.reset()
    live.breaker.state = BreakerState.TRIPPED
    live.update_observation_clock(1)
    with pytest.raises(RuntimeError, match="cannot be reset"):
        live.reset()
    assert live.breaker.state is BreakerState.TRIPPED


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_bad_horizons_rejected(value):
    env = make_env()
    with pytest.raises(ValueError):
        env.max_steps = value
