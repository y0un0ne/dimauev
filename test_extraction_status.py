from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from dimensions_extraction_status import latest_output_month, month_extraction_status, read_extraction_status


def write_summary(root: Path, day: str, value: object) -> None:
    path = root / day / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def good_summary(**overrides: object) -> dict:
    value = {
        "pagination_complete_all": True,
        "final_has_more_all": False,
        "errors_in_db": 0,
        "click_failures": 0,
        "active_snapshot_count": 100,
        "active_details_fetched": 100,
        "missing_from_active_snapshot": 0,
        "acceptance_status": "complete",
    }
    value.update(overrides)
    return value


def test_not_scanned() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status = read_extraction_status(Path(tmp), date(2026, 9, 1))
        assert status["status"] == "not_scanned"
        assert status["output_exists"] is False


def test_complete_and_gap_statuses() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_summary(root, "2026-09-01", good_summary())
        write_summary(root, "2026-09-02", good_summary(
            active_details_fetched=98,
            missing_from_active_snapshot=2,
            acceptance_status="accepted_with_gaps",
            coverage_check_passed=True,
            missing_count_check_passed=True,
        ))
        assert read_extraction_status(root, date(2026, 9, 1))["status"] == "complete"
        gap = read_extraction_status(root, date(2026, 9, 2))
        assert gap["status"] == "accepted_with_gaps"
        assert gap["coverage_pct"] == 98.0


def test_in_progress_incomplete_and_damaged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "2026-09-01" / "normalized").mkdir(parents=True)
        write_summary(root, "2026-09-02", good_summary(click_failures=1, acceptance_status="failed_acceptance"))
        path = root / "2026-09-03" / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text("{bad", encoding="utf-8")
        assert read_extraction_status(root, date(2026, 9, 1))["status"] == "in_progress"
        assert read_extraction_status(root, date(2026, 9, 2))["status"] == "incomplete"
        assert read_extraction_status(root, date(2026, 9, 3))["status"] == "damaged"


def test_latest_month_and_full_calendar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_summary(root, "2026-08-31", good_summary())
        write_summary(root, "2026-09-01", good_summary())
        assert latest_output_month(root) == "2026-09"
        report = month_extraction_status(root, "2026-09")
        assert len(report["dates"]) == 30
        assert report["counts"]["complete"] == 1
        assert report["counts"]["not_scanned"] == 29


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
