from __future__ import annotations

import argparse
import calendar
import asyncio
import json
import os
import re
import sqlite3
import sys
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright, Page, Response, TimeoutError as PlaywrightTimeoutError

from dimensions_extraction_status import latest_output_month, month_extraction_status

ADMIN_URL = "https://admin.sahabat-ai.com/"
GET_CONVERSATIONS_TOKEN = "getconversations"
GET_MESSAGES_TOKEN = "getmsgsbyconversationid"

MODEL_RE = re.compile(r"Request served by model:\s*(.*?)\.\s*Input:", re.I | re.S)
TIMESTAMP_TEXT_RE = re.compile(r"\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}")
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

JAKARTA = ZoneInfo("Asia/Jakarta")
UTC = timezone.utc


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def parse_dimensions_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # Observed API format: MM-DD-YYYY HH:MM:SS (backend/UTC)
    try:
        return datetime.strptime(s, "%m-%d-%Y %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def to_wib_string(s: Optional[str]) -> Optional[str]:
    dt = parse_dimensions_time(s)
    if dt is None:
        return None
    return dt.astimezone(JAKARTA).strftime("%m-%d-%Y %H:%M:%S")


def text_from_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    pieces: List[str] = []
    for item in content:
        if isinstance(item, dict):
            txt = item.get("text")
            if isinstance(txt, str) and txt.strip():
                pieces.append(txt.strip())
    return "\n".join(pieces)


def media_kind(url: str, declared_type: str = "") -> str:
    declared = (declared_type or "").lower()
    if "video" in declared:
        return "video"
    if "image" in declared:
        return "image"
    path = urllib.parse.urlparse(url).path.lower()
    ext = Path(path).suffix
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return declared or "unknown"


def model_from_details(details: Any) -> Optional[str]:
    if not isinstance(details, dict):
        return None
    txt = details.get("text")
    if not isinstance(txt, str):
        return None
    m = MODEL_RE.search(txt)
    return m.group(1).strip() if m else None


def normalize_messages(master: Dict[str, Any], payload: Dict[str, Any], is_disliked: Optional[bool]) -> Dict[str, Any]:
    data = payload.get("data") or {}
    turns_raw = data.get("data") or []
    turns: List[Dict[str, Any]] = []
    unsupported_types: Set[str] = set()
    contains_video = False

    for idx, turn in enumerate(turns_raw, start=1):
        if not isinstance(turn, dict):
            continue
        user_type = str(turn.get("type") or "unknown")
        user_media = list(turn.get("media_urls") or [])
        if "video" in user_type.lower():
            contains_video = True
        if user_type.lower() not in {"text", "image"}:
            unsupported_types.add(user_type)
        for u in user_media:
            if isinstance(u, str) and media_kind(u, user_type) == "video":
                contains_video = True

        responses = []
        for resp in turn.get("response") or []:
            if not isinstance(resp, dict):
                continue
            rtype = str(resp.get("type") or "unknown")
            rmedia = list(resp.get("media_urls") or [])
            if "video" in rtype.lower():
                contains_video = True
            if rtype.lower() not in {"text", "image"}:
                unsupported_types.add(rtype)
            for u in rmedia:
                if isinstance(u, str) and media_kind(u, rtype) == "video":
                    contains_video = True
            responses.append({
                "type": rtype,
                "message_id": resp.get("message_id"),
                "parent_message_id": resp.get("parent_message_id"),
                "created_at_api": resp.get("created_at"),
                "created_at_wib": to_wib_string(resp.get("created_at")),
                "text": text_from_content(resp.get("content")),
                "media_urls": rmedia,
                "harmful_response": resp.get("harmful_response"),
                "isunsafe": resp.get("isunsafe"),
            })

        turns.append({
            "turn_index": idx,
            "message_id": turn.get("message_id"),
            "parent_message_id": turn.get("parent_message_id"),
            "created_at_api": turn.get("created_at"),
            "created_at_wib": to_wib_string(turn.get("created_at")),
            "model": model_from_details(turn.get("inference_model_details")),
            "user": {
                "type": user_type,
                "text": text_from_content(turn.get("content")),
                "media_urls": user_media,
                "harmful_query": turn.get("harmful_query"),
            },
            "assistant": {
                "responses": responses,
            },
            "isreviewed": turn.get("isreviewed"),
        })

    created = master.get("created_at")
    updated = master.get("updated_at")
    return {
        "conversation_id": data.get("conversation_id") or master.get("conversationid"),
        "customer_id": data.get("customerid"),
        "title": master.get("conversation_title"),
        "cycle_id": master.get("cycleid"),
        "created_at_api": created,
        "created_at_wib": to_wib_string(created),
        "updated_at_api": updated,
        "updated_at_wib": to_wib_string(updated),
        "is_disliked": "1" if is_disliked is True else ("0" if is_disliked is False else None),
        "message_count": data.get("noofmessages") or data.get("totalrecords") or len(turns),
        "markstatus": data.get("markstatus"),
        "historymarkstatus": data.get("historymarkstatus"),
        "reviewername": data.get("reviewername"),
        "reviewdate": data.get("reviewdate"),
        "contains_video": contains_video,
        "unsupported_types": sorted(unsupported_types),
        "turns": turns,
    }


def guess_extension(url: str, content_type: Optional[str]) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext in IMAGE_EXTS | VIDEO_EXTS:
        return ext
    ct = (content_type or "").lower().split(";")[0].strip()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    return mapping.get(ct, ".bin")


def download_url(url: str, dest_without_ext: Path, timeout: int = 60) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        content_type = r.headers.get("Content-Type")
    ext = guess_extension(url, content_type)
    dest = dest_without_ext.with_suffix(ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                created_at_api TEXT,
                updated_at_api TEXT,
                is_disliked TEXT,
                raw_path TEXT,
                normalized_path TEXT,
                details_fetched INTEGER DEFAULT 0,
                contains_video INTEGER DEFAULT 0,
                error TEXT,
                extracted_at TEXT
            )
            """
        )
        self.conn.commit()

    def seed_master(
        self,
        master: Dict[str, Dict[str, Any]],
        disliked_ids: Optional[Set[str]] = None,
        dislike_tracking_enabled: bool = False,
    ) -> None:
        """Seed the frozen ALL snapshot.

        MVP 1.5.2 intentionally does not perform a second Disliked=Yes pass.
        Therefore dislike status is left NULL/unknown for new rows. If this date
        was previously processed by MVP 1.3, an existing known 0/1 value is
        preserved instead of being overwritten by NULL.
        """
        disliked_ids = disliked_ids or set()
        rows = []
        for cid, item in master.items():
            if dislike_tracking_enabled:
                disliked_value = "1" if cid in disliked_ids else "0"
            else:
                disliked_value = None
            rows.append((
                cid,
                item.get("conversation_title"),
                item.get("created_at"),
                item.get("updated_at"),
                disliked_value,
            ))
        self.conn.executemany(
            """
            INSERT INTO conversations(conversation_id, title, created_at_api, updated_at_api, is_disliked)
            VALUES(?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                title=excluded.title,
                created_at_api=excluded.created_at_api,
                updated_at_api=excluded.updated_at_api,
                is_disliked=COALESCE(excluded.is_disliked, conversations.is_disliked)
            """,
            rows,
        )
        self.conn.commit()

    def get_dislike_status(self, cid: str) -> Optional[bool]:
        row = self.conn.execute(
            "SELECT is_disliked FROM conversations WHERE conversation_id=?",
            (cid,),
        ).fetchone()
        if not row:
            return None
        if row[0] == "1":
            return True
        if row[0] == "0":
            return False
        return None

    def mark_detail(self, cid: str, raw_path: Path, normalized_path: Path, contains_video: bool) -> None:
        self.conn.execute(
            """
            UPDATE conversations
            SET raw_path=?, normalized_path=?, details_fetched=1, contains_video=?, error=NULL, extracted_at=?
            WHERE conversation_id=?
            """,
            (str(raw_path), str(normalized_path), int(contains_video), datetime.now(JAKARTA).isoformat(), cid),
        )
        self.conn.commit()

    def mark_error(self, cid: str, error: str) -> None:
        self.conn.execute("UPDATE conversations SET error=? WHERE conversation_id=?", (error[:2000], cid))
        self.conn.commit()

    def fetched_ids(self) -> Set[str]:
        rows = self.conn.execute("SELECT conversation_id FROM conversations WHERE details_fetched=1").fetchall()
        return {r[0] for r in rows}

    def summary(self) -> Dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        fetched = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE details_fetched=1").fetchone()[0]
        disliked = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE is_disliked='1'").fetchone()[0]
        videos = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE contains_video=1").fetchone()[0]
        errors = self.conn.execute("SELECT COUNT(*) FROM conversations WHERE error IS NOT NULL AND error <> ''").fetchone()[0]
        return {"total": total, "fetched": fetched, "disliked": disliked, "videos": videos, "errors": errors}

    def close(self) -> None:
        self.conn.close()


@dataclass
class CaptureState:
    root: Path
    target_date: str
    master_all: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    disliked_ids: Set[str] = field(default_factory=set)
    capture_mode: str = "idle"  # all | disliked | details | idle
    totalrecords_all: Optional[int] = None
    totalrecords_disliked: Optional[int] = None
    last_page_all: Optional[int] = None
    last_page_disliked: Optional[int] = None
    has_more_all: Optional[bool] = None
    has_more_disliked: Optional[bool] = None
    raw_details: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # IDs seen in the currently loaded UI list session. This is reset whenever
    # the user reapplies a filter. It is intentionally separate from master_all
    # because switching filters resets the sidebar even though master_all remains
    # populated from the previous stage.
    current_list_ids: Set[str] = field(default_factory=set)

    def reset_list_session(self, mode: Optional[str] = None) -> None:
        """Reset the IDs/pagination state for a freshly-applied UI filter.

        `totalrecords` is intentionally retained as informational metadata, but
        page/has_more are cleared so a terminal state from an earlier filter
        cannot be mistaken for the newly-applied list.
        """
        self.current_list_ids.clear()
        if mode in {"all", "details"}:
            self.last_page_all = None
            self.has_more_all = None
        elif mode == "disliked":
            self.last_page_disliked = None
            self.has_more_disliked = None

    async def handle_response(self, response: Response) -> None:
        url = response.url.lower()
        try:
            if GET_CONVERSATIONS_TOKEN in url:
                payload = await response.json()
                self._consume_conversation_list(payload)
            elif GET_MESSAGES_TOKEN in url:
                payload = await response.json()
                self._consume_messages(payload)
        except Exception:
            # Keep listener best-effort; explicit detail fetch path handles its own errors too.
            return

    def _consume_conversation_list(self, payload: Dict[str, Any]) -> None:
        data = payload.get("data") or {}
        items = data.get("data") or []
        ids = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("conversationid")
            if not cid:
                continue
            ids.append(cid)
            if self.capture_mode in {"all", "disliked", "details"}:
                self.current_list_ids.add(cid)
            # Stage A (`all`) freezes the master snapshot. Stage C (`details`)
            # is only a second UI pass used to expose/click rows; it must not
            # silently mutate the frozen Stage-A master set if the live queue
            # changes while the script is running.
            if self.capture_mode == "all":
                self.master_all[cid] = item
            elif self.capture_mode == "disliked":
                self.disliked_ids.add(cid)
        total = data.get("totalrecords")
        page_no = data.get("page")
        has_more = data.get("has_more")
        if self.capture_mode in {"all", "details"}:
            if isinstance(total, int):
                self.totalrecords_all = total
            if isinstance(page_no, int):
                self.last_page_all = page_no
            if isinstance(has_more, bool):
                self.has_more_all = has_more
        elif self.capture_mode == "disliked":
            if isinstance(total, int):
                self.totalrecords_disliked = total
            if isinstance(page_no, int):
                self.last_page_disliked = page_no
            if isinstance(has_more, bool):
                self.has_more_disliked = has_more

    def _consume_messages(self, payload: Dict[str, Any]) -> None:
        data = payload.get("data") or {}
        cid = data.get("conversation_id")
        if cid:
            self.raw_details[cid] = payload


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


def write_control_state(path: Path, phase: str, prompt: str, signal: Optional[str] = None) -> None:
    json_dump(path, {
        "phase": phase,
        "prompt": prompt,
        "signal": signal,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


async def wait_for_user_signal(
    control_file: Optional[str],
    phase: str,
    prompt: str,
) -> None:
    if not control_file:
        await ainput(prompt)
        return

    path = Path(control_file).resolve()
    write_control_state(path, phase, prompt)
    print(f"[WAITING FOR UI] {prompt}")
    while True:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("signal") == phase:
                write_control_state(path, "running", "MVP 1 is continuing.")
                return
        except (OSError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.5)


async def find_sidebar_scroll_info(page: Page, action: str = "status") -> Optional[Dict[str, float]]:
    js = """
    ({action}) => {
      const els = [...document.querySelectorAll('*')];
      const cand = els.map((el) => {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const scrollable = el.scrollHeight > el.clientHeight + 80 &&
          ['auto','scroll'].includes(style.overflowY);
        if (!scrollable || r.width < 120 || r.width > 520 || r.height < 250 || r.left > 420 || r.right < 120) return null;
        const score = (el.scrollHeight - el.clientHeight) + r.height - Math.max(0, r.left) * 0.2;
        return {el, r, score};
      }).filter(Boolean).sort((a,b)=>b.score-a.score);
      if (!cand.length) return null;
      const el = cand[0].el;
      if (action === 'top') el.scrollTop = 0;
      if (action === 'bottom') el.scrollTop = el.scrollHeight;
      if (action === 'next') el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(250, el.clientHeight * 0.82));
      return {
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        left: cand[0].r.left,
        top: cand[0].r.top,
        width: cand[0].r.width,
        height: cand[0].r.height
      };
    }
    """
    return await page.evaluate(js, {"action": action})


async def find_load_more_button(page: Page):
    """Return the visible Load More locator in the left conversation list, if present."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*Load More\s*$", re.I)),
        page.get_by_text("Load More", exact=True),
    ]
    for loc in candidates:
        try:
            count = await loc.count()
            for i in range(count):
                item = loc.nth(i)
                if not await item.is_visible():
                    continue
                box = await item.bounding_box()
                # The relevant button is in the left conversation sidebar.
                if box and box["x"] < 430:
                    return item
        except Exception:
            continue
    return None


