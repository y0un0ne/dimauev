from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from credential_store import CredentialStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = PROJECT_ROOT / "dimensions_pipeline.py"
OUTPUT_ROOT = PROJECT_ROOT / "dimensions_output"
LOG_ROOT = OUTPUT_ROOT / "logs"
STATUS_ROOT = OUTPUT_ROOT / "pipeline_status"
CONTROL_ROOT = Path(os.getenv("LOCALAPPDATA", OUTPUT_ROOT)) / "DimensionsAutoEvaluate" / "controls"
PROGRESS_ROOT = Path(os.getenv("LOCALAPPDATA", OUTPUT_ROOT)) / "DimensionsAutoEvaluate" / "progress"
HISTORY_ROOT = Path(os.getenv("LOCALAPPDATA", OUTPUT_ROOT)) / "DimensionsAutoEvaluate"
HISTORY_FILE = HISTORY_ROOT / "worker_history.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scope_label(payload: Dict[str, Any]) -> str:
    if payload.get("date"):
        return str(payload["date"])
    if payload.get("start_date"):
        return f"{payload['start_date']}_to_{payload['end_date']}"
    return str(payload.get("month") or "unknown")


def history_key(kind: str, scope: str, started_at: str) -> str:
    """Stable per-run key. Scope alone would hide future reruns of same date."""
    return f"history-v2:{kind}:{scope}:{started_at}"


