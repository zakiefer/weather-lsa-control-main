import logging  # noqa: F401
import os


def test_gitignore_has_logs_ignored():
    # Ensure logs and JSONL logs are ignored
    root = os.path.dirname(os.path.dirname(__file__))
    gi = os.path.join(root, ".gitignore")
    with open(gi) as f:
        content = f.read()
    assert "\nlogs/\n" in content or content.endswith("logs/\n") or content.startswith("logs/\n")
    assert "logs/*.jsonl" in content


def test_rotating_file_handler_configured():
    # Importing the CLI module sets up logging with a RotatingFileHandler
    from src import __main__ as cli  # noqa: F401

    handlers = logging.getLogger().handlers
    rfh = None
    for h in handlers:
        if h.__class__.__name__ == "RotatingFileHandler":
            rfh = h
            break
    assert rfh is not None, "RotatingFileHandler not configured on root logger"
    assert getattr(rfh, "maxBytes", None) == 1_000_000
    assert getattr(rfh, "backupCount", None) == 3
    path = getattr(rfh, "baseFilename", "")
    assert path.endswith(os.path.join("logs", "weather_monitor.log"))
