from __future__ import annotations

import calendar
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _last_updated(root: Path) -> Optional[str]:
    candidates = [
        root / "summary.json",
        root / "master_conversations.json",
        root / "state.db",
        root / "missing_conversation_ids.json",
    ]
    timestamps = [path.stat().st_mtime for path in candidates if path.exists()]
    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps), tz=timezone.utc).isoformat()


def _db_counts(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN details_fetched=1 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN error IS NOT NULL AND error<>'' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN contains_video=1 THEN 1 ELSE 0 END)
                FROM conversations
                """
            ).fetchone()
        return {
            "total": int(row[0] or 0),
            "fetched": int(row[1] or 0),
            "errors": int(row[2] or 0),
            "videos": int(row[3] or 0),
        }
    except (sqlite3.Error, OSError):
        return {}


def read_extraction_status(output_root: Path, target: date) -> Dict[str, Any]:
    date_name = target.isoformat()
    root = output_root / date_name
    base: Dict[str, Any] = {
        "date": date_name,
        "status": "not_scanned",
        "output_exists": root.exists(),
        "summary_exists": False,
        "last_updated": _last_updated(root) if root.exists() else None,
        "active_snapshot_count": 0,
        "active_details_fetched": 0,
        "coverage_pct": None,
        "missing_details": 0,
        "db_errors": 0,
        "click_failures": 0,
        "videos": 0,
        "normalized_files": 0,
        "has_evaluation": False,
        "message": "No local MVP 1 output found.",
    }
    if not root.exists():
        return base

    normalized_dir = root / "normalized"
    base["normalized_files"] = len(list(normalized_dir.glob("*.json"))) if normalized_dir.exists() else 0
    base["has_evaluation"] = (root / "ai_reviews" / "evaluation_summary.json").exists()
    db = _db_counts(root / "state.db")
    base["db_errors"] = db.get("errors", 0)
    base["videos"] = db.get("videos", 0)
    summary_path = root / "summary.json"
    base["summary_exists"] = summary_path.exists()

    if not summary_path.exists():
        has_work = bool(
            db
            or (root / "master_conversations.json").exists()
            or normalized_dir.exists()
            or (root / "raw").exists()
        )
        base["status"] = "in_progress" if has_work else "incomplete"
        base["active_snapshot_count"] = db.get("total", 0)
        base["active_details_fetched"] = db.get("fetched", 0)
        if base["active_snapshot_count"]:
            base["coverage_pct"] = round(
                base["active_details_fetched"] / base["active_snapshot_count"] * 100, 2
            )
            base["missing_details"] = max(
                0, base["active_snapshot_count"] - base["active_details_fetched"]
            )
        base["message"] = "MVP 1 output exists, but no final summary is available."
        return base

    try:
        summary = _read_json(summary_path)
    except Exception as e:
        base["status"] = "damaged"
        base["message"] = f"summary.json cannot be read: {type(e).__name__}: {e}"
        return base

    active = int(summary.get("active_snapshot_count") or db.get("total", 0))
    fetched = int(
        summary.get("active_details_fetched")
        or summary.get("details_after_run_active")
        or db.get("fetched", 0)
    )
    missing = int(summary.get("missing_from_active_snapshot") or max(0, active - fetched))
    coverage = 100.0 if active == 0 else fetched / active * 100
    errors = int(summary.get("errors_in_db") or db.get("errors", 0))
    click_failures = int(summary.get("click_failures") or 0)
    hard_ok = (
        summary.get("pagination_complete_all") is True
        and summary.get("final_has_more_all") is False
        and errors == 0
        and click_failures == 0
    )
    coverage_ok = bool(summary.get("coverage_check_passed", coverage >= 90.0))
    missing_ok = bool(summary.get("missing_count_check_passed", missing <= 25))
    acceptance = str(summary.get("acceptance_status") or "")

    if acceptance == "complete" and hard_ok and missing == 0:
        status = "complete"
    elif acceptance == "accepted_with_gaps" and hard_ok and coverage_ok and missing_ok:
        status = "accepted_with_gaps"
    elif not acceptance and hard_ok and coverage_ok and missing_ok:
        status = "complete" if missing == 0 else "accepted_with_gaps"
    else:
        status = "incomplete"

    messages = {
        "complete": "MVP 1 completed with full detail coverage.",
        "accepted_with_gaps": "MVP 1 passed hard checks with unresolved detail gaps.",
        "incomplete": "MVP 1 output did not pass acceptance checks.",
    }
    base.update({
        "status": status,
        "active_snapshot_count": active,
        "active_details_fetched": fetched,
        "coverage_pct": round(coverage, 2),
        "missing_details": missing,
        "db_errors": errors,
        "click_failures": click_failures,
        "videos": int(summary.get("videos_in_db") or db.get("videos", 0)),
        "message": messages[status],
    })
    return base


def month_extraction_status(output_root: Path, month: str) -> Dict[str, Any]:
    if not MONTH_RE.fullmatch(month):
        raise ValueError("month must use YYYY-MM")
    parsed = datetime.strptime(month, "%Y-%m")
    days = calendar.monthrange(parsed.year, parsed.month)[1]
    statuses = [
        read_extraction_status(output_root, date(parsed.year, parsed.month, day))
        for day in range(1, days + 1)
    ]
    counts: Dict[str, int] = {}
    for item in statuses:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"month": month, "counts": counts, "dates": statuses}


def latest_output_month(output_root: Path) -> Optional[str]:
    months: List[str] = []
    if not output_root.exists():
        return None
    for root in output_root.iterdir():
        if not root.is_dir() or not DATE_RE.fullmatch(root.name):
            continue
        if any((root / name).exists() for name in ("summary.json", "state.db", "master_conversations.json", "normalized")):
            months.append(root.name[:7])
    return max(months) if months else None
