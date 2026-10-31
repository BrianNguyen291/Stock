"""Local safeguards for the commands used by the real Azure acceptance run."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil

import pytest

from database import StockDatabase
from scripts.verify_azure_persistence import REMOTE

ROOT = Path(__file__).resolve().parent.parent
MARKER_ID = "Z123456789"
MARKER_NOTE = "issue-9-acceptance:local-test"


@pytest.fixture
def deployed_home(tmp_path, monkeypatch):
    root = tmp_path / "site" / "wwwroot"
    root.mkdir(parents=True)
    for name in ("2327.csv", "2492.csv"):
        shutil.copy(ROOT / name, root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WEBSITE_SITE_NAME", "stock")
    monkeypatch.setenv("WEBSITE_SLOT_NAME", "test")
    monkeypatch.delenv("STOCK_DB_PATH", raising=False)
    return tmp_path


def probe(slot, mode):
    namespace = {"expected_slot": slot, "mode": mode,
                 "marker_id": MARKER_ID, "marker_note": MARKER_NOTE}
    output = io.StringIO()
    with redirect_stdout(output):
        try:
            exec(REMOTE, namespace)
        except SystemExit as exc:
            assert exc.code == 0
    return json.loads(output.getvalue())


def test_marker_and_history_survive_probe_then_only_marker_is_cleaned(deployed_home):
    before = probe("test", "prepare")
    assert before["marker"][0][-1] == MARKER_NOTE
    assert probe("test", "inspect") == before
    assert probe("test", "cleanup")["marker_cleaned"]
    db = StockDatabase()
    assert db.query_prices(MARKER_ID).empty
    for stock_id in ("2327", "2492"):
        assert len(db.query_prices(stock_id)) == 193
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM update_logs WHERE source=?", (MARKER_NOTE,)).fetchone()[0] == 0


def test_production_probe_does_not_create_a_database(deployed_home, monkeypatch):
    monkeypatch.setenv("WEBSITE_SLOT_NAME", "production")
    result = probe("production", "inspect")
    assert not result["database_exists"]
    assert not Path(result["path"]).exists()


def test_refuses_wrong_slot_or_database_under_deployment(deployed_home, monkeypatch):
    with pytest.raises(AssertionError, match="Unexpected slot"):
        probe("production", "inspect")
    monkeypatch.setenv("STOCK_DB_PATH", str(Path.cwd() / "wrong.sqlite3"))
    with pytest.raises(AssertionError, match="outside wwwroot"):
        probe("test", "prepare")
    assert not (Path.cwd() / "wrong.sqlite3").exists()


def test_missing_or_changed_marker_fails_verification(deployed_home):
    probe("test", "prepare")
    db = StockDatabase()
    with db.connect() as connection:
        connection.execute("UPDATE daily_prices SET note='changed' WHERE stock_id=?", (MARKER_ID,))
    with pytest.raises(AssertionError, match="Marker missing or changed"):
        probe("test", "inspect")


def test_configure_migrates_history_when_windows_omits_slot_name(deployed_home, monkeypatch):
    monkeypatch.delenv("WEBSITE_SLOT_NAME")
    original = StockDatabase()
    original.import_csv(ROOT / "2327.csv")
    configuration = probe("test", "configure")
    assert configuration["migrated"]
    assert Path(configuration["target_path"]).name == "test.sqlite3"
    migrated = StockDatabase(configuration["target_path"])
    assert len(migrated.query_prices("2327")) == 193
    assert len(original.query_prices("2327")) == 193
