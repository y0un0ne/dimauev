from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from schemas import EvaluateRequest, ExtractRequest
from credential_store import CredentialStore
from runtime_health import dependency_health
import app as app_module
from worker_manager import WorkerManager, scope_label


def test_scope_validation() -> None:
    single = ExtractRequest(date="2026-09-10")
    assert single.date.isoformat() == "2026-09-10"
    ranged = EvaluateRequest(start_date="2026-09-01", end_date="2026-09-10", cloud_data_consent=True)
    assert ranged.end_date.isoformat() == "2026-09-10"


def test_command_arrays_never_use_shell_strings() -> None:
    manager = WorkerManager()
    command, status = manager._command("evaluate", {
        "date": "2026-09-10",
        "mode": "retry_errors",
        "delay": 4.2,
        "max_images": 12,
        "thinking_level": "low",
    })
    assert isinstance(command, list)
    assert "--evaluate-only" in command
    assert "--retry-errors" in command
    assert status.name == "evaluate_2026-09-10.json"


def test_extract_command_has_control_file() -> None:
    manager = WorkerManager()
    control = Path(tempfile.gettempdir()) / "dimensions-control-test.json"
    command, _ = manager._command("extract", {
        "date": "2026-09-10",
        "min_detail_coverage": 0.9,
        "max_missing_details": 25,
    }, control)
    assert "--extract-control-file" in command
    assert str(control) in command


def test_extract_scan_modes_are_exclusive_commands() -> None:
    manager = WorkerManager()
    base = {
        "date": "2026-09-10",
        "min_detail_coverage": 0.9,
        "max_missing_details": 25,
    }
    rescan, _ = manager._command("extract", {**base, "scan_mode": "rescan"})
    retry, _ = manager._command("extract", {**base, "scan_mode": "retry_missing"})
    normal, _ = manager._command("extract", {**base, "scan_mode": "normal"})
    assert "--rescan" in rescan and "--retry-missing" not in rescan
    assert "--retry-missing" in retry and "--rescan" not in retry
    assert "--retry-missing" not in normal and "--rescan" not in normal


