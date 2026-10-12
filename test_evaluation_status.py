from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from dimensions_ai_evaluator_mvp2_6 import EvaluationResult, ReviewDB
from dimensions_evaluation_status import evaluation_history, read_evaluation_status


def setup_input(root: Path, count: int = 2) -> Path:
    day = root / "2026-09-01"
    normalized = day / "normalized"
    normalized.mkdir(parents=True)
    ids = [f"c{i}" for i in range(count)]
    for cid in ids:
        (normalized / f"{cid}.json").write_text("{}", encoding="utf-8")
    (day / "master_conversations.json").write_text(json.dumps({
        "master_conversations": [{"conversationid": cid} for cid in ids]
    }), encoding="utf-8")
    (day / "summary.json").write_text(json.dumps({
        "pagination_complete_all": True, "final_has_more_all": False,
        "errors_in_db": 0, "click_failures": 0,
        "active_snapshot_count": count, "active_details_fetched": count,
        "missing_from_active_snapshot": 0, "acceptance_status": "complete",
    }), encoding="utf-8")
    return day


def safe_result() -> EvaluationResult:
    return EvaluationResult(decision="SAFE", unsafe_message_ids=[], category=None,
                            category_id=None, remark=None, manual_review_reason=None)


def test_not_started_and_complete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        day = setup_input(root)
        assert read_evaluation_status(root, date(2026, 9, 1))["status"] == "not_started"
        review_dir = day / "ai_reviews"
        review_dir.mkdir()
        db = ReviewDB(review_dir / "review_state.db")
        for cid in ("c0", "c1"):
            db.save_success(cid, safe_result(), "gemini:x", review_dir / f"{cid}.json", day / "normalized" / f"{cid}.json")
        db.close()
        status = read_evaluation_status(root, date(2026, 9, 1))
        assert status["status"] == "complete"
        assert status["completed"] == 2


def test_needs_retry_and_stale_ignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        day = setup_input(root)
        review_dir = day / "ai_reviews"
        review_dir.mkdir()
        db = ReviewDB(review_dir / "review_state.db")
        db.save_success("c0", safe_result(), "gemini:x", review_dir / "c0.json", day / "normalized" / "c0.json")
        db.save_error("c1", day / "normalized" / "c1.json", "gemini:x", "invalid JSON")
        db.save_success("stale", safe_result(), "gemini:x", review_dir / "stale.json", day / "normalized" / "stale.json")
        db.close()
        status = read_evaluation_status(root, date(2026, 9, 1))
        assert status["status"] == "needs_retry"
        assert status["errors"] == 1
        assert status["stale_results"] == 1


def test_append_only_attempt_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        day = setup_input(root, 1)
        review_dir = day / "ai_reviews"
        review_dir.mkdir()
        db = ReviewDB(review_dir / "review_state.db")
        run = db.start_run("normal", 1, 1, "gemini:x", "gemini:x")
        db.save_attempt(run, "c0", "gemini", "x", "invalid_json", error="bad")
        db.save_attempt(run, "c0", "gemini", "x", "success", result=safe_result())
        db.finish_run(run, "complete", None, 1, 0)
        db.close()
        history = evaluation_history(root, date(2026, 9, 1))
        outcomes = [row["outcome"] for row in history["attempts"]]
        assert outcomes == ["success", "invalid_json"]


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