async def click_load_more_once(page: Page, capture: CaptureState) -> Tuple[bool, int, Optional[int], Optional[bool]]:
    """Click one Load More and synchronously consume its getconversations response.

    The previous MVP relied on the asynchronous page.on('response') listener having
    updated the ID set before the next loop iteration. That can race under load. Here
    the exact response generated by the click is parsed and consumed before returning.
    """
    button = await find_load_more_button(page)
    if button is None:
        return False, 0, None, None
    try:
        try:
            if not await button.is_enabled():
                return False, 0, None, None
        except Exception:
            pass
        await button.scroll_into_view_if_needed(timeout=5000)
        before = len(capture.current_list_ids)
        async with page.expect_response(
            lambda r: GET_CONVERSATIONS_TOKEN in r.url.lower() and r.request.method.upper() == "POST",
            timeout=20000,
        ) as resp_info:
            await button.click(timeout=10000)
        resp = await resp_info.value
        payload = await resp.json()
        # Consume synchronously to eliminate listener timing races. The background
        # listener may consume it again; sets/dicts make that harmless.
        capture._consume_conversation_list(payload)
        data = payload.get("data") or {}
        page_no = data.get("page") if isinstance(data.get("page"), int) else None
        has_more = data.get("has_more") if isinstance(data.get("has_more"), bool) else None
        await page.wait_for_timeout(800)
        return True, len(capture.current_list_ids) - before, page_no, has_more
    except PlaywrightTimeoutError:
        return False, 0, None, None
    except Exception:
        return False, 0, None, None