@dataclass
class Worker:
    id: str
    kind: str
    scope: str
    command: List[str]
    log_path: Path
    status_path: Path
    started_at: str
    process: subprocess.Popen[str]
    control_path: Optional[Path] = None
    progress_path: Optional[Path] = None
    state: str = "running"
    returncode: Optional[int] = None
    finished_at: Optional[str] = None
    stop_requested: bool = False
    _log_handle: Any = field(default=None, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        status: Optional[Dict[str, Any]] = None
        if self.status_path.exists():
            try:
                status = json.loads(self.status_path.read_text(encoding="utf-8"))
            except Exception:
                status = None
        return {
            "id": self.id,
            "history_key": history_key(self.kind, self.scope, self.started_at),
            "kind": self.kind,
            "scope": self.scope,
            "state": self.state,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stop_requested": self.stop_requested,
            "log_path": str(self.log_path),
            "status_path": str(self.status_path),
            "status": status,
            "control": self.control_state(),
            "progress": self.progress_state(),
        }

    def control_state(self) -> Optional[Dict[str, Any]]:
        if not self.control_path or not self.control_path.exists():
            return None
        try:
            state = json.loads(self.control_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else None
        except Exception:
            return None

    def progress_state(self) -> Optional[Dict[str, Any]]:
        if not self.progress_path or not self.progress_path.exists():
            return None
        try:
            state = json.loads(self.progress_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else None
        except Exception:
            return None


class WorkerConflict(RuntimeError):
    pass


class WorkerManager:
    def __init__(self, credential_store: Optional[CredentialStore] = None) -> None:
        self._workers: Dict[str, Worker] = {}
        self._lock = threading.Lock()
        self._credential_store = credential_store
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        CONTROL_ROOT.mkdir(parents=True, exist_ok=True)
        PROGRESS_ROOT.mkdir(parents=True, exist_ok=True)
        HISTORY_ROOT.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_hidden() -> set[str]:
        if not HISTORY_FILE.exists():
            return set()
        try:
            value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            hidden = value.get("hidden", []) if isinstance(value, dict) else []
            # Version 1 stored only kind/date, which accidentally hid all future
            # reruns of that scope. Ignore those ambiguous tombstones once.
            return {
                str(item) for item in hidden
                if isinstance(item, str) and str(item).startswith("history-v2:")
            }
        except Exception:
            return set()

    @staticmethod
    def _write_hidden(hidden: set[str]) -> None:
        tmp = HISTORY_FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"hidden": sorted(hidden)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(HISTORY_FILE)

    def _conflicts(self, kind: str, scope: str) -> Optional[Worker]:
        for worker in self._workers.values():
            if worker.state != "running":
                continue
            if kind == "extract" and worker.kind == "extract":
                return worker
            if worker.kind == kind and worker.scope == scope:
                return worker
        return None

    def start(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        scope = scope_label(payload)
        with self._lock:
            conflict = self._conflicts(kind, scope)
            if conflict:
                raise WorkerConflict(f"{conflict.kind} worker already running for {conflict.scope}")

            worker_id = uuid.uuid4().hex[:12]
            control_path = CONTROL_ROOT / f"{worker_id}.json" if kind == "extract" else None
            progress_path = PROGRESS_ROOT / f"{worker_id}.json" if kind == "evaluate" else None
            command, status_path = self._command(kind, payload, control_path, progress_path)
            log_path = LOG_ROOT / f"{kind}_{scope}_{worker_id}.log"
            log_handle = log_path.open("w", encoding="utf-8", buffering=1)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            if kind == "evaluate" and self._credential_store is not None:
                credential_environment = self._credential_store.environment()
                if credential_environment.get("GEMINI_ACCOUNT_COUNT") not in {None, "0"}:
                    environment.pop("GEMINI_API_KEY", None)
                    environment.pop("GOOGLE_API_KEY", None)
                environment.update(credential_environment)
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                creationflags=creationflags,
                env=environment,
                bufsize=1,
            )
            worker = Worker(
                id=worker_id,
                kind=kind,
                scope=scope,
                command=command,
                log_path=log_path,
                status_path=status_path,
                started_at=utc_now(),
                process=process,
                control_path=control_path,
                progress_path=progress_path,
                _log_handle=log_handle,
            )
            self._workers[worker_id] = worker
            threading.Thread(target=self._watch, args=(worker,), daemon=True).start()
            return worker.as_dict()

    def _command(
        self,
        kind: str,
        payload: Dict[str, Any],
        control_path: Optional[Path] = None,
        progress_path: Optional[Path] = None,
    ) -> tuple[List[str], Path]:
        command = [sys.executable, "-u", str(PIPELINE)]
        if kind == "report":
            month = str(payload["month"])
            command += ["--month", month, "--report-only"]
            return command, STATUS_ROOT / f"report_{month}.json"

        if payload.get("date"):
            command += ["--date", str(payload["date"])]
        else:
            command += [
                "--start-date", str(payload["start_date"]),
                "--end-date", str(payload["end_date"]),
            ]
        command.append(f"--{kind}-only")
        scope = scope_label(payload)

        if kind == "extract":
            command += [
                "--min-detail-coverage", str(payload["min_detail_coverage"]),
                "--max-missing-details", str(payload["max_missing_details"]),
            ]
            if payload.get("scan_mode") == "retry_missing":
                command.append("--retry-missing")
            elif payload.get("scan_mode") == "rescan":
                command.append("--rescan")
            if payload.get("continue_on_error"):
                command.append("--continue-on-error")
            if control_path:
                command += ["--extract-control-file", str(control_path)]
        elif kind == "evaluate":
            command += [
                "--delay", str(payload["delay"]),
                "--max-images", str(payload["max_images"]),
                "--limit", str(payload.get("limit", 0)),
                "--request-timeout", str(payload.get("request_timeout", 90)),
                "--thinking-level", str(payload["thinking_level"]),
            ]
            if payload.get("mode") == "retry_errors":
                command.append("--retry-errors")
            elif payload.get("mode") == "force":
                command.append("--force-evaluate")
            if payload.get("text_models"):
                command += ["--text-models", str(payload["text_models"])]
            if payload.get("vision_models"):
                command += ["--vision-models", str(payload["vision_models"])]
            if progress_path:
                command += ["--evaluation-progress-file", str(progress_path)]

        return command, STATUS_ROOT / f"{kind}_{scope}.json"

    def _watch(self, worker: Worker) -> None:
        returncode = worker.process.wait()
        with self._lock:
            worker.returncode = returncode
            worker.finished_at = utc_now()
            worker.state = "stopped" if worker.stop_requested else (
                "complete" if returncode == 0 else "failed"
            )
            if not worker.stop_requested and returncode != 0:
                self._reconcile_unfinished_status(worker)
            if worker._log_handle:
                worker._log_handle.write(
                    f"\n[WORKER] Process exited with code {returncode}. "
                    f"State: {worker.state}.\n"
                )
                if worker.state == "failed":
                    worker._log_handle.write(
                        "[WORKER] If no traceback appears above, inspect the final pipeline status "
                        "and restart the app before retrying.\n"
                    )
                worker._log_handle.flush()
                worker._log_handle.close()
                worker._log_handle = None

    def _reconcile_unfinished_status(self, worker: Worker) -> None:
        """Never leave a dead worker represented as a running pipeline status."""
        if not worker.status_path.exists():
            return
        try:
            status = json.loads(worker.status_path.read_text(encoding="utf-8"))
            if status.get("status") != "running":
                return
            status.update({
                "status": "incomplete",
                "fatal_error": (
                    f"Worker process exited with code {worker.returncode} before pipeline finalized. "
                    f"See {worker.log_path}."
                ),
                "finished_at_utc": worker.finished_at,
            })
            tmp = worker.status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(worker.status_path)
        except Exception as e:
            if worker._log_handle:
                worker._log_handle.write(f"[WORKER] Could not reconcile pipeline status: {e}\n")

    def stop(self, worker_id: str) -> Dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            if worker.state != "running":
                return worker.as_dict()
            worker.stop_requested = True
            process = worker.process

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        return worker.as_dict()

    def continue_worker(self, worker_id: str) -> Dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            if worker.kind != "extract" or worker.state != "running" or not worker.control_path:
                raise ValueError("Worker is not a running MVP 1 extraction")
            state = worker.control_state()
            phase = (state or {}).get("phase")
            if phase not in {"waiting_login", "waiting_close"}:
                raise ValueError("MVP 1 is not waiting for user confirmation")
            updated = dict(state or {})
            updated["signal"] = phase
            updated["signaled_at"] = utc_now()
            tmp = worker.control_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(worker.control_path)
            return worker.as_dict()

    def list(self) -> List[Dict[str, Any]]:
        hidden = self._read_hidden()
        with self._lock:
            workers = [worker.as_dict() for worker in self._workers.values()]
            live_status_paths = {str(worker.status_path.resolve()) for worker in self._workers.values()}
        if STATUS_ROOT.exists():
            for path in STATUS_ROOT.glob("*.json"):
                if str(path.resolve()) in live_status_paths:
                    continue
                try:
                    status = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                kind, _, scope = path.stem.partition("_")
                started_at = str(status.get("started_at_utc") or "")
                workers.append({
                    "id": history_key(kind, scope, started_at), "kind": kind, "scope": scope,
                    "history_key": history_key(kind, scope, started_at),
                    "state": "historical", "returncode": None,
                    "started_at": status.get("started_at_utc") or "",
                    "finished_at": status.get("finished_at_utc"),
                    "stop_requested": False, "log_path": None,
                    "status_path": str(path), "status": status,
                })
        # A live worker must never be hidden by a historical card tombstone.
        visible = [
            worker for worker in workers
            if worker["state"] == "running" or worker["history_key"] not in hidden
        ]
        return sorted(visible, key=lambda item: item["started_at"], reverse=True)

    def hidden_history(self) -> List[str]:
        return sorted(self._read_hidden())

    def hide_history(self, worker_id: str) -> None:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                if worker.state == "running":
                    raise ValueError("A running worker cannot be hidden. Stop or wait for it first.")
                history_key = f"history-{worker.status_path.stem}"
            elif worker_id.startswith("history-v2:"):
                history_key = worker_id
            else:
                raise KeyError(worker_id)
        hidden = self._read_hidden()
        hidden.add(history_key)
        self._write_hidden(hidden)

    def restore_history(self, worker_id: Optional[str] = None) -> int:
        hidden = self._read_hidden()
        if worker_id is None:
            restored = len(hidden)
            self._write_hidden(set())
            return restored
        if not worker_id.startswith("history-v2:"):
            raise ValueError("Invalid history ID")
        existed = worker_id in hidden
        hidden.discard(worker_id)
        self._write_hidden(hidden)
        return int(existed)

    def get(self, worker_id: str) -> Dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            return worker.as_dict()

    def log(self, worker_id: str, max_bytes: int = 200_000) -> str:
        if worker_id.startswith("history-"):
            return "Log unavailable for a worker created before this server session."
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            path = worker.log_path
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > max_bytes:
                handle.seek(-max_bytes, 2)
            return handle.read().decode("utf-8", errors="replace")
