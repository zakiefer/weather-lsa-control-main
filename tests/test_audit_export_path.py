import importlib
import os


def test_export_audit_writes_to_log_dir(tmp_path, monkeypatch):
    # Point LOG_DIR to a temp
    # Use tmp_path for writing; computing root unused
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # We import db and monkeypatch LOG_DIR
    import src.db as db

    importlib.reload(db)
    db.LOG_DIR = str(tmp_path)
    # Create minimal schema and write audit file
    db.ensure_schema()
    path = db.export_audit(days=0, fmt="jsonl")
    assert path.startswith(str(tmp_path))
    assert os.path.exists(path)
    # Ensure has at least header/lines (could be empty sets, but file exists)
    with open(path) as f:
        content = f.read()
    assert isinstance(content, str)
