from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from trading_gym.rl.trainer.update import update


@pytest.fixture
def mock_mlflow_client():
    """Fixture for a mocked MLflow client."""
    return MagicMock()


@pytest.fixture
def mock_load_model():
    """Fixture for a mocked model loader."""
    return MagicMock()


@patch("trading_gym.rl.trainer.update.load_app_config")
@patch("trading_gym.rl.trainer.update.run_walkforward")
def test_update_challenger_promoted(
    mock_run_walkforward, mock_load_app_config, mock_mlflow_client, mock_load_model
):
    """
    Tests that the challenger is promoted when its Sharpe ratio is higher.
    """
    # Arrange
    champion_results = SimpleNamespace(sharpe_ratio=1.5, evaluation_folds=5)
    challenger_results = SimpleNamespace(
        sharpe_ratio=2.0,
        evaluation_folds=5,
        model_name="test_model",
        model_version="1",
    )
    mock_run_walkforward.side_effect = [champion_results, challenger_results]

    # Act
    update(
        config_path="config/app.yaml",
        champion_run_id="champion",
        challenger_run_id="challenger",
        client=mock_mlflow_client,
        _mlflow_load_model=mock_load_model,
    )

    # Assert
    mock_mlflow_client.transition_model_version_stage.assert_called_once_with(
        name="test_model", version="1", stage="Staging"
    )


@patch("trading_gym.rl.trainer.update.load_app_config")
@patch("trading_gym.rl.trainer.update.run_walkforward")
def test_update_champion_retained(
    mock_run_walkforward, mock_load_app_config, mock_mlflow_client, mock_load_model
):
    """
    Tests that the champion is retained when its Sharpe ratio is higher.
    """
    # Arrange
    champion_results = SimpleNamespace(sharpe_ratio=2.0, evaluation_folds=5)
    challenger_results = SimpleNamespace(sharpe_ratio=1.5, evaluation_folds=5)
    mock_run_walkforward.side_effect = [champion_results, challenger_results]

    # Act
    update(
        config_path="config/app.yaml",
        champion_run_id="champion",
        challenger_run_id="challenger",
        client=mock_mlflow_client,
        _mlflow_load_model=mock_load_model,
    )

    # Assert
    mock_mlflow_client.transition_model_version_stage.assert_not_called()
