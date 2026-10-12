"""Offline tests for pipeline scope and artifact gates."""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import dimensions_pipeline as pipeline


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_date_range_crosses_month() -> None:
    dates = pipeline.date_range(date(2026, 9, 30), date(2026, 10, 2))
    assert [item.isoformat() for item in dates] == [
        "2026-09-30", "2026-10-01", "2026-10-02"
    ]


def test_independent_stage_selection() -> None:
    class Args:
        extract_only = False
        evaluate_only = True
        report_only = False
        skip_extract = False
        skip_evaluate = False
        skip_report = False

    args = Args()
    assert pipeline.selected_mode(args) == "evaluate"
    assert pipeline.stage_selection(args) == (False, True, False)


def test_status_files_do_not_collide_between_workers() -> None:
    root = Path("out")
    dates = [date(2026, 9, 1)]
    extract = pipeline.status_path(root, "extract", dates, ["2026-09"])
    evaluate = pipeline.status_path(root, "evaluate", dates, ["2026-09"])
    assert extract != evaluate
    assert extract.name == "extract_2026-09-01.json"


def test_extraction_accepts_legacy_summary_with_bounded_gaps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_json(root / "2026-09-01" / "summary.json", {
            "pagination_complete_all": True,
            "final_has_more_all": False,
            "errors_in_db": 0,
            "click_failures": 0,
            "active_snapshot_count": 229,
            "active_details_fetched": 227,
            "missing_from_active_snapshot": 2,
        })
        status = pipeline.validate_extraction(root, date(2026, 9, 1), 0.90, 25)
        assert status["valid"] is True
        assert status["acceptance_status"] == "accepted_with_gaps"
        assert status["detail_coverage_pct"] == 99.13


def test_extraction_rejects_hard_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_json(root / "2026-09-01" / "summary.json", {
            "pagination_complete_all": True,
            "final_has_more_all": False,
            "errors_in_db": 0,
            "click_failures": 1,
            "active_snapshot_count": 10,
            "active_details_fetched": 10,
        })
        status = pipeline.validate_extraction(root, date(2026, 9, 1), 0.90, 25)
        assert status["valid"] is False
        assert status["hard_checks_passed"] is False


def test_evaluation_requires_every_source_and_zero_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        date_root = root / "2026-09-01"
        for cid in ("a", "b"):
            write_json(date_root / "normalized" / f"{cid}.json", {})
        summary = date_root / "ai_reviews" / "evaluation_summary.json"
        write_json(summary, {
            "completed_in_db": 2,
            "errors_in_db": 0,
            "safe": 1,
            "unsafe": 1,
            "review_required": 0,
            "stop_reason": None,
            "model_breakdown": {"gemini:model": 2},
        })
        assert pipeline.validate_evaluation(root, date(2026, 9, 1))["valid"] is True
        write_json(summary, {
            "completed_in_db": 1,
            "errors_in_db": 1,
            "stop_reason": "quota exhausted",
        })
        status = pipeline.validate_evaluation(root, date(2026, 9, 1))
        assert status["valid"] is False
        assert status["errors_in_db"] == 1


def test_evaluation_ignores_normalized_files_outside_active_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        date_root = root / "2026-09-01"
        write_json(date_root / "normalized" / "active.json", {})
        write_json(date_root / "normalized" / "stale.json", {})
        write_json(date_root / "master_conversations.json", {
            "master_conversations": [{"conversationid": "active"}],
        })
        write_json(date_root / "ai_reviews" / "evaluation_summary.json", {
            "completed_in_db": 1,
            "errors_in_db": 0,
            "stop_reason": None,
        })
        status = pipeline.validate_evaluation(root, date(2026, 9, 1))
        assert status["valid"] is True
        assert status["source_normalized_files"] == 1
        assert status["stale_normalized_files"] == 1


def test_empty_active_snapshot_ignores_old_normalized_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        date_root = root / "2026-09-01"
        write_json(date_root / "normalized" / "stale.json", {})
        write_json(date_root / "master_conversations.json", {
            "master_conversations": [],
        })
        write_json(date_root / "ai_reviews" / "evaluation_summary.json", {
            "completed_in_db": 0,
            "errors_in_db": 0,
            "stop_reason": None,
        })
        status = pipeline.validate_evaluation(root, date(2026, 9, 1))
        assert status["valid"] is True
        assert status["source_normalized_files"] == 0
        assert status["stale_normalized_files"] == 1


def test_model_breakdown_merge() -> None:
    merged = pipeline.merge_model_breakdowns({
        "a": {"model_breakdown": {"gemini:x": 2, "preflight": 1}},
        "b": {"model_breakdown": {"gemini:x": 3, "groq:y": 4}},
    })
    assert merged == {"gemini:x": 5, "groq:y": 4, "preflight": 1}


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
