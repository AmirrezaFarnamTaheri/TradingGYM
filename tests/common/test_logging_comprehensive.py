import logging
import pytest
from trading_gym.common.logging import configure_logging


@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.setLevel(logging.NOTSET)
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)


def test_configure_logging_console(clean_logging):
    configure_logging(level="DEBUG", force=True)
    logger = logging.getLogger()

    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_configure_logging_file(clean_logging, tmp_path):
    log_file = tmp_path / "test.log"
    configure_logging(level="INFO", log_file=str(log_file), force=True)
    logger = logging.getLogger()

    assert logger.level == logging.INFO
    # Handlers: Stream + File
    assert len(logger.handlers) >= 1

    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) > 0
    assert str(log_file) in file_handlers[0].baseFilename


def test_detailed_formatting(clean_logging, capsys):
    configure_logging(level="INFO", detailed=True, force=True)
    logger = logging.getLogger()

    logger.info("Detailed message")

    # Detailed format includes function name
    captured = capsys.readouterr()
    assert (
        "[test_detailed_formatting]" in captured.err
        or "[test_detailed_formatting]" in captured.out
    )