async def exhaust_current_list(page: Page, capture: CaptureState, mode: str) -> bool:
    """Load the current result set until the backend says pagination is finished.

    IMPORTANT: Dimensions' `totalrecords` can remain higher than the currently
    retrievable Not-Reviewed queue after some conversations have already been
    reviewed/removed. Therefore completeness is defined by the API's terminal
    pagination signal (`has_more == false`), not by `unique_ids == totalrecords`.

    Returns True only when a terminal `has_more == false` response/state has been
    observed for the current filter. If the UI stops exposing Load More while the
    backend still says `has_more == true`, the function fails loudly.
    """
    capture.capture_mode = mode
    await page.wait_for_timeout(1200)

    miss_retries = 0
    max_miss_retries = 12
    last_reported = None

    def state() -> Tuple[Optional[int], Optional[bool], Optional[int]]:
        if mode in {"all", "details"}:
            return capture.totalrecords_all, capture.has_more_all, capture.last_page_all
        return capture.totalrecords_disliked, capture.has_more_disliked, capture.last_page_disliked

    for _attempt in range(1200):
        total, has_more, last_page = state()
        current_count = len(capture.current_list_ids)

        # The backend's terminal pagination flag is the authoritative completion
        # signal for the active queue. `totalrecords` is retained only as info.
        if has_more is False:
            capture.capture_mode = "idle"
            return True

        before = current_count
        clicked, added, page_no, response_has_more = await click_load_more_once(page, capture)
        after = len(capture.current_list_ids)

        if clicked:
            total, has_more, last_page = state()
            page_txt = f" page={page_no}" if page_no is not None else ""
            hm_txt = f" has_more={response_has_more}" if response_has_more is not None else ""
            gap_txt = ""
            if total is not None:
                gap_txt = f" / reported {total}"
            print(f"[LOAD MORE][{mode.upper()}] {before} -> {after}{gap_txt}{page_txt}{hm_txt}")
            miss_retries = 0 if (after > before or added > 0) else miss_retries + 1

            # Fast-path: the response generated by this click is terminal.
            if response_has_more is False:
                capture.capture_mode = "idle"
                return True
        else:
            # The control can be transiently outside the sidebar viewport or be
            # replaced during React re-render. Scroll to bottom, wait, and retry.
            await find_sidebar_scroll_info(page, "bottom")
            await page.wait_for_timeout(900)
            clicked, added, page_no, response_has_more = await click_load_more_once(page, capture)
            after = len(capture.current_list_ids)
            if clicked:
                total, has_more, last_page = state()
                print(
                    f"[LOAD MORE][{mode.upper()}] {before} -> {after}" +
                    (f" / reported {total}" if total is not None else "") +
                    (f" page={page_no}" if page_no is not None else "") +
                    (f" has_more={response_has_more}" if response_has_more is not None else "")
                )
                miss_retries = 0 if (after > before or added > 0) else miss_retries + 1
                if response_has_more is False:
                    capture.capture_mode = "idle"
                    return True
            else:
                miss_retries += 1
                total, has_more, last_page = state()
                report_key = (total, has_more, last_page, after)
                if report_key != last_reported:
                    print(
                        f"[WAIT][{mode.upper()}] Load More not available; captured {after}" +
                        (f" / reported {total}" if total is not None else "") +
                        (f" page={last_page}" if last_page is not None else "") +
                        (f" has_more={has_more}" if has_more is not None else "")
                    )
                    last_reported = report_key
                await page.wait_for_timeout(1200)

        if miss_retries >= max_miss_retries:
            break

    capture.capture_mode = "idle"
    _total, has_more, _last_page = state()
    return has_more is False

def save_master_snapshot(capture: CaptureState) -> None:
    snapshot = {
        "target_date": capture.target_date,
        "totalrecords_all": capture.totalrecords_all,
        "disliked_tracking_enabled": False,
        "master_conversations": list(capture.master_all.values()),
    }
    json_dump(capture.root / "master_conversations.json", snapshot)


async def click_visible_conversation_rows(page: Page, capture: CaptureState, db: StateDB) -> Tuple[int, int]:
    """Traverse the left list and click timestamp rows. Returns (newly_fetched, failures)."""
    capture.capture_mode = "details"
    await find_sidebar_scroll_info(page, "top")
    await page.wait_for_timeout(1000)

    already = db.fetched_ids()
    target = set(capture.master_all)
    new_count = 0
    failures = 0
    seen_ui_keys: Set[str] = set()
    stable_rounds = 0
    previous_fetched = len(already)

    timestamp_locator = page.locator("text=/\\d{2}-\\d{2}-\\d{4}\\s+\\d{2}:\\d{2}:\\d{2}/")

    for _round in range(800):
        # Process visible timestamp-like items in the left side.
        count = await timestamp_locator.count()
        visible_candidates = []
        for i in range(count):
            el = timestamp_locator.nth(i)
            try:
                if not await el.is_visible():
                    continue
                box = await el.bounding_box()
                if not box or box["x"] > 420 or box["y"] < 80:
                    continue
                text = (await el.inner_text()).strip()
                # Use a little parent text to disambiguate duplicate timestamps.
                parent_text = await el.evaluate("el => (el.parentElement && el.parentElement.innerText) ? el.parentElement.innerText : el.innerText")
                key = f"{text}|{parent_text[:250]}"
                if key in seen_ui_keys:
                    continue
                visible_candidates.append((el, key))
            except Exception:
                continue

        for el, key in visible_candidates:
            seen_ui_keys.add(key)
            try:
                async with page.expect_response(
                    lambda r: GET_MESSAGES_TOKEN in r.url.lower() and r.request.method.upper() == "POST",
                    timeout=12000,
                ) as resp_info:
                    await el.click(timeout=7000)
                resp = await resp_info.value
                payload = await resp.json()
                data = payload.get("data") or {}
                cid = data.get("conversation_id")
                if not cid:
                    continue
                if cid not in target:
                    # Ignore right-side or stale item outside current master snapshot.
                    continue
                if cid in already:
                    continue
                await persist_detail(capture, db, cid, payload)
                already.add(cid)
                new_count += 1
                print(f"[DETAIL] {len(already)}/{len(target)}  {cid}")
            except PlaywrightTimeoutError:
                # Selected/current row may not fire a new request. Non-fatal.
                continue
            except Exception as e:
                failures += 1
                print(f"[WARN] Failed clicking a row: {e}")

        if target.issubset(already):
            break

        info = await find_sidebar_scroll_info(page, "next")
        await page.wait_for_timeout(650)

        now = len(already)
        if now == previous_fetched:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_fetched = now

        # At list bottom with no progress: stop and report missing IDs.
        if info:
            at_bottom = info["scrollTop"] + info["clientHeight"] >= info["scrollHeight"] - 3
            if at_bottom and stable_rounds >= 5:
                break
        elif stable_rounds >= 5:
            break

    capture.capture_mode = "idle"
    return new_count, failures


async def persist_detail(capture: CaptureState, db: StateDB, cid: str, payload: Dict[str, Any]) -> None:
    master = capture.master_all.get(cid) or {}
    # MVP 1.5 no longer spends a full pagination pass resolving Disliked=Yes.
    # Preserve a known value from an older/resumed state DB, otherwise leave it unknown.
    is_disliked = db.get_dislike_status(cid)

    raw_path = capture.root / "raw" / f"{cid}.json"
    norm_path = capture.root / "normalized" / f"{cid}.json"
    json_dump(raw_path, payload)

    normalized = normalize_messages(master, payload, is_disliked)
    asset_root = capture.root / "assets" / cid
    local_assets: List[Dict[str, Any]] = []

    # Download user and assistant media immediately; signed URLs can expire.
    for turn in normalized.get("turns", []):
        ti = turn["turn_index"]
        for mi, url in enumerate(turn["user"].get("media_urls") or [], start=1):
            if not isinstance(url, str):
                continue
            kind = media_kind(url, turn["user"].get("type", ""))
            if kind == "video":
                continue
            try:
                dest = await asyncio.to_thread(download_url, url, asset_root / f"turn_{ti:03d}_user_{mi:02d}")
                local_assets.append({"turn_index": ti, "role": "user", "url": url, "local_path": str(dest)})
            except Exception as e:
                local_assets.append({"turn_index": ti, "role": "user", "url": url, "error": str(e)})

        for ri, resp in enumerate(turn["assistant"].get("responses") or [], start=1):
            for mi, url in enumerate(resp.get("media_urls") or [], start=1):
                if not isinstance(url, str):
                    continue
                kind = media_kind(url, resp.get("type", ""))
                if kind == "video":
                    continue
                try:
                    dest = await asyncio.to_thread(download_url, url, asset_root / f"turn_{ti:03d}_assistant_{ri:02d}_{mi:02d}")
                    local_assets.append({"turn_index": ti, "role": "assistant", "response_index": ri, "url": url, "local_path": str(dest)})
                except Exception as e:
                    local_assets.append({"turn_index": ti, "role": "assistant", "response_index": ri, "url": url, "error": str(e)})

    normalized["local_assets"] = local_assets
    json_dump(norm_path, normalized)
    db.mark_detail(cid, raw_path, norm_path, bool(normalized.get("contains_video")))



