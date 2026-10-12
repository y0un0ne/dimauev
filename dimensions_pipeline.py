from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
MVP1 = ROOT / "dimensions_extractor_mvp1_5_2.py"
MVP2 = ROOT / "dimensions_ai_evaluator_mvp2_6.py"
MVP3 = ROOT / "dimensions_excel_generator_mvp3.py"


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from e


def parse_month(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as e:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from e
    return value


def date_range(start: date, end: date) -> List[date]:
    if end < start:
        raise ValueError("--end-date must be on/after --start-date")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def run_command(command: List[str]) -> int:
    print("\n[RUN] " + subprocess.list2cmdline(command), flush=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    return subprocess.run(command, cwd=ROOT, env=environment).returncode


def validate_extraction(
    output_root: Path,
    target: date,
    min_detail_coverage: float,
    max_missing_details: int,
) -> Dict[str, Any]:
    path = output_root / target.isoformat() / "summary.json"
    status: Dict[str, Any] = {
        "summary_path": str(path),
        "valid": False,
        "acceptance_status": "missing",
    }
    if not path.exists():
        status["error"] = "summary.json is missing"
        return status
    try:
        data = load_json(path)
    except Exception as e:
        status["error"] = f"{type(e).__name__}: {e}"
        return status

    hard_ok = (
        data.get("pagination_complete_all") is True
        and data.get("final_has_more_all") is False
        and int(data.get("errors_in_db") or 0) == 0
        and int(data.get("click_failures") or 0) == 0
    )
    active = int(data.get("active_snapshot_count") or 0)
    fetched = int(data.get("active_details_fetched") or data.get("details_after_run_active") or 0)
    missing = int(data.get("missing_from_active_snapshot") or max(0, active - fetched))
    coverage = 1.0 if active == 0 else fetched / active
    coverage_ok = coverage >= min_detail_coverage
    missing_ok = max_missing_details < 0 or missing <= max_missing_details
    valid = hard_ok and coverage_ok and missing_ok
    acceptance = data.get("acceptance_status")
    if not acceptance:
        acceptance = "complete" if valid and missing == 0 else (
            "accepted_with_gaps" if valid else "failed_acceptance"
        )

    status.update({
        "valid": valid,
        "acceptance_status": acceptance,
        "active_snapshot_count": active,
        "active_details_fetched": fetched,
        "missing_details": missing,
        "detail_coverage_pct": round(coverage * 100, 2),
        "hard_checks_passed": hard_ok,
    })
    if not valid:
        status["error"] = "extraction acceptance checks failed"
    return status


def validate_evaluation(output_root: Path, target: date) -> Dict[str, Any]:
    date_root = output_root / target.isoformat()
    path = date_root / "ai_reviews" / "evaluation_summary.json"
    normalized = date_root / "normalized"
    normalized_ids = {item.stem for item in normalized.glob("*.json")} if normalized.exists() else set()
    master_path = date_root / "master_conversations.json"
    active_ids: Optional[set[str]] = None
    if master_path.exists():
        try:
            master = load_json(master_path)
            active_ids = {
                str(item.get("conversationid"))
                for item in master.get("master_conversations", [])
                if isinstance(item, dict) and item.get("conversationid")
            }
        except Exception:
            active_ids = None
    source_count = len(
        normalized_ids if active_ids is None else normalized_ids.intersection(active_ids)
    )
    status: Dict[str, Any] = {
        "summary_path": str(path),
        "valid": False,
        "source_normalized_files": source_count,
        "stale_normalized_files": (
            len(normalized_ids - active_ids) if active_ids is not None else 0
        ),
    }
    if not path.exists():
        status["error"] = "evaluation_summary.json is missing"
        return status
    try:
        data = load_json(path)
    except Exception as e:
        status["error"] = f"{type(e).__name__}: {e}"
        return status

    completed = int(data.get("completed_in_db") or 0)
    errors = int(data.get("errors_in_db") or 0)
    decisions = {
        "SAFE": int(data.get("safe") or 0),
        "UNSAFE": int(data.get("unsafe") or 0),
        "REVIEW_REQUIRED": int(data.get("review_required") or 0),
    }
    model_breakdown = data.get("model_breakdown") or {}
    state_path = date_root / "ai_reviews" / "review_state.db"
    if active_ids and state_path.exists():
        with sqlite3.connect(state_path) as connection:
            rows = connection.execute(
                "SELECT conversation_id, decision, error FROM reviews"
            ).fetchall()
        active_rows = [row for row in rows if row[0] in active_ids]
        completed = sum(
            row[1] in {"SAFE", "UNSAFE", "REVIEW_REQUIRED"} and not row[2]
            for row in active_rows
        )
        errors = sum(bool(row[2]) for row in active_rows)
        decisions = {
            decision: sum(row[1] == decision and not row[2] for row in active_rows)
            for decision in ("SAFE", "UNSAFE", "REVIEW_REQUIRED")
        }
        model_breakdown = {}
        results_dir = date_root / "ai_reviews" / "results"
        for result_path in results_dir.glob("*.json"):
            if result_path.stem not in active_ids:
                continue
            try:
                model = str(load_json(result_path).get("evaluator_model") or "unknown")
            except Exception:
                continue
            model_breakdown[model] = model_breakdown.get(model, 0) + 1
    valid = completed == source_count and errors == 0 and not data.get("stop_reason")
    status.update({
        "valid": valid,
        "completed_in_db": completed,
        "errors_in_db": errors,
        "safe": decisions["SAFE"],
        "unsafe": decisions["UNSAFE"],
        "review_required": decisions["REVIEW_REQUIRED"],
        "stop_reason": data.get("stop_reason"),
        "model_breakdown": model_breakdown,
        "disabled_targets": data.get("disabled_targets_this_run") or {},
    })
    if not valid:
        status["error"] = (
            f"evaluation incomplete: completed={completed}/{source_count}, errors={errors}"
        )
    return status


def merge_model_breakdowns(statuses: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for status in statuses.values():
        for model, count in (status.get("model_breakdown") or {}).items():
            merged[str(model)] = merged.get(str(model), 0) + int(count or 0)
    return dict(sorted(merged.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Dimensions MVP 1.5.2, MVP 2.6, and MVP 3 as one validated pipeline"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", type=parse_date, help="Single date in YYYY-MM-DD")
    mode.add_argument("--start-date", type=parse_date, help="Range start in YYYY-MM-DD")
    mode.add_argument("--month", type=parse_month, help="Report-only month in YYYY-MM")
    parser.add_argument("--end-date", type=parse_date, help="Range end in YYYY-MM-DD")

    stage_mode = parser.add_mutually_exclusive_group()
    stage_mode.add_argument("--extract-only", action="store_true", help="Run only MVP 1")
    stage_mode.add_argument("--evaluate-only", action="store_true", help="Run only MVP 2")
    stage_mode.add_argument("--report-only", action="store_true", help="Run only MVP 3")

    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--skip-evaluate", action="store_true")
    parser.add_argument("--skip-report", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")

    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--rescan", "--rerun-extract", dest="rescan", action="store_true")
    evaluator_mode = parser.add_mutually_exclusive_group()
    evaluator_mode.add_argument("--retry-errors", action="store_true")
    evaluator_mode.add_argument("--force-evaluate", action="store_true")

    parser.add_argument("--output-dir", default="dimensions_output")
    parser.add_argument("--profile-dir", default=".dimensions_playwright_profile")
    parser.add_argument("--extract-control-file", help=argparse.SUPPRESS)
    parser.add_argument("--report-output-dir", default="dimensions_output/monthly_reports")
    parser.add_argument("--min-detail-coverage", type=float, default=0.90)
    parser.add_argument("--max-missing-details", type=int, default=25)

    parser.add_argument("--text-models")
    parser.add_argument("--vision-models")
    parser.add_argument("--thinking-level", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--delay", type=float, default=4.2)
    parser.add_argument("--request-timeout", type=int, default=90)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-images", type=int, default=12)
    parser.add_argument("--evaluation-progress-file", help=argparse.SUPPRESS)
    return parser


def selected_mode(args: argparse.Namespace) -> str:
    if args.extract_only:
        return "extract"
    if args.evaluate_only:
        return "evaluate"
    if args.report_only:
        return "report"
    return "full"


def resolve_scope(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Tuple[List[date], List[str]]:
    mode = selected_mode(args)
    if args.month:
        if args.end_date:
            parser.error("--end-date cannot be combined with --month")
        if mode not in {"report", "full"}:
            parser.error("--month can only be used with --report-only")
        if mode == "full" and (not args.skip_extract or not args.skip_evaluate):
            parser.error("use --month --report-only, or include --skip-extract --skip-evaluate")
        return [], [args.month]
    if mode == "report":
        parser.error("--report-only requires --month")
    if args.date:
        if args.end_date:
            parser.error("--end-date cannot be combined with --date")
        dates = [args.date]
    else:
        if not args.end_date:
            parser.error("--end-date is required with --start-date")
        try:
            dates = date_range(args.start_date, args.end_date)
        except ValueError as e:
            parser.error(str(e))
    return dates, sorted({target.strftime("%Y-%m") for target in dates})


def stage_selection(args: argparse.Namespace) -> Tuple[bool, bool, bool]:
    mode = selected_mode(args)
    if mode == "extract":
        return True, False, False
    if mode == "evaluate":
        return False, True, False
    if mode == "report":
        return False, False, True
    return not args.skip_extract, not args.skip_evaluate, not args.skip_report


def status_path(output_root: Path, mode: str, dates: List[date], months: List[str]) -> Path:
    if dates:
        scope = dates[0].isoformat()
        if len(dates) > 1:
            scope += f"_to_{dates[-1].isoformat()}"
    else:
        scope = "_".join(months)
    return output_root / "pipeline_status" / f"{mode}_{scope}.json"


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not (0 < args.min_detail_coverage <= 1):
        parser.error("--min-detail-coverage must be > 0 and <= 1")
    if args.max_missing_details < -1:
        parser.error("--max-missing-details must be -1 or >= 0")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if not 10 <= args.request_timeout <= 600:
        parser.error("--request-timeout must be between 10 and 600")

    dates, months = resolve_scope(args, parser)
    mode = selected_mode(args)
    run_extract, run_evaluate, run_report = stage_selection(args)
    if args.extract_only and args.skip_extract:
        parser.error("--extract-only cannot be combined with --skip-extract")
    if args.evaluate_only and args.skip_evaluate:
        parser.error("--evaluate-only cannot be combined with --skip-evaluate")
    if args.report_only and args.skip_report:
        parser.error("--report-only cannot be combined with --skip-report")
    output_root = Path(args.output_dir).resolve()
    report_root = Path(args.report_output_dir).resolve()
    summary_path = status_path(output_root, mode, dates, months)
    started = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {
        "status": "running",
        "mode": mode,
        "started_at_utc": started.isoformat(),
        "dates": [target.isoformat() for target in dates],
        "months": months,
        "stages": {},
        "extraction": {},
        "evaluation": {},
        "reports": {},
    }

    def save() -> None:
        json_dump(summary_path, summary)

    save()
    failed = False
    blocked_after_extraction = False
    eligible_dates = list(dates)

    try:
        if dates and run_extract:
            command = [sys.executable, str(MVP1)]
            if len(dates) == 1:
                command += ["--date", dates[0].isoformat()]
            else:
                command += ["--start-date", dates[0].isoformat(), "--end-date", dates[-1].isoformat()]
            command += [
                "--output-dir", str(output_root),
                "--profile-dir", str(Path(args.profile_dir).resolve()),
                "--min-detail-coverage", str(args.min_detail_coverage),
                "--max-missing-details", str(args.max_missing_details),
            ]
            if args.retry_missing:
                command.append("--retry-missing")
            if args.rescan:
                command.append("--rescan")
            if args.continue_on_error:
                command.append("--continue-on-error")
            if args.extract_control_file:
                command += ["--control-file", str(Path(args.extract_control_file).resolve())]
            code = run_command(command)
            summary["stages"]["extract"] = {"returncode": code}
            if code != 0:
                failed = True

        if dates and mode != "evaluate":
            extraction = {
                target.isoformat(): validate_extraction(
                    output_root, target, args.min_detail_coverage, args.max_missing_details
                )
                for target in dates
            }
            summary["extraction"] = extraction
            eligible_dates = [target for target in dates if extraction[target.isoformat()]["valid"]]
            invalid_dates = [target.isoformat() for target in dates if target not in eligible_dates]
            if invalid_dates:
                failed = True
                print("[GATE] Extraction invalid for: " + ", ".join(invalid_dates))
                if not args.continue_on_error:
                    print("[STOP] Evaluation skipped. Use --continue-on-error to process valid dates.")
                    eligible_dates = []
                    blocked_after_extraction = True
            save()
        elif dates:
            extraction = {
                target.isoformat(): validate_extraction(
                    output_root, target, args.min_detail_coverage, args.max_missing_details
                )
                for target in dates
            }
            summary["extraction"] = extraction
            eligible_dates = list(dates)
            summary["partial_input_dates"] = [
                target.isoformat()
                for target in dates
                if not extraction[target.isoformat()]["valid"]
            ]
            save()

        if dates and run_evaluate:
            stage_results: Dict[str, int] = {}
            for target in eligible_dates:
                command = [
                    sys.executable, "-u", str(MVP2),
                    "--date", target.isoformat(),
                    "--input-root", str(output_root),
                    "--thinking-level", args.thinking_level,
                    "--delay", str(args.delay),
                    "--max-images", str(args.max_images),
                    "--limit", str(args.limit),
                    "--request-timeout", str(args.request_timeout),
                ]
                if args.evaluation_progress_file:
                    command += ["--progress-file", str(Path(args.evaluation_progress_file).resolve())]
                if args.retry_errors:
                    command.append("--retry-errors")
                if args.force_evaluate:
                    command.append("--force")
                if args.text_models:
                    command += ["--text-models", args.text_models]
                if args.vision_models:
                    command += ["--vision-models", args.vision_models]
                code = run_command(command)
                stage_results[target.isoformat()] = code
                if code != 0:
                    failed = True
                    if not args.continue_on_error:
                        break
            summary["stages"]["evaluate"] = {"returncodes": stage_results}

        if dates and (run_evaluate or (mode == "full" and args.skip_evaluate)):
            evaluation = {
                target.isoformat(): validate_evaluation(output_root, target)
                for target in eligible_dates
            }
            summary["evaluation"] = evaluation
            if any(not item["valid"] for item in evaluation.values()):
                failed = True
            summary["model_breakdown"] = merge_model_breakdowns(evaluation)
            save()

        if run_report and not blocked_after_extraction:
            report_codes: Dict[str, int] = {}
            for month in months:
                command = [
                    sys.executable, str(MVP3),
                    "--month", month,
                    "--input-root", str(output_root),
                    "--output-root", str(report_root),
                ]
                code = run_command(command)
                report_codes[month] = code
                manifest_path = report_root / f"report_build_{month}.json"
                manifest: Dict[str, Any] = {}
                if manifest_path.exists():
                    try:
                        manifest = load_json(manifest_path)
                    except Exception:
                        manifest = {}
                workbook = Path(manifest.get("generated_path") or (
                    report_root / f"Conversation_Monitoring_{month}.xlsx"
                ))
                summary["reports"][month] = {
                    "returncode": code,
                    "workbook": str(workbook),
                    "exists": workbook.exists(),
                    "generated_this_run": code == 0 and workbook.exists(),
                    "status": manifest.get("status"),
                    "canonical_replaced": manifest.get("canonical_replaced"),
                    "warnings": manifest.get("warnings") or [],
                }
                if code != 0 or not workbook.exists():
                    failed = True
            summary["stages"]["report"] = {"returncodes": report_codes}
        elif blocked_after_extraction and run_report:
            summary["stages"]["report"] = {
                "skipped": True,
                "reason": "extraction gate failed",
            }

    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(f"\n[STOPPED] Pipeline summary: {summary_path}")
        return 130
    except Exception as e:
        failed = True
        summary["fatal_error"] = f"{type(e).__name__}: {e}"

    partial_input = bool(summary.get("partial_input_dates")) and not failed
    summary["status"] = "incomplete" if failed else ("partial_input" if partial_input else "complete")
    summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["duration_seconds"] = round(
        (datetime.now(timezone.utc) - started).total_seconds(), 2
    )
    save()

    print("\n=== PIPELINE SUMMARY ===")
    print(f"Status       : {summary['status']}")
    print(f"Dates        : {len(dates)}")
    print(f"Months       : {', '.join(months)}")
    print(f"Summary      : {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
