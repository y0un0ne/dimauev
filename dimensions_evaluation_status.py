from __future__ import annotations

import calendar
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dimensions_extraction_status import latest_output_month, read_extraction_status


MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def ensure_history_schema(db_path: Path) -> None:
    if not db_path.exists():
        return
    with closing(sqlite3.connect(db_path, timeout=5)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                mode TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0,
                pending_count INTEGER NOT NULL DEFAULT 0, text_route TEXT,
                vision_route TEXT, stop_reason TEXT, status TEXT NOT NULL DEFAULT 'running',
                completed_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL, attempted_at TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, outcome TEXT NOT NULL,
                decision TEXT, category TEXT, error TEXT, preflight INTEGER NOT NULL DEFAULT 0,
                fallback_level INTEGER, credential_slot INTEGER, credential_label TEXT
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(evaluation_attempts)").fetchall()}
        if "credential_slot" not in columns:
            connection.execute("ALTER TABLE evaluation_attempts ADD COLUMN credential_slot INTEGER")
        if "credential_label" not in columns:
            connection.execute("ALTER TABLE evaluation_attempts ADD COLUMN credential_label TEXT")
        marker = "legacy-import"
        if connection.execute("SELECT 1 FROM evaluation_runs WHERE run_id=?", (marker,)).fetchone():
            return
        rows = connection.execute(
            "SELECT conversation_id, decision, category, evaluator_model, evaluated_at, error FROM reviews"
        ).fetchall()
        if not rows:
            return
        timestamps = [str(row[4]) for row in rows if row[4]]
        started = min(timestamps) if timestamps else datetime.now(timezone.utc).isoformat()
        finished = max(timestamps) if timestamps else started
        connection.execute(
            """
            INSERT INTO evaluation_runs(run_id, started_at, finished_at, mode,
                source_count, pending_count, status, completed_count, error_count)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (marker, started, finished, "legacy_import", len(rows), len(rows), "imported",
             sum(bool(row[1]) and not row[5] for row in rows), sum(bool(row[5]) for row in rows)),
        )
        for cid, decision, category, target, attempted_at, error in rows:
            provider, sep, model = str(target or "legacy:unknown").partition(":")
            if not sep:
                provider, model = "legacy", provider
            connection.execute(
                """
                INSERT INTO evaluation_attempts(run_id, conversation_id, attempted_at,
                    provider, model, outcome, decision, category, error, preflight, fallback_level)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (marker, cid, attempted_at or finished, provider, model,
                 "error" if error else "success", decision, category, error,
                 int(target == "preflight"), -1 if target == "preflight" else None),
            )
        connection.commit()


def _active_ids(date_root: Path) -> Set[str]:
    normalized = date_root / "normalized"
    normalized_ids = {path.stem for path in normalized.glob("*.json")} if normalized.exists() else set()
    master_path = date_root / "master_conversations.json"
    if not master_path.exists():
        return normalized_ids
    try:
        master = json.loads(master_path.read_text(encoding="utf-8"))
        ids = {
            str(item.get("conversationid"))
            for item in master.get("master_conversations", [])
            if isinstance(item, dict) and item.get("conversationid")
        }
        return normalized_ids.intersection(ids)
    except Exception:
        return normalized_ids


def _live_status(status_root: Path, date_name: str) -> Optional[Dict[str, Any]]:
    candidates = sorted(status_root.glob(f"evaluate_{date_name}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return None


def read_evaluation_status(output_root: Path, target: date) -> Dict[str, Any]:
    date_name = target.isoformat()
    date_root = output_root / date_name
    extraction = read_extraction_status(output_root, target)
    active_ids = _active_ids(date_root)
    db_path = date_root / "ai_reviews" / "review_state.db"
    summary_path = date_root / "ai_reviews" / "evaluation_summary.json"
    live = _live_status(output_root / "pipeline_status", date_name)
    base: Dict[str, Any] = {
        "date": date_name,
        "status": "waiting_for_input",
        "input_status": extraction["status"],
        "active_conversations": len(active_ids),
        "completed": 0,
        "pending": len(active_ids),
        "errors": 0,
        "safe": 0,
        "unsafe": 0,
        "review_required": 0,
        "preflight": 0,
        "coverage_pct": 0.0,
        "last_activity": None,
        "stop_reason": None,
        "model_breakdown": {},
        "stale_results": 0,
        "run_count": 0,
        "has_history": False,
        "message": "No usable MVP 1 input is available.",
    }
    if not active_ids:
        return base

    base["status"] = "not_started"
    base["message"] = "MVP 1 input is available; evaluation has not started."
    if not db_path.exists():
        if extraction["status"] not in {"complete", "accepted_with_gaps"}:
            base["status"] = "partial_input"
            base["message"] = "MVP 1 input is still partial; evaluator has not started."
        return base

    ensure_history_schema(db_path)

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as connection:
        rows = connection.execute(
            "SELECT conversation_id, decision, evaluator_model, evaluated_at, error FROM reviews"
        ).fetchall()
        active_rows = [row for row in rows if row[0] in active_ids]
        stale = sum(row[0] not in active_ids for row in rows)
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        run_count = 0
        if "evaluation_runs" in tables:
            run_count = connection.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]

    completed_rows = [row for row in active_rows if row[1] in {"SAFE", "UNSAFE", "REVIEW_REQUIRED"} and not row[4]]
    errors = sum(bool(row[4]) for row in active_rows)
    decisions = {
        decision: sum(row[1] == decision and not row[4] for row in active_rows)
        for decision in ("SAFE", "UNSAFE", "REVIEW_REQUIRED")
    }
    models: Dict[str, int] = {}
    for row in completed_rows:
        model = str(row[2] or "unknown")
        models[model] = models.get(model, 0) + 1
    timestamps = [str(row[3]) for row in active_rows if row[3]]
    completed = len(completed_rows)
    pending = max(0, len(active_ids) - completed - errors)
    stop_reason = None
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            stop_reason = summary.get("stop_reason")
        except Exception:
            pass

    live_running = bool(live and live.get("status") == "running")
    quota_markers = ("quota", "daily", "no enabled target", "no enabled model")
    if live_running:
        status = "in_progress"
        message = "An evaluator worker is currently running."
    elif errors:
        status = "needs_retry"
        message = f"{errors} active conversation(s) have evaluator errors."
    elif completed == len(active_ids):
        status = "complete"
        message = "Every active conversation has a successful evaluator result."
    elif stop_reason and any(marker in str(stop_reason).lower() for marker in quota_markers):
        status = "paused_quota"
        message = f"Evaluation paused: {stop_reason}"
    elif extraction["status"] not in {"complete", "accepted_with_gaps"}:
        status = "partial_input"
        message = "MVP 1 input is still partial; current evaluator progress is provisional."
    else:
        status = "incomplete"
        message = f"{pending} conversation(s) remain pending."

    base.update({
        "status": status,
        "completed": completed,
        "pending": pending,
        "errors": errors,
        "safe": decisions["SAFE"],
        "unsafe": decisions["UNSAFE"],
        "review_required": decisions["REVIEW_REQUIRED"],
        "preflight": models.get("preflight", 0),
        "coverage_pct": round(completed / len(active_ids) * 100, 2),
        "last_activity": max(timestamps) if timestamps else None,
        "stop_reason": stop_reason,
        "model_breakdown": dict(sorted(models.items())),
        "stale_results": stale,
        "run_count": int(run_count),
        "has_history": bool(run_count),
        "message": message,
    })
    return base


def month_evaluation_status(output_root: Path, month: str) -> Dict[str, Any]:
    if not MONTH_RE.fullmatch(month):
        raise ValueError("month must use YYYY-MM")
    parsed = datetime.strptime(month, "%Y-%m")
    days = calendar.monthrange(parsed.year, parsed.month)[1]
    statuses = [
        read_evaluation_status(output_root, date(parsed.year, parsed.month, day))
        for day in range(1, days + 1)
    ]
    counts: Dict[str, int] = {}
    for item in statuses:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"month": month, "counts": counts, "dates": statuses}


def latest_evaluation_month(output_root: Path) -> Optional[str]:
    months: List[str] = []
    if output_root.exists():
        for root in output_root.iterdir():
            if root.is_dir() and (root / "ai_reviews" / "review_state.db").exists():
                months.append(root.name[:7])
    return max(months) if months else latest_output_month(output_root)


def evaluation_history(output_root: Path, target: date, limit: int = 100) -> Dict[str, Any]:
    date_root = output_root / target.isoformat()
    db_path = date_root / "ai_reviews" / "review_state.db"
    if not db_path.exists():
        return {"date": target.isoformat(), "runs": [], "attempts": []}
    ensure_history_schema(db_path)
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "evaluation_runs" not in tables:
            return {"date": target.isoformat(), "runs": [], "attempts": []}
        connection.row_factory = sqlite3.Row
        runs = [dict(row) for row in connection.execute(
            "SELECT * FROM evaluation_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        )]
        attempts = [dict(row) for row in connection.execute(
            """
            SELECT attempt_id, run_id, conversation_id, attempted_at, provider,
                   model, outcome, decision, category, error, preflight, fallback_level
                   , credential_slot, credential_label
            FROM evaluation_attempts ORDER BY attempt_id DESC LIMIT ?
            """, (limit,)
        )]
    return {"date": target.isoformat(), "runs": runs, "attempts": attempts}


def evaluation_errors(output_root: Path, target: date, limit: int = 200) -> Dict[str, Any]:
    date_root = output_root / target.isoformat()
    db_path = date_root / "ai_reviews" / "review_state.db"
    active_ids = _active_ids(date_root)
    if not db_path.exists():
        return {"date": target.isoformat(), "errors": []}
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """
            SELECT conversation_id, evaluator_model, evaluated_at, error
            FROM reviews
            WHERE error IS NOT NULL AND error<>''
            ORDER BY evaluated_at DESC LIMIT ?
            """, (limit,)
        )]
    return {
        "date": target.isoformat(),
        "errors": [row for row in rows if row["conversation_id"] in active_ids],
    }