MONTH_NAME_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$",
    re.I,
)


def parse_cli_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def iter_dates(start: date, end: date) -> List[date]:
    if end < start:
        raise ValueError("end date must be on/after start date")
    out: List[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def expected_api_window(target: date) -> Tuple[str, str]:
    """Expected backend UTC window for one UI/WIB calendar day.

    Dimensions was observed to translate:
      2026-09-28 00:00 WIB -> 2026-09-27 17:00:00 UTC
      2026-09-28 23:59 WIB -> 2026-09-28 16:59:00 UTC
    """
    start_local = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=JAKARTA)
    end_local = datetime(target.year, target.month, target.day, 23, 59, 0, tzinfo=JAKARTA)
    return (
        start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


def recursively_find_key(obj: Any, wanted: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == wanted.lower():
                found.append(v)
            found.extend(recursively_find_key(v, wanted))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(recursively_find_key(item, wanted))
    return found


def request_json(request) -> Optional[Dict[str, Any]]:
    try:
        obj = request.post_data_json
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        raw = request.post_data
        if raw:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return None


def verify_filter_request(request, target: date) -> Tuple[bool, str]:
    body = request_json(request)
    if not body:
        return False, "Could not parse getconversations request body."

    expected_start, expected_end = expected_api_window(target)
    starts = [str(x) for x in recursively_find_key(body, "startdate") if x is not None]
    ends = [str(x) for x in recursively_find_key(body, "enddate") if x is not None]
    actions = [str(x) for x in recursively_find_key(body, "action") if x is not None]

    if not starts or not ends:
        return False, (
            f"Request body did not expose startdate/enddate. "
            f"Expected {expected_start} -> {expected_end}."
        )

    start_ok = expected_start in starts
    end_ok = expected_end in ends
    action_note = ""
    if actions:
        action_note = f" action={actions[0]}"
    action_ok = (not actions) or ("3" in actions)
    if start_ok and end_ok and action_ok:
        return True, (
            f"verified API window {expected_start} -> {expected_end}{action_note}"
        )
    if start_ok and end_ok and not action_ok:
        return False, (
            f"Date window is correct, but Disliked filter action is not All. "
            f"Expected action=3, actual action={actions[:3]}."
        )
    return False, (
        f"API date mismatch. expected={expected_start} -> {expected_end}; "
        f"actual start={starts[:3]} end={ends[:3]}{action_note}"
    )


def summary_is_complete(
    path: Path,
    min_detail_coverage: float,
    max_missing_details: int,
) -> bool:
    """Return True when an existing date is safe to skip on resume.

    MVP 1.5.2 uses hybrid acceptance:
      HARD gates must always pass:
        - pagination reached has_more=False
        - no DB extraction errors
        - no row click failures

      SOFT detail coverage:
        - fetched / frozen snapshot >= min_detail_coverage
        - missing count <= max_missing_details (unless negative = disabled)

    This lets a date continue when a very small number of frozen conversations
    disappear after the initial snapshot (for example because they were manually
    reviewed/submitted), without weakening pagination/date integrity checks.
    """
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    hard_ok = (
        data.get("pagination_complete_all") is True
        and data.get("final_has_more_all") is False
        and int(data.get("errors_in_db") or 0) == 0
        and int(data.get("click_failures") or 0) == 0
    )
    if not hard_ok:
        return False

    active = int(data.get("active_snapshot_count") or 0)
    fetched = int(data.get("active_details_fetched") or data.get("details_after_run_active") or 0)
    missing = int(data.get("missing_from_active_snapshot") or 0)

    coverage = 1.0 if active == 0 else fetched / active
    missing_ok = max_missing_details < 0 or missing <= max_missing_details

    return coverage >= min_detail_coverage and missing_ok


def dates_to_process(
    dates: List[date],
    output_base: Path,
    min_detail_coverage: float,
    max_missing_details: int,
    *,
    rescan: bool,
    retry_missing: bool,
) -> Tuple[List[date], Dict[str, Dict[str, Any]]]:
    """Select dates requiring browser work before Playwright starts.

    Normal backfills skip accepted snapshots. Rescan deliberately refreshes a
    live Dimensions date; retry-missing only opens dates with unresolved IDs.
    """
    pending: List[date] = []
    skipped: Dict[str, Dict[str, Any]] = {}
    for target in dates:
        out_root = output_base / target.isoformat()
        summary_path = out_root / "summary.json"
        missing_path = out_root / "missing_conversation_ids.json"
        if rescan:
            pending.append(target)
            continue
        if retry_missing:
            if missing_path.exists():
                pending.append(target)
            else:
                skipped[target.isoformat()] = {
                    "status": "skipped_no_missing",
                    "summary": str(summary_path) if summary_path.exists() else None,
                }
            continue
        if summary_is_complete(summary_path, min_detail_coverage, max_missing_details):
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
                existing_status = existing.get("acceptance_status", "complete")
            except Exception:
                existing_status = "complete"
            skipped[target.isoformat()] = {
                "status": (
                    "skipped_accepted_with_gaps"
                    if existing_status == "accepted_with_gaps"
                    else "skipped_complete"
                ),
                "summary": str(summary_path),
            }
            continue
        pending.append(target)
    return pending, skipped


async def save_ui_debug(page: Page, output_root: Path, target: date, label: str) -> None:
    debug = output_root / "_mvp1_5_2_debug"
    debug.mkdir(parents=True, exist_ok=True)
    stem = f"{target.isoformat()}_{label}"
    try:
        await page.screenshot(path=str(debug / f"{stem}.png"), full_page=True)
    except Exception:
        pass
    try:
        (debug / f"{stem}.html").write_text(await page.content(), encoding="utf-8")
    except Exception:
        pass


async def initial_filter_modal_visible(page: Page) -> bool:
    """Detect the first-load 'Conversation Filter' modal shown by Dimensions."""
    try:
        title = page.get_by_text("Conversation Filter", exact=True)
        for i in range(await title.count()):
            if await title.nth(i).is_visible():
                return True
    except Exception:
        pass
    return False


async def ensure_filter_surface_open(page: Page) -> str:
    """Return 'initial_modal' or 'side_panel'.

    On first entry to Guardrail Identify, Dimensions immediately shows a
    Conversation Filter modal. After the first submission, later dates use the
    normal filter button / side panel.
    """
    if await initial_filter_modal_visible(page):
        return "initial_modal"

    await ensure_filter_panel_open(page)
    return "side_panel"


async def ensure_filter_panel_open(page: Page) -> None:
    # If Clear Filters is visible, the filter panel is already open.
    try:
        clear = page.get_by_text("Clear Filters", exact=True)
        for i in range(await clear.count()):
            if await clear.nth(i).is_visible():
                return
    except Exception:
        pass

    # First prefer accessible Filter buttons.
    candidates = [
        page.get_by_role("button", name=re.compile(r"filter", re.I)),
        page.locator('[aria-label*="filter" i]'),
        page.locator('[title*="filter" i]'),
    ]
    for loc in candidates:
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(timeout=5000)
                    await page.wait_for_timeout(400)
                    if await page.get_by_text("Clear Filters", exact=True).is_visible():
                        return
        except Exception:
            continue

    # Dimensions currently exposes an icon-only filter control in the upper-left
    # conversation header. Use a geometry fallback rather than fixed pixels.
    clicked = await page.evaluate(
        """
        () => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const els = [...document.querySelectorAll('button,[role="button"]')]
            .filter(visible)
            .map(el => ({el, r: el.getBoundingClientRect()}))
            .filter(x => x.r.left >= 180 && x.r.right <= 340 && x.r.top >= 45 && x.r.bottom <= 145)
            .sort((a,b) => b.r.left - a.r.left);
          if (!els.length) return false;
          els[0].el.click();
          return true;
        }
        """
    )
    if clicked:
        await page.wait_for_timeout(500)
        try:
            if await page.get_by_text("Clear Filters", exact=True).is_visible():
                return
        except Exception:
            pass

    raise RuntimeError("Could not open the Dimensions Filter panel automatically.")


async def clear_filter_form(page: Page) -> None:
    """Clear only free-text filters; never click Clear Filters.

    Dimensions can restore its own default filter state when "Clear Filters" is
    clicked. In some sessions that can make Disliked appear as Yes. The
    extractor therefore clears only the two text fields directly and leaves the
    segmented filters alone.
    """
    for placeholder in ("Search conversations...", "Enter conversation ID..."):
        try:
            el = page.get_by_placeholder(placeholder)
            if await el.count() and await el.first.is_visible():
                await el.first.fill("")
        except Exception:
            pass


async def click_option_near_label(
    page: Page,
    label_text: str,
    option_text: str,
    *,
    required: bool = True,
) -> bool:
    """Click a segmented/filter option located directly below a field label.

    This avoids ambiguity when the page contains more than one "All" option.
    """
    result = await page.evaluate(
        """
        ({labelText, optionText}) => {
          const visible = el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 &&
                   s.display !== 'none' && s.visibility !== 'hidden';
          };

          const labels = [...document.querySelectorAll('*')]
            .filter(el => visible(el) && (el.textContent || '').trim() === labelText)
            .map(el => ({el, r: el.getBoundingClientRect()}))
            .filter(x => x.r.left < window.innerWidth * 0.75)
            .sort((a,b) => a.r.top - b.r.top);

          if (!labels.length) return {ok:false, reason:'label_not_found'};

          for (const label of labels) {
            const lr = label.r;
            const candidates = [...document.querySelectorAll('button,[role="button"],div,span')]
              .filter(visible)
              .map(el => ({
                el,
                r: el.getBoundingClientRect(),
                text: (el.textContent || '').trim()
              }))
              .filter(x =>
                x.text === optionText &&
                x.r.top >= lr.bottom - 5 &&
                x.r.top <= lr.bottom + 90 &&
                x.r.left >= lr.left - 30 &&
                x.r.left <= lr.left + 520 &&
                x.r.width >= 25 &&
                x.r.height >= 20
              )
              .sort((a,b) => {
                const ad = Math.abs(a.r.top - lr.bottom) + Math.abs(a.r.left - lr.left);
                const bd = Math.abs(b.r.top - lr.bottom) + Math.abs(b.r.left - lr.left);
                return ad - bd;
              });

            if (candidates.length) {
              candidates[0].el.click();
              return {ok:true};
            }
          }
          return {ok:false, reason:'option_not_found'};
        }
        """,
        {"labelText": label_text, "optionText": option_text},
    )
    ok = bool(result and result.get("ok"))
    if not ok and required:
        raise RuntimeError(
            f"Could not select {label_text}={option_text} on the current filter surface."
        )
    if ok:
        await page.wait_for_timeout(180)
    return ok


async def ensure_filter_defaults(page: Page, surface: str) -> None:
    """Enforce the monitoring filters used by MVP 1.5.2."""
    # Initial modal defaults can be Yes (observed in the real Dimensions UI),
    # so Disliked=All must always be selected explicitly.
    await click_option_near_label(page, "Disliked", "All", required=True)

    # Flagged exists in the normal side panel but not in the first-load modal.
    if surface == "side_panel":
        await click_option_near_label(page, "Flagged", "All", required=False)

    # Text filters only exist in the normal side panel.
    if surface == "side_panel":
        for placeholder in ("Search conversations...", "Enter conversation ID..."):
            try:
                el = page.get_by_placeholder(placeholder)
                if await el.count() and await el.first.is_visible():
                    await el.first.fill("")
            except Exception:
                pass


async def click_date_control(page: Page) -> None:
    # Fast path: visible date-like value.
    # Supports both:
    #   "Sep 01, 2026"
    # and the initial-modal format:
    #   "Oct 02, 2026 00:00 - 23:59"
    date_like = page.locator(
        "text=/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{2},\\s+\\d{4}(?:\\s+\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2})?$/"
    )
    try:
        for i in range(await date_like.count()):
            el = date_like.nth(i)
            if not await el.is_visible():
                continue
            box = await el.bounding_box()
            if box and box["x"] < 700:
                await el.click(timeout=5000)
                await page.wait_for_timeout(350)
                return
    except Exception:
        pass

    # Generic relation-to-label fallback.
    clicked = await page.evaluate(
        """
        () => {
          const visible = el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
          };
          const labels = [...document.querySelectorAll('*')]
            .filter(el => visible(el) && ['Date','Date & Time Range'].includes((el.textContent || '').trim()))
            .map(el => ({el, r: el.getBoundingClientRect()}))
            .filter(x => x.r.left < 700 && x.r.top < 650)
            .sort((a,b) => a.r.left - b.r.left);
          if (!labels.length) return false;
          const lr = labels[0].r;
          const candidates = [...document.querySelectorAll('input,button,[role="button"],div')]
            .filter(visible)
            .map(el => ({el, r: el.getBoundingClientRect(), txt:(el.innerText || el.value || '').trim()}))
            .filter(x =>
              x.r.left >= lr.left - 10 &&
              x.r.left <= lr.left + 320 &&
              x.r.top >= lr.bottom - 5 &&
              x.r.top <= lr.bottom + 90 &&
              x.r.width >= 120
            )
            .sort((a,b) => {
              const ad = Math.abs(a.r.top - lr.bottom) + Math.abs(a.r.left - lr.left);
              const bd = Math.abs(b.r.top - lr.bottom) + Math.abs(b.r.left - lr.left);
              return ad - bd;
            });
          if (!candidates.length) return false;
          candidates[0].el.click();
          return true;
        }
        """
    )
    if not clicked:
        raise RuntimeError("Could not locate the Date control inside the filter panel.")
    await page.wait_for_timeout(350)


async def visible_calendar_month(page: Page) -> Tuple[str, int, int]:
    """Return ('Month YYYY', month_number, year) for the visible date picker."""
    data = await page.evaluate(
        """
        () => {
          const re = /^(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i;
          const visible = el => {
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
          };
          const hits = [...document.querySelectorAll('*')]
            .filter(el => visible(el) && re.test((el.textContent||'').trim()))
            .map(el => {
              const r=el.getBoundingClientRect();
              return {text:(el.textContent||'').trim(), x:r.x, y:r.y, w:r.width, h:r.height};
            })
            .filter(x => x.w < 400 && x.h < 100)
            .sort((a,b) => a.y-b.y);
          return hits.length ? hits[0] : null;
        }
        """
    )
    if not data:
        raise RuntimeError("Date picker opened, but current calendar month/year could not be detected.")
    m = MONTH_NAME_RE.match(data["text"])
    if not m:
        raise RuntimeError(f"Could not parse calendar month header: {data['text']}")
    month_num = list(calendar.month_name).index(m.group(1).capitalize())
    return data["text"], month_num, int(m.group(2))


async def click_calendar_nav(page: Page, direction: str) -> None:
    assert direction in {"prev", "next"}

    # Accessibility-first.
    regex = re.compile(r"(previous|prev)" if direction == "prev" else r"(next)", re.I)
    for loc in (
        page.get_by_role("button", name=regex),
        page.locator('[aria-label*="previous" i]' if direction == "prev" else '[aria-label*="next" i]'),
        page.locator('[title*="previous" i]' if direction == "prev" else '[title*="next" i]'),
    ):
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(timeout=4000)
                    await page.wait_for_timeout(180)
                    return
        except Exception:
            continue

    # Geometry fallback: use buttons immediately left/right of the detected
    # "Month YYYY" header. This avoids hard-coded screen coordinates.
    header = await page.evaluate(
        """
        () => {
          const re=/^(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d{4}$/i;
          const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
          const el=[...document.querySelectorAll('*')].find(el=>vis(el)&&re.test((el.textContent||'').trim()));
          if(!el) return null;
          const r=el.getBoundingClientRect();
          return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,cy:(r.top+r.bottom)/2};
        }
        """
    )
    if not header:
        raise RuntimeError("Could not locate calendar header for month navigation.")

    clicked = await page.evaluate(
        """
        ({header,direction}) => {
          const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
          const cand=[...document.querySelectorAll('button,[role="button"]')]
            .filter(vis)
            .map(el=>({el,r:el.getBoundingClientRect()}))
            .filter(x => Math.abs(((x.r.top+x.r.bottom)/2)-header.cy) < 45 && x.r.width < 90 && x.r.height < 90)
            .filter(x => direction==='prev' ? x.r.right <= header.left+20 : x.r.left >= header.right-20)
            .sort((a,b)=>{
              const da = direction==='prev' ? Math.abs(header.left-a.r.right) : Math.abs(a.r.left-header.right);
              const db = direction==='prev' ? Math.abs(header.left-b.r.right) : Math.abs(b.r.left-header.right);
              return da-db;
            });
          if(!cand.length) return false;
          cand[0].el.click();
          return true;
        }
        """,
        {"header": header, "direction": direction},
    )
    if not clicked:
        raise RuntimeError(f"Could not click calendar {direction} navigation button.")
    await page.wait_for_timeout(180)


async def navigate_calendar_to(page: Page, target: date) -> None:
    for _ in range(48):
        _label, current_month, current_year = await visible_calendar_month(page)
        diff = (target.year - current_year) * 12 + (target.month - current_month)
        if diff == 0:
            return
        await click_calendar_nav(page, "next" if diff > 0 else "prev")
    raise RuntimeError(f"Calendar navigation exceeded safety limit while seeking {target:%B %Y}.")


async def click_calendar_day(page: Page, target: date) -> None:
    """Click the target day by matching its weekday column.

    This avoids accidentally clicking the same numeric day from the previous or
    next month that many calendar widgets render in a muted style.
    """
    # Calendar shown by Dimensions is Sunday-first ("Su Mo Tu We Th Fr Sa").
    weekday_labels = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
    target_col = (target.weekday() + 1) % 7  # Python Monday=0 -> Sunday=0
    wanted_weekday = weekday_labels[target_col]

    geometry = await page.evaluate(
        """
        ({day,wantedWeekday}) => {
          const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
          const all=[...document.querySelectorAll('*')].filter(vis);
          const weekdayHits=all
            .filter(el => (el.textContent||'').trim()===wantedWeekday)
            .map(el=>{const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,cx:(r.left+r.right)/2,cy:(r.top+r.bottom)/2}})
            .filter(x=>x.w<100&&x.h<80)
            .sort((a,b)=>a.y-b.y);
          if(!weekdayHits.length) return null;
          // Prefer the weekday header that is part of the currently visible calendar,
          // usually the lowest one near the month header.
          const wh=weekdayHits[weekdayHits.length-1];
          const candidates=all
            .filter(el => (el.textContent||'').trim()===String(day))
            .map(el=>{const r=el.getBoundingClientRect(); return {el,r,cx:(r.left+r.right)/2,cy:(r.top+r.bottom)/2,area:r.width*r.height}})
            .filter(x => x.r.width>0 && x.r.width<90 && x.r.height>0 && x.r.height<90)
            .filter(x => x.cy > wh.cy+12 && x.cy < wh.cy+330)
            .map(x => ({...x, score: Math.abs(x.cx-wh.cx)}))
            .sort((a,b)=>a.score-b.score || a.area-b.area);
          if(!candidates.length) return null;
          const c=candidates[0];
          return {x:c.cx,y:c.cy,score:c.score};
        }
        """,
        {"day": target.day, "wantedWeekday": wanted_weekday},
    )
    if not geometry or geometry["score"] > 45:
        raise RuntimeError(
            f"Could not safely identify calendar day {target.day} in weekday column {wanted_weekday}."
        )
    await page.mouse.click(geometry["x"], geometry["y"])
    await page.wait_for_timeout(180)


async def close_date_picker_with_ok(page: Page) -> None:
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*OK\s*$", re.I)),
        page.get_by_text("OK", exact=True),
    ]
    for loc in candidates:
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(timeout=5000)
                    await page.wait_for_timeout(300)
                    return
        except Exception:
            continue
    raise RuntimeError("Could not click the date picker's OK button.")


async def click_apply_and_capture_first_page(
    page: Page,
    capture: CaptureState,
    target: date,
) -> Tuple[Dict[str, Any], str]:
    capture.master_all.clear()
    capture.current_list_ids.clear()
    capture.raw_details.clear()
    capture.totalrecords_all = None
    capture.last_page_all = None
    capture.has_more_all = None
    capture.capture_mode = "all"

    # First-load modal uses "Submit"; the normal side filter uses "Apply".
    apply_candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*(Apply|Submit)\s*$", re.I)),
        page.get_by_text("Apply", exact=True),
        page.get_by_text("Submit", exact=True),
    ]
    apply_button = None
    for loc in apply_candidates:
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    apply_button = el
                    break
        except Exception:
            continue
        if apply_button is not None:
            break
    if apply_button is None:
        raise RuntimeError("Could not locate the filter Apply/Submit button.")

    async with page.expect_response(
        lambda r: GET_CONVERSATIONS_TOKEN in r.url.lower()
        and r.request.method.upper() == "POST",
        timeout=20000,
    ) as info:
        await apply_button.click(timeout=7000)

    response = await info.value
    payload = await response.json()
    # Consume synchronously; the page listener may consume the same response too.
    capture._consume_conversation_list(payload)

    ok, message = verify_filter_request(response.request, target)
    if not ok:
        capture.capture_mode = "idle"
        raise RuntimeError(message)

    await page.wait_for_timeout(700)
    return payload, message


