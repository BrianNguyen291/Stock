"""Manual Azure test-slot acceptance, using the workflow's existing Azure login.

Kudu command API: https://github.com/projectkudu/kudu/wiki/REST-API#command
Only the test slot is written/restarted; production probes are read-only.
Credentials remain in memory and are never included in the evidence artifact.
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zlib


REMOTE = r'''
import hashlib, json, os, sqlite3
from pathlib import Path

home = Path(os.path.abspath(os.environ["HOME"]))
root = Path.cwd()
environment_slot = os.environ.get("WEBSITE_SLOT_NAME")
if environment_slot:
    assert environment_slot.lower() == expected_slot, "Unexpected slot: " + environment_slot
# The authenticated SCM endpoint was selected from the ARM slot resource.
# Windows SCM processes do not always expose WEBSITE_SLOT_NAME.
slot_name = expected_slot
configured = os.environ.get("STOCK_DB_PATH")
path = Path(configured) if configured else home / "data" / "stock" / (slot_name + ".sqlite3")
assert path.is_absolute(), "Database path must be absolute"
physical = path.resolve()
assert physical.is_relative_to(home.resolve()) and not physical.is_relative_to(root.resolve()), "Database must be on HOME storage outside wwwroot"
# Keep the stable HOME drive alias instead of storing a resolved Azure UNC/IP.
# SQLite read-only file URIs also require a local (empty-authority) path.
path = home / physical.relative_to(home.resolve())

if expected_slot == "test":
    from database import StockDatabase, get_database_path
    import pandas as pd
    if mode == "configure":
        target = home / "data" / "stock" / "test.sqlite3"
        previous = home / get_database_path().resolve().relative_to(home.resolve())
        migrated = False
        if previous != target and previous.is_file():
            assert not target.exists(), "Both old and new databases exist; refusing to overwrite either"
            target.parent.mkdir(parents=True, exist_ok=True)
            source = sqlite3.connect(previous.as_uri() + "?mode=ro", uri=True)
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
                migrated = True
            finally:
                destination.close()
                source.close()
        print(json.dumps({"target_path": str(target), "previous_path": str(previous), "migrated": migrated}))
        raise SystemExit(0)
    assert get_database_path().resolve() == path.resolve()
    if mode == "prepare":
        database = StockDatabase()
        for filename in ("2327.csv", "2492.csv"):
            database.import_csv(root / filename, only_if_empty=True)
        existing = database.query_prices(marker_id, indicators=False)
        assert existing.empty, "Acceptance marker already exists"
        database.upsert_prices(pd.DataFrame([{
            "stock_id": marker_id, "stock_name": "Persistence acceptance marker",
            "date": "1900-01-01", "open": 12, "high": 15, "low": 10,
            "close": 13, "trade_volume": 1234567, "note": marker_note,
        }]), source=marker_note)

result = {"slot": slot_name, "path": str(path), "home": str(home),
          "deployment_root": str(root), "configured_path": configured,
          "database_exists": path.is_file()}
if path.is_file():
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "daily_prices" in tables:
            rows = connection.execute("SELECT * FROM daily_prices ORDER BY stock_id, date").fetchall()
            stocks = connection.execute("SELECT * FROM stocks ORDER BY stock_id").fetchall()
            result["sha256"] = hashlib.sha256(json.dumps([rows, stocks], ensure_ascii=True).encode()).hexdigest()
            result["row_count"] = len(rows)
            result["stocks"] = connection.execute("SELECT stock_id, COUNT(*), MIN(date), MAX(date) FROM daily_prices GROUP BY stock_id ORDER BY stock_id").fetchall()
            marker = connection.execute("SELECT date, open, high, low, close, trade_volume, note FROM daily_prices WHERE stock_id=?", (marker_id,)).fetchall()
            result["marker"] = marker
        else:
            result["marker"] = []
    finally:
        connection.close()
else:
    result["marker"] = []

if expected_slot == "test":
    assert result["marker"] == [("1900-01-01", 12.0, 15.0, 10.0, 13.0, 1234567, marker_note)], "Marker missing or changed"
else:
    assert not result["marker"], "Test marker leaked into production"

if mode == "cleanup":
    assert expected_slot == "test"
    database = StockDatabase()
    with database.connect() as connection:
        # Also clean this tool's identical markers left by an interrupted run.
        # Match the full test signature; never remove ordinary stock records.
        markers = connection.execute("""
            SELECT p.stock_id, p.note FROM daily_prices p JOIN stocks s USING(stock_id)
            WHERE s.stock_name='Persistence acceptance marker' AND p.stock_id LIKE 'Z%'
            AND p.date='1900-01-01' AND p.open=12 AND p.high=15 AND p.low=10
            AND p.close=13 AND p.trade_volume=1234567 AND p.note LIKE 'issue-9-acceptance:%'
            AND (SELECT COUNT(*) FROM daily_prices d WHERE d.stock_id=p.stock_id)=1
        """).fetchall()
        for identifier, note in markers:
            connection.execute("DELETE FROM daily_prices WHERE stock_id=? AND note=?", (identifier, note))
            connection.execute("DELETE FROM stocks WHERE stock_id=? AND NOT EXISTS (SELECT 1 FROM daily_prices WHERE stock_id=?)", (identifier, identifier))
            connection.execute("DELETE FROM update_logs WHERE source=?", (note,))
    result["marker_cleaned"] = True
    result["cleaned_marker_ids"] = [row[0] for row in markers]

print(json.dumps(result, ensure_ascii=True))
'''


def azure(*arguments):
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI is required")
    result = subprocess.run([executable, *arguments, "--output", "json", "--only-show-errors"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        # Do not echo stdout: publishing-credential commands return secrets there.
        raise RuntimeError("Azure CLI operation failed: " + " ".join(arguments[:3]))
    return json.loads(result.stdout) if result.stdout.strip() else None


class AzureAcceptance:
    def __init__(self):
        self.name = os.environ["AZURE_WEBAPP_NAME"]
        self.group = os.environ["AZURE_RESOURCE_GROUP"]
        self.clients = {}

    def target(self, slot):
        args = ["--resource-group", self.group, "--name", self.name]
        if slot == "test":
            args += ["--slot", "test"]
        return args

    def probe(self, slot, mode, baseline):
        if slot not in self.clients:
            target = self.target(slot)
            info = azure("webapp", "show", *target)
            scm = next(host for host in info["enabledHostNames"] if ".scm." in host)
            credentials = azure("webapp", "deployment", "list-publishing-credentials", *target)
            pair = credentials["publishingUserName"] + ":" + credentials["publishingPassword"]
            authorization = "Basic " + base64.b64encode(pair.encode()).decode()
            # Mask all forms before any HTTP requests are made.
            if os.environ.get("GITHUB_ACTIONS"):
                for secret in (credentials["publishingUserName"], credentials["publishingPassword"], authorization):
                    print("::add-mask::" + secret)
            self.clients[slot] = ("https://" + scm + "/api/command", authorization)
        endpoint, authorization = self.clients[slot]
        constants = "\n".join(f"{key} = {value!r}" for key, value in {
            "expected_slot": slot, "mode": mode,
            "marker_id": baseline["marker_id"], "marker_note": baseline["marker_note"],
        }.items())
        payload = base64.b64encode(zlib.compress((constants + "\n" + REMOTE).encode())).decode()
        command = 'runtime\\python.exe -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\'' + payload + '\')))"'
        body = json.dumps({"command": command, "dir": "site\\wwwroot"}).encode()
        request = urllib.request.Request(endpoint, data=body, headers={
            "Authorization": authorization, "Content-Type": "application/json",
        })
        # No automatic POST retry: preparation writes a unique marker once.
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
        if result["ExitCode"] != 0:
            raise RuntimeError(f"{slot} {mode} failed: " + result.get("Error", ""))
        return json.loads(result["Output"].strip())

    def wait_healthy(self):
        info = azure("webapp", "show", *self.target("test"))
        endpoint = "https://" + info["defaultHostName"] + "/_stcore/health"
        for _ in range(60):
            try:
                with urllib.request.urlopen(endpoint, timeout=15) as response:
                    if response.status == 200 and response.read().strip() == b"ok":
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(5)
        raise RuntimeError("Test slot did not become healthy")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "restart", "redeploy"), required=True)
    parser.add_argument("--evidence", type=Path, default=Path("persistence-evidence"))
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    baseline_path = args.evidence / "baseline.json"
    client = AzureAcceptance()
    if args.phase == "prepare":
        if baseline_path.exists():
            raise RuntimeError("Use a new evidence directory for each acceptance run")
        baseline = {"marker_id": "Z" + uuid.uuid4().hex[:9].upper(),
                    "marker_note": "issue-9-acceptance:" + uuid.uuid4().hex,
                    "commit": os.environ.get("GITHUB_SHA"),
                    "run_id": os.environ.get("GITHUB_RUN_ID")}
        # Persist marker identity before remote writes for traceability on failure.
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        baseline["production"] = client.probe("production", "inspect", baseline)
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        configuration = client.probe("test", "configure", baseline)
        baseline["configuration"] = configuration
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        azure("webapp", "config", "appsettings", "set", *client.target("test"),
              "--slot-settings", "STOCK_DB_PATH=" + configuration["target_path"])
        client.wait_healthy()
        baseline["test"] = client.probe("test", "prepare", baseline)
        if baseline["production"]["path"].lower() == baseline["test"]["path"].lower():
            raise RuntimeError("Production and test paths must differ")
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        report = baseline
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if args.phase == "restart":
            azure("webapp", "restart", *client.target("test"))
        client.wait_healthy()
        report = {"test": client.probe("test", "inspect", baseline),
                  "production": client.probe("production", "inspect", baseline)}
        for slot in ("test", "production"):
            if report[slot] != baseline[slot]:
                raise RuntimeError(f"{slot} data or configuration changed after {args.phase}")
        if args.phase == "redeploy":
            report["cleanup"] = client.probe("test", "cleanup", baseline)
        report["passed"] = True
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    (args.evidence / f"{args.phase}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "passed": True, "test_path": report["test"]["path"],
                      "test_row_count": report["test"]["row_count"],
                      "production_path": report["production"]["path"]}))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(f"\n- Persistence acceptance `{args.phase}` passed: {report['test']['row_count']} rows; marker and SHA-256 match.\n")


if __name__ == "__main__":
    main()