def test_continue_worker_signals_only_current_wait_phase() -> None:
    from worker_manager import Worker

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control = root / "control.json"
        control.write_text(json.dumps({
            "phase": "waiting_login", "prompt": "ready?", "signal": None,
        }), encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        manager = WorkerManager()
        worker = Worker(
            id="test-worker", kind="extract", scope="2026-09-10", command=[],
            log_path=root / "log.txt", status_path=root / "status.json",
            started_at=datetime.now(timezone.utc).isoformat(), process=process,
            control_path=control,
        )
        manager._workers[worker.id] = worker
        try:
            result = manager.continue_worker(worker.id)
            assert result["control"]["signal"] == "waiting_login"
            state = json.loads(control.read_text(encoding="utf-8"))
            assert state["signal"] == "waiting_login"
            state["phase"] = "running"
            control.write_text(json.dumps(state), encoding="utf-8")
            try:
                manager.continue_worker(worker.id)
            except ValueError:
                pass
            else:
                raise AssertionError("running phase must reject continue signal")
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_evaluate_command_is_unbuffered_and_tracks_progress() -> None:
    manager = WorkerManager()
    progress = Path(tempfile.gettempdir()) / "dimensions-progress-test.json"
    command, _ = manager._command("evaluate", {
        "date": "2026-09-10",
        "mode": "normal",
        "delay": 4.2,
        "limit": 1,
        "request_timeout": 45,
        "max_images": 12,
        "thinking_level": "low",
    }, progress_path=progress)
    assert command[1] == "-u"
    assert "--limit" in command and command[command.index("--limit") + 1] == "1"
    assert "--evaluation-progress-file" in command
    assert str(progress) in command
    assert command[command.index("--request-timeout") + 1] == "45"


def test_frozen_worker_dispatch_uses_same_executable() -> None:
    import worker_manager as manager_module

    original = manager_module.is_frozen
    manager_module.is_frozen = lambda: True
    try:
        manager = WorkerManager()
        command, _ = manager._command("evaluate", {
            "date": "2026-09-10", "mode": "normal", "delay": 0,
            "limit": 1, "request_timeout": 30, "max_images": 1,
            "thinking_level": "low",
        })
        assert command[:3] == [sys.executable, "--worker", "pipeline"]
        assert "dimensions_pipeline.py" not in command
    finally:
        manager_module.is_frozen = original


def test_scope_labels() -> None:
    assert scope_label({"date": "2026-09-10"}) == "2026-09-10"
    assert scope_label({"start_date": "2026-09-01", "end_date": "2026-09-10"}) == "2026-09-01_to_2026-09-10"


def test_evaluation_requires_cloud_data_consent() -> None:
    from pydantic import ValidationError

    try:
        EvaluateRequest(date="2026-09-10")
    except ValidationError:
        pass
    else:
        raise AssertionError("evaluation must require explicit cloud data consent")


def test_security_headers_and_cross_origin_mutation_guard() -> None:
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        page = client.get("/api/health")
        assert page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        blocked = client.post("/api/shutdown", headers={"Origin": "https://attacker.example"})
        assert blocked.status_code == 403


def test_windows_credential_round_trip() -> None:
    store = CredentialStore()
    name = "OPENROUTER_API_KEY"
    previous = store.get(name)
    try:
        store.set(name, "frontend-disposable-test-secret")
        assert store.get(name) == "frontend-disposable-test-secret"
        assert store.configured()[name] is True
        assert store.environment()[name] == "frontend-disposable-test-secret"
        assert store.delete(name) is True
        assert store.get(name) is None
    finally:
        if previous:
            store.set(name, previous)
        else:
            store.delete(name)


def test_configured_status_never_returns_values() -> None:
    status = CredentialStore().configured()
    assert status
    assert all(isinstance(value, bool) for value in status.values())


def test_extractor_runtime_resolves_jakarta_timezone() -> None:
    health = dependency_health()
    assert health["extract"]["ready"] is True
    assert health["extract"]["missing"] == []


def test_report_readiness_for_fresh_machine() -> None:
    old_root = app_module.OUTPUT_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        app_module.OUTPUT_ROOT = Path(tmp) / "dimensions_output"
        try:
            readiness = app_module.report_readiness("2026-09")
            assert readiness["ready"] is False
            assert readiness["source_date_folders"] == 0
            assert "MVP 1" in readiness["reason"]
        finally:
            app_module.OUTPUT_ROOT = old_root


def test_complete_date_normal_scan_is_rejected_before_worker_start() -> None:
    from fastapi import HTTPException
    from schemas import ExtractRequest

    old_root = app_module.OUTPUT_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        day = root / "2026-09-10"
        day.mkdir()
        (day / "summary.json").write_text(json.dumps({
            "pagination_complete_all": True, "final_has_more_all": False,
            "errors_in_db": 0, "click_failures": 0,
            "active_snapshot_count": 1, "active_details_fetched": 1,
            "missing_from_active_snapshot": 0, "acceptance_status": "complete",
        }), encoding="utf-8")
        app_module.OUTPUT_ROOT = root
        try:
            try:
                app_module.extract(ExtractRequest(date="2026-09-10", scan_mode="normal"))
            except HTTPException as error:
                assert error.status_code == 409
                assert "Choose Rescan" in error.detail
            else:
                raise AssertionError("complete date normal scan must be rejected")
        finally:
            app_module.OUTPUT_ROOT = old_root


def test_hide_restore_history_never_deletes_status_file() -> None:
    import worker_manager as manager_module

    old_history = manager_module.HISTORY_FILE
    old_status = manager_module.STATUS_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager_module.HISTORY_FILE = root / "worker_history.json"
        manager_module.STATUS_ROOT = root / "pipeline_status"
        manager_module.STATUS_ROOT.mkdir()
        status_a = manager_module.STATUS_ROOT / "evaluate_2026-09-06.json"
        status_b = manager_module.STATUS_ROOT / "report_2026-09.json"
        for path in (status_a, status_b):
            path.write_text(json.dumps({"started_at_utc": "2026-09-01T00:00:00+00:00"}), encoding="utf-8")
        try:
            manager = WorkerManager()
            before = manager.list()
            assert len(before) == 2
            old_key = "history-v2:evaluate:2026-09-06:2026-09-01T00:00:00+00:00"
            manager.hide_history(old_key)
            assert status_a.exists()
            visible = manager.list()
            assert [item["id"] for item in visible] == [
                "history-v2:report:2026-09:2026-09-01T00:00:00+00:00"
            ]
            assert manager.hidden_history() == [old_key]
            assert manager.restore_history() == 1
            assert len(manager.list()) == 2
        finally:
            manager_module.HISTORY_FILE = old_history
            manager_module.STATUS_ROOT = old_status


def test_running_worker_cannot_be_hidden() -> None:
    import worker_manager as manager_module
    from worker_manager import Worker

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        manager = WorkerManager()
        worker = Worker(
            id="running-worker", kind="evaluate", scope="2026-09-06", command=[],
            log_path=root / "log", status_path=root / "evaluate_2026-09-06.json",
            started_at=datetime.now(timezone.utc).isoformat(), process=process,
        )
        manager._workers[worker.id] = worker
        try:
            try:
                manager.hide_history(worker.id)
            except ValueError:
                pass
            else:
                raise AssertionError("running worker must not be hidden")
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_hidden_old_run_does_not_hide_same_date_live_rerun() -> None:
    import worker_manager as manager_module
    from worker_manager import Worker

    old_history = manager_module.HISTORY_FILE
    old_status = manager_module.STATUS_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager_module.HISTORY_FILE = root / "worker_history.json"
        manager_module.STATUS_ROOT = root / "pipeline_status"
        manager_module.STATUS_ROOT.mkdir()
        old_key = "history-v2:evaluate:2026-09-06:2026-09-01T00:00:00+00:00"
        manager_module.HISTORY_FILE.write_text(
            json.dumps({"hidden": [old_key]}), encoding="utf-8"
        )
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        manager = WorkerManager()
        worker = Worker(
            id="new-rerun", kind="evaluate", scope="2026-09-06", command=[],
            log_path=root / "log", status_path=root / "evaluate_2026-09-06.json",
            started_at="2026-09-02T00:00:00+00:00", process=process,
        )
        manager._workers[worker.id] = worker
        try:
            visible = manager.list()
            assert len(visible) == 1
            assert visible[0]["id"] == "new-rerun"
            assert visible[0]["history_key"] != old_key
        finally:
            process.terminate()
            process.wait(timeout=5)
            manager_module.HISTORY_FILE = old_history
            manager_module.STATUS_ROOT = old_status


def test_legacy_history_keys_are_ignored() -> None:
    import worker_manager as manager_module

    old_history = manager_module.HISTORY_FILE
    with tempfile.TemporaryDirectory() as tmp:
        manager_module.HISTORY_FILE = Path(tmp) / "worker_history.json"
        manager_module.HISTORY_FILE.write_text(
            json.dumps({"hidden": ["history-evaluate_2026-09-06"]}), encoding="utf-8"
        )
        try:
            assert WorkerManager().hidden_history() == []
        finally:
            manager_module.HISTORY_FILE = old_history


def test_gemini_account_pool_secrets_do_not_appear_in_metadata() -> None:
    import credential_store as store_module

    old_file = store_module.GEMINI_ACCOUNTS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        store_module.GEMINI_ACCOUNTS_FILE = Path(tmp) / "gemini_accounts.json"
        store_module.GEMINI_ACCOUNTS_FILE.write_text('{"accounts": []}', encoding="utf-8")
        store = CredentialStore()
        account = store.add_gemini_account("Primary", "person@example.com", "pool-disposable-secret")
        try:
            listed = store.list_gemini_accounts()
            assert listed == [{
                "id": account["id"], "label": "Primary", "email": "person@example.com",
                "enabled": True, "priority": 1, "configured": True,
            }]
            assert "pool-disposable-secret" not in json.dumps(listed)
            environment = store.gemini_environment()
            assert environment["GEMINI_API_KEY_1"] == "pool-disposable-secret"
            assert environment["GEMINI_ACCOUNT_LABEL_1"] == "Primary"
            assert store.delete_gemini_account(str(account["id"])) is True
        finally:
            store_module.GEMINI_ACCOUNTS_FILE = old_file


def test_pool_environment_overrides_inherited_gemini_key() -> None:
    import worker_manager as manager_module

    class PoolStore:
        def environment(self):
            return {
                "GEMINI_ACCOUNT_COUNT": "1",
                "GEMINI_API_KEY_1": "pooled-key",
                "GEMINI_ACCOUNT_ID_1": "a" * 32,
                "GEMINI_ACCOUNT_LABEL_1": "Primary",
            }

    old_pipeline = manager_module.PIPELINE
    old_env = os.environ.get("GEMINI_API_KEY")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake = root / "pipeline.py"
        fake.write_text("import os; print(os.getenv('GEMINI_API_KEY'), os.getenv('GEMINI_API_KEY_1'))", encoding="utf-8")
        manager_module.PIPELINE = fake
        os.environ["GEMINI_API_KEY"] = "inherited-key"
        manager = WorkerManager(PoolStore())
        worker = manager.start("evaluate", {
            "date": "2026-09-10", "mode": "normal", "delay": 0,
            "limit": 1, "max_images": 1, "thinking_level": "low",
        })
        live = manager._workers[worker["id"]]
        live.process.wait(timeout=5)
        for _ in range(20):
            if live.state != "running":
                break
            time.sleep(0.05)
        text = live.log_path.read_text(encoding="utf-8")
        assert "None pooled-key" in text
    manager_module.PIPELINE = old_pipeline
    if old_env is None:
        os.environ.pop("GEMINI_API_KEY", None)
    else:
            os.environ["GEMINI_API_KEY"] = old_env




if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
