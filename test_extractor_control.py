from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
from datetime import date
from pathlib import Path

from dimensions_extractor_mvp1_5_2 import dates_to_process, main_async, wait_for_user_signal


async def exercise() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "control.json"
        waiter = asyncio.create_task(
            wait_for_user_signal(str(path), "waiting_login", "ready?")
        )
        for _ in range(50):
            if path.exists():
                break
            await asyncio.sleep(0.02)
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["phase"] == "waiting_login"
        assert state["signal"] is None
        state["signal"] = "waiting_login"
        path.write_text(json.dumps(state), encoding="utf-8")
        await asyncio.wait_for(waiter, timeout=2)
        final = json.loads(path.read_text(encoding="utf-8"))
        assert final["phase"] == "running"
        assert final["signal"] is None


def test_rescan_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        day = root / "2026-09-01"
        day.mkdir()
        (day / "summary.json").write_text(
            '{"pagination_complete_all":true,"final_has_more_all":false,'
            '"errors_in_db":0,"click_failures":0,"active_snapshot_count":2,'
            '"active_details_fetched":2,"missing_from_active_snapshot":0,'
            '"acceptance_status":"complete"}',
            encoding="utf-8",
        )
        targets = [date(2026, 9, 1)]
        normal, skipped = dates_to_process(targets, root, 0.9, 25, rescan=False, retry_missing=False)
        assert normal == []
        assert skipped["2026-09-01"]["status"] == "skipped_complete"
        refreshed, skipped = dates_to_process(targets, root, 0.9, 25, rescan=True, retry_missing=False)
        assert refreshed == targets
        assert skipped == {}
        retry, skipped = dates_to_process(targets, root, 0.9, 25, rescan=False, retry_missing=True)
        assert retry == []
        assert skipped["2026-09-01"]["status"] == "skipped_no_missing"
        (day / "missing_conversation_ids.json").write_text('["abc"]', encoding="utf-8")
        retry, skipped = dates_to_process(targets, root, 0.9, 25, rescan=False, retry_missing=True)
        assert retry == targets
        assert skipped == {}


def test_manual_continue_does_not_require_brittle_text_gate() -> None:
    source = inspect.getsource(main_async)
    assert "target_page_ready" not in source
    assert "Dimensions setup was not verified" not in source


if __name__ == "__main__":
    asyncio.run(exercise())
    print("PASS structured frontend handoff")
    test_rescan_selection()
    print("PASS rescan preflight selection")
    test_manual_continue_does_not_require_brittle_text_gate()
    print("PASS manual Continue has no brittle text gate")