async def autonomously_apply_date_filter(
    page: Page,
    capture: CaptureState,
    target: date,
    output_root: Path,
) -> None:
    """Set one exact Dimensions UI date and verify the emitted API window."""
    last_error: Optional[Exception] = None
    for attempt in range(1, 3):
        try:
            capture.capture_mode = "idle"

            surface = await ensure_filter_surface_open(page)
            print(f"[FILTER] {target.isoformat()} using {surface}")

            # The side panel can safely use Clear Filters. The initial modal has
            # no Clear Filters control and often opens with Disliked=Yes.
            if surface == "side_panel":
                await clear_filter_form(page)
                await page.wait_for_timeout(250)

            await ensure_filter_defaults(page, surface)

            await click_date_control(page)
            await navigate_calendar_to(page, target)
            await click_calendar_day(page, target)
            await close_date_picker_with_ok(page)

            # Final guard: the script must never submit with Disliked=Yes/No.
            # Re-assert All immediately before Apply/Submit.
            await click_option_near_label(page, "Disliked", "All", required=True)

            _payload, verification = await click_apply_and_capture_first_page(
                page, capture, target
            )
            print(f"[DATE VERIFIED] {target.isoformat()} | {verification}")
            return
        except Exception as e:
            last_error = e
            print(f"[DATE SET][attempt {attempt}/2] {target.isoformat()} failed: {e}")
            await save_ui_debug(page, output_root, target, f"attempt_{attempt}")
            # Escape a possibly-open date picker/filter state before retrying.
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
            except Exception:
                pass

    raise RuntimeError(
        f"Could not autonomously set/verify Dimensions date {target.isoformat()}: {last_error}"
    )


