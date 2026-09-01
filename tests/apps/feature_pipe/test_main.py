import pytest
from unittest.mock import Mock, patch
from apps.feature_pipe.main import main


def test_main_check_only():
    # Mock parse_args to return check_only=True
    with patch("apps.feature_pipe.main.parse_args") as mock_parse:
        mock_args = Mock()
        mock_args.config = "test.yaml"
        mock_args.check_only = True
        mock_parse.return_value = mock_args

        with patch("apps.feature_pipe.main.load_app_config") as mock_load:
            with patch("apps.feature_pipe.main.run_feature_pipeline") as mock_run:
                main()

                mock_load.assert_called_once_with("test.yaml")
                mock_run.assert_not_called()


def test_main_run():
    with patch("apps.feature_pipe.main.parse_args") as mock_parse:
        mock_args = Mock()
        mock_args.config = "test.yaml"
        mock_args.check_only = False
        mock_parse.return_value = mock_args

        with patch("apps.feature_pipe.main.load_app_config") as mock_load:
            with patch("apps.feature_pipe.main.run_feature_pipeline") as mock_run:
                # Setup config mock
                mock_cfg = Mock()
                mock_load.return_value = mock_cfg

                main()

                mock_load.assert_called_once_with("test.yaml")
                mock_run.assert_called_once_with(mock_cfg)


def test_main_error():
    with patch("apps.feature_pipe.main.parse_args") as mock_parse:
        mock_args = Mock()
        mock_args.config = "test.yaml"
        mock_parse.return_value = mock_args

        with patch("apps.feature_pipe.main.load_app_config") as mock_load:
            mock_load.side_effect = Exception("Config error")

            with pytest.raises(SystemExit) as raised:
                main()
            assert raised.value.code == 1
