import os
from unittest.mock import patch, MagicMock
from pathlib import Path
from apps.api.main import prune_zombies


def test_prune_zombies():
    my_pid = os.getpid()

    # Mock psutil.process_iter
    p1 = MagicMock()
    p1.info = {"pid": 1234, "cmdline": ["python", "-m", "apps.live.main"]}
    p1.terminate = MagicMock()

    p2 = MagicMock()
    p2.info = {
        "pid": my_pid,
        "cmdline": ["python", "-m", "apps.api.main"],
    }  # Should ignore self
    p2.terminate = MagicMock()

    p3 = MagicMock()
    p3.info = {"pid": 5678, "cmdline": ["bash", "random_script.sh"]}  # Should ignore
    p3.terminate = MagicMock()

    # Mock psutil.Process to always return True for cwd check (scoped_to_repo)
    # We mock the constructor psutil.Process(pid) -> mock_proc
    # mock_proc.cwd() -> returns a path that starts with PROJECT_ROOT
    with patch("psutil.process_iter", return_value=[p1, p2, p3]), patch(
        "apps.api.main.psutil.Process"
    ) as MockProcess, patch("apps.api.main.PROJECT_ROOT", new=Path(os.getcwd())), patch(
        "apps.api.main.capture_process_identity",
        return_value={"pid": 1234, "create_time": 1.0, "command_sha256": "a" * 64},
    ), patch(
        "apps.api.main.terminate_process_tree",
        return_value={"terminated": 1, "remaining": 0},
    ) as terminate:

        mock_proc_instance = MockProcess.return_value
        mock_proc_instance.cwd.return_value = os.getcwd()

        prune_zombies()

        # The verified identity is terminated through the process-tree guard,
        # not through the untrusted process_iter object.
        terminate.assert_called_once()
        p1.terminate.assert_not_called()

        # p2 (self) should NOT be terminated
        p2.terminate.assert_not_called()

        # p3 (random) should NOT be terminated
        p3.terminate.assert_not_called()