async def process_one_date(
    page: Page,
    active_capture: Dict[str, Optional[CaptureState]],
    output_base: Path,
    target: date,
    min_detail_coverage: float,
    max_missing_details: int,
    scan_mode: str,
) -> Tuple[bool, Dict[str, Any]]:
    out_root = output_base / target.isoformat()
    out_root.mkdir(parents=True, exist_ok=True)
    db = StateDB(out_root / "state.db")
    capture = CaptureState(root=out_root, target_date=target.isoformat())
    active_capture["value"] = capture

    try:
        print("\n" + "=" * 78)
        print(f"[DATE] {target.isoformat()}")
        print(f"[OUT ] {out_root}")
        print("=" * 78)

        await autonomously_apply_date_filter(page, capture, target, output_base)

        pagination_complete_all = await exhaust_current_list(page, capture, "all")
        snapshot_captured_at = datetime.now(timezone.utc).isoformat()
        snapshot_ids = set(capture.master_all)
        reported_all = capture.totalrecords_all
        final_page_all = capture.last_page_all
        final_has_more_all = capture.has_more_all

        print(
            f"Captured active ALL: {len(snapshot_ids)}"
            + (f" / reported {reported_all}" if reported_all is not None else "")
            + f" | final_page={final_page_all} has_more={final_has_more_all}"
        )

        save_master_snapshot(capture)

        if not pagination_complete_all or final_has_more_all is not False:
            raise RuntimeError(
                "ALL pagination did not reach terminal has_more=False."
            )

        # A date with genuinely zero conversations is valid and should not block
        # an unattended range run.
        if not snapshot_ids and int(reported_all or 0) == 0:
            summary = {
                "target_date": target.isoformat(),
                "active_snapshot_count": 0,
                "active_details_fetched": 0,
                "missing_from_active_snapshot": 0,
                "disliked_tracking_enabled": False,
                "disliked_in_active_snapshot": None,
                "videos_in_db": 0,
                "errors_in_db": 0,
                "reported_totalrecords_all": reported_all,
                "reported_vs_active_gap": 0,
                "pagination_complete_all": True,
                "final_page_all": final_page_all,
                "final_has_more_all": False,
                "details_before_run_active": 0,
                "details_new_this_run": 0,
                "details_after_run_active": 0,
                "click_failures": 0,
                "db_total_rows": 0,
                "db_fetched_rows": 0,
                "detail_coverage_ratio": 1.0,
                "detail_coverage_pct": 100.0,
                "min_detail_coverage_ratio": min_detail_coverage,
                "min_detail_coverage_pct": round(min_detail_coverage * 100, 2),
                "max_missing_details": max_missing_details,
                "hard_checks_passed": True,
                "coverage_check_passed": True,
                "missing_count_check_passed": True,
                "acceptance_status": "complete",
                "scan_mode": scan_mode,
                "snapshot_captured_at_utc": snapshot_captured_at,
            }
            json_dump(out_root / "summary.json", summary)
            print(f"[OK] {target.isoformat()} has 0 conversations. Marked complete.")
            return True, summary

        if not snapshot_ids:
            raise RuntimeError(
                "No conversation IDs were captured even though the backend did not report a clean zero-result day."
            )

        db.seed_master(
            capture.master_all,
            disliked_ids=None,
            dislike_tracking_enabled=False,
        )

        await find_sidebar_scroll_info(page, "top")
        await page.wait_for_timeout(700)

        before_set = db.fetched_ids()
        before_active = len(snapshot_ids & before_set)
        new_count, click_failures = await click_visible_conversation_rows(
            page, capture, db
        )

        after_set = db.fetched_ids()
        after_active = len(snapshot_ids & after_set)
        missing = sorted(snapshot_ids - after_set)

        save_master_snapshot(capture)
        if missing:
            json_dump(out_root / "missing_conversation_ids.json", missing)
        else:
            stale = out_root / "missing_conversation_ids.json"
            if stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    pass

        db_summary = db.summary()

        # Hybrid acceptance:
        # - never relax structural/integrity checks;
        # - tolerate a bounded number of missing detail snapshots.
        active_count = len(snapshot_ids)
        detail_coverage = 1.0 if active_count == 0 else after_active / active_count

        hard_checks_passed = (
            pagination_complete_all
            and final_has_more_all is False
            and db_summary["errors"] == 0
            and click_failures == 0
        )
        coverage_check_passed = detail_coverage >= min_detail_coverage
        missing_count_check_passed = (
            max_missing_details < 0 or len(missing) <= max_missing_details
        )

        passed = (
            hard_checks_passed
            and coverage_check_passed
            and missing_count_check_passed
        )

        if passed and len(missing) == 0:
            acceptance_status = "complete"
        elif passed:
            acceptance_status = "accepted_with_gaps"
        else:
            acceptance_status = "failed_acceptance"

        summary = {
            "target_date": target.isoformat(),
            "active_snapshot_count": active_count,
            "active_details_fetched": after_active,
            "missing_from_active_snapshot": len(missing),
            "disliked_tracking_enabled": False,
            "disliked_in_active_snapshot": None,
            "videos_in_db": db_summary["videos"],
            "errors_in_db": db_summary["errors"],
            "reported_totalrecords_all": reported_all,
            "reported_vs_active_gap": (
                reported_all - active_count
                if reported_all is not None
                else None
            ),
            "pagination_complete_all": pagination_complete_all,
            "final_page_all": final_page_all,
            "final_has_more_all": final_has_more_all,
            "details_before_run_active": before_active,
            "details_new_this_run": new_count,
            "details_after_run_active": after_active,
            "click_failures": click_failures,
            "db_total_rows": db_summary["total"],
            "db_fetched_rows": db_summary["fetched"],
            "detail_coverage_ratio": round(detail_coverage, 6),
            "detail_coverage_pct": round(detail_coverage * 100, 2),
            "min_detail_coverage_ratio": min_detail_coverage,
            "min_detail_coverage_pct": round(min_detail_coverage * 100, 2),
            "max_missing_details": max_missing_details,
            "hard_checks_passed": hard_checks_passed,
            "coverage_check_passed": coverage_check_passed,
            "missing_count_check_passed": missing_count_check_passed,
            "acceptance_status": acceptance_status,
            "scan_mode": scan_mode,
            "snapshot_captured_at_utc": snapshot_captured_at,
        }
        json_dump(out_root / "summary.json", summary)

        print(f"\n=== SUMMARY {target.isoformat()} ===")
        for k, v in summary.items():
            print(f"{k:36s}: {v}")

        if acceptance_status == "complete":
            print(f"[OK] {target.isoformat()} extraction complete (100% detail coverage).")
        elif acceptance_status == "accepted_with_gaps":
            print(
                f"[ACCEPTED WITH GAPS] {target.isoformat()} detail coverage "
                f"{detail_coverage * 100:.2f}% ({after_active}/{active_count}); "
                f"{len(missing)} frozen conversation(s) unresolved. "
                "Range will continue."
            )
            print(
                f"[INFO] Missing IDs remain saved in {out_root / 'missing_conversation_ids.json'} "
                "for audit/retry."
            )
        else:
            print(f"[NOT READY] {target.isoformat()} did not pass acceptance checks.")
            if not hard_checks_passed:
                print("[FAIL] One or more HARD checks failed (pagination/errors/click integrity).")
            if not coverage_check_passed:
                print(
                    f"[FAIL] Detail coverage {detail_coverage * 100:.2f}% is below "
                    f"minimum {min_detail_coverage * 100:.2f}%."
                )
            if not missing_count_check_passed:
                print(
                    f"[FAIL] Missing detail count {len(missing)} exceeds "
                    f"maximum allowed {max_missing_details}."
                )

        return passed, summary
    finally:
        active_capture["value"] = None
        db.close()


