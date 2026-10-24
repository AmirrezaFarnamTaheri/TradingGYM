import numpy as np
import pytest
from trading_gym.intelligence.residual_experiment import (
    compose_residual,
    ResidualPolicy,
    residual_training_environment,
    compare_residual_candidates,
)
from test_shadow_specialists_patch import make_env, frame

CONTRACT = dict(residual_bound=0.2, action_low=-5.0, action_high=20.0)


def test_residual_bounds_and_no_implicit_direction_or_flat_permission():
    out = compose_residual(1.0, 50.0, **CONTRACT)
    assert out.final_target == 1.2 and out.applied_residual == pytest.approx(0.2)
    assert compose_residual(0.0, 1.0, **CONTRACT).final_target == 0
    assert compose_residual(0.1, -1.0, **CONTRACT).final_target == 0
    with pytest.raises(ValueError):
        compose_residual(1.0, float("nan"), **CONTRACT)


def test_training_and_inference_use_same_composition():
    wrapped = residual_training_environment(
        make_env(), lambda obs: 1.0, residual_bound=0.2
    )
    obs, _ = wrapped.reset(seed=42)
    predicted = ResidualPolicy(lambda obs: 1.0, lambda obs: 0.1, **CONTRACT).predict(
        obs
    )[0]
    _, _, _, _, info = wrapped.step(np.array([0.1]))
    assert float(predicted[0]) == pytest.approx(
        info["residual_composition"]["final_target"]
    )
    assert info["fee_cost"] > 0


def test_all_four_comparators_have_independent_economic_trajectories():
    report = compare_residual_candidates(
        baseline_factory=lambda: lambda obs: 1.0,
        rl_factory=lambda: lambda obs: 0.5,
        residual_factory=lambda: lambda obs: 0.1,
        bundle_ids={"baseline": "b", "rl": "r", "residual": "d"},
        composition=CONTRACT,
        env_factory=make_env,
        market_frame=frame,
        steps=4,
        horizon_steps=2,
    )
    first = {
        s.source_id: s.target_leverage
        for s in report.steps
        if s.decision_id.endswith(":0")
    }
    assert first == pytest.approx(
        {"baseline": 1, "rl-only": 0.5, "fixed-average": 0.75, "residual": 1.1}
    )


def test_real_cpu_ppo_can_train_delta_policy_without_account_reimplementation():
    from stable_baselines3 import PPO

    env = residual_training_environment(make_env(), lambda obs: 1.0, residual_bound=0.2)
    model = PPO(
        "MlpPolicy",
        env,
        n_steps=4,
        batch_size=4,
        n_epochs=1,
        seed=7,
        device="cpu",
        verbose=0,
    )
    model.learn(total_timesteps=8)
    obs, _ = env.reset(seed=7)
    action = ResidualPolicy(lambda obs: 1.0, model, **CONTRACT).predict(obs)[0]
    assert 0.8 <= float(action[0]) <= 1.2
