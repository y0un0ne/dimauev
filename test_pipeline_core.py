"""Offline checks for critical MVP 1/2 routing and resume behavior."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import dimensions_ai_evaluator_mvp2_6 as evaluator
import dimensions_extractor_mvp1_5_2 as extractor


def test_openai_client_construction() -> None:
    old = os.environ.get("GROQ_API_KEY")
    os.environ["GROQ_API_KEY"] = "offline-test"
    evaluator._CLIENTS.pop(("groq", 0, 90), None)
    try:
        client = evaluator.get_client("groq")
        assert client is not None
        client.close()
    finally:
        evaluator._CLIENTS.pop(("groq", 0, 90), None)
        if old is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = old


def test_empty_route_is_not_vacuously_disabled() -> None:
    assert evaluator.route_labels([]) == ""
    try:
        evaluator.call_router([], {}, [], [], "low", 0, 0)
    except evaluator.NoTargetAvailableError:
        pass
    else:
        raise AssertionError("empty route must raise NoTargetAvailableError")


def test_error_ids_are_retryable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = evaluator.ReviewDB(Path(tmp) / "state.db")
        source = Path(tmp) / "c1.json"
        db.save_error("c1", source, "ollama:qwen3", "adapter failed")
        assert db.error_ids() == {"c1"}
        assert "c1" not in db.completed_ids()
        db.close()


def test_progress_file_is_atomic_and_structured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "progress.json"
        evaluator.write_progress(str(path), "running", 2, 1, "c1", "gemini:x", 1, 0, "saved")
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["status"] == "running"
        assert value["current"] == 1
        assert value["total"] == 2
        assert value["model"] == "gemini:x"


def test_sensitive_input_block_is_terminal_manual_review() -> None:
    message = (
        "Error code: 400 - Input blocked: The prompt could not be submitted. "
        "The prompt contains sensitive words that violate Google's Generative AI Prohibited Use policy."
    )
    assert evaluator.classify_api_error(Exception(message)) == "input_blocked"

    original = evaluator.call_target
    calls = []

    def blocked(target, *args, **kwargs):
        calls.append(target.label)
        callback = kwargs.get("on_attempt")
        if callback:
            callback("input_blocked", None, message)
        return "input_blocked", None, "", message

    evaluator.call_target = blocked
    try:
        route = [
            evaluator.Target("gemini", "gemini-3.5-flash-lite"),
            evaluator.Target("groq", "qwen/qwen3.8-27b"),
        ]
        attempts = []
        result, raw, target, level = evaluator.call_router(
            route, {}, [], [], "low", 0, 0,
            on_attempt=lambda *attempt: attempts.append(attempt),
        )
        assert result.decision == "REVIEW_REQUIRED"
        assert result.manual_review_reason == evaluator.INPUT_BLOCKED_MANUAL_REASON
        assert target.label == "gemini:gemini-3.5-flash-lite"
        assert level == 0
        assert calls == ["gemini:gemini-3.5-flash-lite"]
        assert attempts[0][1] == "input_blocked"
    finally:
        evaluator.call_target = original


def test_gemini_quota_disables_only_current_account() -> None:
    original = evaluator.call_target
    calls = []

    def quota_then_success(target, *args, **kwargs):
        calls.append(target.label)
        if target.credential_slot == 1:
            return "daily_quota", None, "", "requests per day"
        return "success", evaluator.EvaluationResult(
            decision="SAFE", unsafe_message_ids=[], category=None,
            category_id=None, remark=None, manual_review_reason=None,
        ), "{}", ""

    evaluator.call_target = quota_then_success
    try:
        route = [
            evaluator.Target("gemini", "gemini-3.5-flash-lite", 1, "Primary"),
            evaluator.Target("gemini", "gemini-3.5-flash-lite", 2, "Overflow"),
            evaluator.Target("gemini", "gemini-3.7-flash", 1, "Primary"),
        ]
        disabled = {}
        result, _raw, target, _level = evaluator.call_router(route, disabled, [], [], "low", 0, 0)
        assert result.decision == "SAFE"
        assert target.credential_slot == 2
        assert disabled["gemini[Primary]:gemini-3.5-flash-lite"] == "daily_quota"
        assert disabled["gemini[Primary]:gemini-3.7-flash"] == "daily_quota"
        assert "gemini[Overflow]:gemini-3.5-flash-lite" not in disabled
        assert calls == ["gemini[Primary]:gemini-3.5-flash-lite", "gemini[Overflow]:gemini-3.5-flash-lite"]
    finally:
        evaluator.call_target = original


def test_gemini_timeout_moves_to_second_account_without_retry() -> None:
    original = evaluator.call_target
    calls = []

    def timeout_then_success(target, *args, **kwargs):
        calls.append(target.label)
        if target.credential_slot == 1:
            callback = kwargs.get("on_attempt")
            if callback:
                callback("request_timeout", None, "request timed out")
            return "request_timeout", None, "", "request timed out"
        return "success", evaluator.EvaluationResult(
            decision="SAFE", unsafe_message_ids=[], category=None,
            category_id=None, remark=None, manual_review_reason=None,
        ), "{}", ""

    evaluator.call_target = timeout_then_success
    try:
        route = [
            evaluator.Target("gemini", "gemini-3.5-flash-lite", 1, "Primary"),
            evaluator.Target("gemini", "gemini-3.5-flash-lite", 2, "Secondary"),
            evaluator.Target("gemini", "gemini-3.7-flash", 1, "Primary"),
        ]
        disabled = {}
        result, _raw, target, _level = evaluator.call_router(
            route, disabled, [], [], "low", 2, 2, 30
        )
        assert result.decision == "SAFE"
        assert target.credential_slot == 2
        assert calls == [
            "gemini[Primary]:gemini-3.5-flash-lite",
            "gemini[Secondary]:gemini-3.5-flash-lite",
        ]
        assert disabled["gemini[Primary]:gemini-3.5-flash-lite"] == "request_timeout"
        assert "gemini[Primary]:gemini-3.7-flash" not in disabled
    finally:
        evaluator.call_target = original


def test_timeout_error_classification() -> None:
    for message in (
        "httpx.ReadTimeout: timed out",
        "Deadline exceeded while waiting for response",
        "request timeout after 90 seconds",
    ):
        assert evaluator.classify_api_error(Exception(message)) == "request_timeout"


def test_mvp1_old_summary_still_resumes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "summary.json"
        path.write_text(
            '{"pagination_complete_all":true,"final_has_more_all":false,'
            '"errors_in_db":0,"click_failures":0,"active_snapshot_count":229,'
            '"active_details_fetched":227,"missing_from_active_snapshot":2}',
            encoding="utf-8",
        )
        assert extractor.summary_is_complete(path, 0.90, 25)


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