async def main_async(args: argparse.Namespace) -> int:
    output_base = Path(args.output_dir).resolve()
    output_base.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    if args.date:
        dates = [parse_cli_date(args.date)]
    else:
        start = parse_cli_date(args.start_date)
        end = parse_cli_date(args.end_date)
        dates = iter_dates(start, end)

    requested_dates = dates
    dates, skipped_before_browser = dates_to_process(
        requested_dates,
        output_base,
        args.min_detail_coverage,
        args.max_missing_details,
        rescan=args.rescan,
        retry_missing=args.retry_missing,
    )
    scan_mode = "rescan" if args.rescan else ("retry_missing" if args.retry_missing else "normal")
    print("\n=== Dimensions Conversation Extractor — MVP 1.5.2 RANGE (READ ONLY) ===")
    print(f"Requested   : {requested_dates[0].isoformat()} -> {requested_dates[-1].isoformat()} ({len(requested_dates)} day(s))")
    print(f"To process  : {len(dates)} day(s) | Mode: {scan_mode}")
    print(f"Output root : {output_base}")
    print("Flow        : login/open CMS once -> autonomous date selection -> one ALL pagination/date -> detail extraction")
    print("Disliked    : not enumerated separately")
    print("Safety      : each UI date is verified against the emitted getconversations UTC window")
    print("This MVP NEVER calls submitreviews and never marks Safe/Unsafe.\n")

    range_status: Dict[str, Any] = {
        "start_date": requested_dates[0].isoformat(),
        "end_date": requested_dates[-1].isoformat(),
        "scan_mode": scan_mode,
        "days": skipped_before_browser,
    }

    if not dates:
        json_dump(output_base / "mvp1_5_2_range_status.json", range_status)
        print("[OK] No browser work needed. Requested dates already satisfy selected scan mode.")
        return 0

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1600, "height": 950},
            args=["--start-maximized"],
        )
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        active_capture: Dict[str, Optional[CaptureState]] = {"value": None}

        async def routed_response(response: Response) -> None:
            capture = active_capture.get("value")
            if capture is not None:
                await capture.handle_response(response)

        page.on("response", lambda r: asyncio.create_task(routed_response(r)))

        await page.goto(ADMIN_URL, wait_until="domcontentloaded")
        login_prompt = (
            "Browser opened. Login if needed, then open Sahabat Chat -> Conversation Verify -> "
            "Guardrail Identify. Wait for the Conversation Filter, then return to the app and click Continue."
            if args.control_file
            else
            "Browser opened. Login if needed, then open Guardrail Identify / Conversation Verifying. "
            "Wait until the initial 'Conversation Filter' modal is fully visible. "
            "Do NOT change Date or Disliked manually. Press ENTER ONCE here when that modal is ready... "
        )
        await wait_for_user_signal(args.control_file, "waiting_login", login_prompt)
        await page.wait_for_timeout(800)

        any_failure = False

        for index, target in enumerate(dates, start=1):
            out_root = output_base / target.isoformat()
            summary_path = out_root / "summary.json"
            missing_path = out_root / "missing_conversation_ids.json"

            if summary_is_complete(
                summary_path,
                args.min_detail_coverage,
                args.max_missing_details,
            ) and not (args.rescan or args.retry_missing):
                print(
                    f"\n[{index:02d}/{len(dates):02d}] {target.isoformat()} "
                    "[SKIP] existing summary already passes MVP 1 acceptance checks."
                )
                try:
                    existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    existing_status = existing_summary.get("acceptance_status", "complete")
                except Exception:
                    existing_status = "complete"
                range_status["days"][target.isoformat()] = {
                    "status": (
                        "skipped_accepted_with_gaps"
                        if existing_status == "accepted_with_gaps"
                        else "skipped_complete"
                    ),
                    "summary": str(summary_path),
                }
                json_dump(output_base / "mvp1_5_2_range_status.json", range_status)
                continue

            print(f"\n[{index:02d}/{len(dates):02d}] Processing {target.isoformat()}...")
            try:
                passed, summary = await process_one_date(
                    page=page,
                    active_capture=active_capture,
                    output_base=output_base,
                    target=target,
                    min_detail_coverage=args.min_detail_coverage,
                    max_missing_details=args.max_missing_details,
                    scan_mode=scan_mode,
                )
                range_status["days"][target.isoformat()] = {
                    "status": summary.get(
                        "acceptance_status",
                        "complete" if passed else "failed_acceptance",
                    ),
                    "summary": summary,
                }
                json_dump(output_base / "mvp1_5_2_range_status.json", range_status)

                if not passed:
                    any_failure = True
                    if not args.continue_on_error:
                        print("[STOP] Range halted because this date did not pass acceptance checks.")
                        break
            except Exception as e:
                any_failure = True
                range_status["days"][target.isoformat()] = {
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}",
                }
                json_dump(output_base / "mvp1_5_2_range_status.json", range_status)
                await save_ui_debug(page, output_base, target, "fatal")
                print(f"[ERROR] {target.isoformat()}: {type(e).__name__}: {e}")
                if not args.continue_on_error:
                    print("[STOP] Range halted to avoid silently skipping/badly labeling dates.")
                    break

        completed_statuses = {
            "complete",
            "accepted_with_gaps",
            "skipped_complete",
            "skipped_accepted_with_gaps",
            "skipped_no_missing",
        }
        completed = sum(
            1 for x in range_status["days"].values()
            if x.get("status") in completed_statuses
        )
        accepted_with_gaps = sum(
            1 for x in range_status["days"].values()
            if x.get("status") in {"accepted_with_gaps", "skipped_accepted_with_gaps"}
        )
        failed = sum(
            1 for x in range_status["days"].values()
            if x.get("status") not in completed_statuses
        )
        range_status["completed_or_skipped"] = completed
        range_status["accepted_with_gaps"] = accepted_with_gaps
        range_status["failed"] = failed
        json_dump(output_base / "mvp1_5_2_range_status.json", range_status)

        print("\n=== RANGE SUMMARY ===")
        print(f"Requested dates : {len(requested_dates)}")
        print(f"Processed dates : {len(dates)}")
        print(f"Complete/skip   : {completed}")
        print(f"With gaps       : {accepted_with_gaps}")
        print(f"Failed          : {failed}")
        print(f"Status file     : {output_base / 'mvp1_5_2_range_status.json'}")

        await wait_for_user_signal(
            args.control_file,
            "waiting_close",
            "Range run finished/stopped. Confirm when the extraction browser can close.",
        )
        await context.close()
        if args.control_file:
            write_control_state(
                Path(args.control_file).resolve(),
                "finished",
                "Extraction browser closed.",
            )

    return 1 if any_failure else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only Dimensions Conversation Monitoring extractor (MVP 1.5.2 autonomous date range)"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", help="Single target UI date in YYYY-MM-DD")
    mode.add_argument("--start-date", help="Start UI date in YYYY-MM-DD")
    mode.add_argument(
        "--status-month",
        nargs="?",
        const="latest",
        metavar="YYYY-MM",
        help="Print local MVP 1 scan status for a month; omit value for latest local month.",
    )

    p.add_argument(
        "--end-date",
        help="End UI date in YYYY-MM-DD. Required when --start-date is used.",
    )
    p.add_argument(
        "--rescan",
        "--rerun-complete",
        dest="rescan",
        action="store_true",
        help="Refresh current Dimensions snapshot even when local date already passes acceptance checks.",
    )
    p.add_argument(
        "--retry-missing",
        action="store_true",
        help=(
            "Reprocess only dates containing missing_conversation_ids.json. "
            "Existing fetched details are reused."
        ),
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue to later dates after a date-selection/extraction failure. Default is fail-closed.",
    )
    p.add_argument(
        "--min-detail-coverage",
        type=float,
        default=0.90,
        help=(
            "Minimum fetched-detail coverage required for a date to continue. "
            "Default 0.90 = 90%%. HARD checks are never relaxed."
        ),
    )
    p.add_argument(
        "--max-missing-details",
        type=int,
        default=25,
        help=(
            "Absolute cap on unresolved frozen conversation details. "
            "Default 25. Use -1 to disable the absolute cap."
        ),
    )
    p.add_argument("--output-dir", default="dimensions_output", help="Root output directory")
    p.add_argument("--profile-dir", default=".dimensions_playwright_profile", help="Persistent Playwright browser profile")
    p.add_argument(
        "--control-file",
        help="Optional JSON control file used by the local frontend for user handoff signals.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.status_month:
        output_root = Path(args.output_dir).resolve()
        selected = (
            latest_output_month(output_root)
            if args.status_month == "latest"
            else args.status_month
        )
        if not selected:
            print("[STATUS] No local MVP 1 output found.")
            return 0
        try:
            report = month_extraction_status(output_root, selected)
        except ValueError as e:
            print(f"Invalid status month: {e}", file=sys.stderr)
            return 2
        print(f"\n=== MVP 1 SCAN STATUS — {selected} ===")
        for item in report["dates"]:
            details = "-"
            if item["output_exists"]:
                details = (
                    f"{item['active_details_fetched']}/{item['active_snapshot_count']} details, "
                    f"{item['missing_details']} missing"
                )
            print(f"{item['date']}  {item['status']:20s}  {details}")
        print("\nCounts:")
        for name, count in sorted(report["counts"].items()):
            print(f"{name:20s}: {count}")
        return 0

    if args.start_date and not args.end_date:
        print("--end-date is required when --start-date is used", file=sys.stderr)
        return 2
    if args.date and args.end_date:
        print("--end-date cannot be combined with --date", file=sys.stderr)
        return 2

    try:
        if not (0.0 < args.min_detail_coverage <= 1.0):
            raise ValueError("--min-detail-coverage must be > 0 and <= 1")
        if args.max_missing_details < -1:
            raise ValueError("--max-missing-details must be -1 or >= 0")

        if args.date:
            parse_cli_date(args.date)
        else:
            start = parse_cli_date(args.start_date)
            end = parse_cli_date(args.end_date)
            if end < start:
                raise ValueError("--end-date must be on/after --start-date")
    except ValueError as e:
        print(f"Invalid date arguments: {e}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Existing per-date snapshots/state remain on disk and can be resumed.")
        return 130
    except Exception as e:
        print(f"\n[FATAL] {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
