from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

PROJECT_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_IMPORT_ROOT))

from credential_store import ALLOWED_CREDENTIALS, CredentialStore
from dimensions_extraction_status import latest_output_month, month_extraction_status, read_extraction_status
from dimensions_evaluation_status import (
    evaluation_errors,
    evaluation_history,
    latest_evaluation_month,
    month_evaluation_status,
    read_evaluation_status,
)
from schemas import CredentialRequest, EvaluateRequest, ExtractRequest, GeminiAccountRequest, ReportRequest
from runtime_health import dependency_health
from worker_manager import OUTPUT_ROOT, PROJECT_ROOT, WorkerConflict, WorkerManager


app = FastAPI(title="Dimensions Auto Evaluate", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
credential_store = CredentialStore()
manager = WorkerManager(credential_store)

DATE_ROOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORT_MONTH_RE = re.compile(r"^Conversation_Monitoring_(\d{4}-\d{2})(?:_.*)?\.xlsx$")


def report_readiness(month: str) -> Dict[str, Any]:
    roots = []
    if OUTPUT_ROOT.exists():
        roots = [
            root for root in OUTPUT_ROOT.iterdir()
            if root.is_dir() and DATE_ROOT_RE.fullmatch(root.name) and root.name.startswith(f"{month}-")
        ]
    result_files = sum(
        len(list((root / "ai_reviews" / "results").glob("*.json")))
        for root in roots
        if (root / "ai_reviews" / "results").exists()
    )
    if not roots:
        return {
            "ready": False,
            "reason": "No MVP 1 date folders exist for this month. Run extraction first.",
            "source_date_folders": 0,
            "evaluator_result_files": 0,
        }
    if not result_files:
        return {
            "ready": False,
            "reason": "MVP 1 data exists, but no MVP 2 evaluator results exist for this month.",
            "source_date_folders": len(roots),
            "evaluator_result_files": 0,
        }
    return {
        "ready": True,
        "reason": "Report source data is ready.",
        "source_date_folders": len(roots),
        "evaluator_result_files": result_files,
    }


def start_worker(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    health = dependency_health().get(kind, {"ready": False, "missing": ["unknown"]})
    if not health["ready"]:
        missing = ", ".join(health["missing"])
        raise HTTPException(
            status_code=503,
            detail=f"{kind.title()} worker is unavailable. Missing Python packages: {missing}. Restart the app to install dependencies.",
        )
    try:
        return manager.start(kind, payload)
    except WorkerConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "frontend_api_version": 4,
        "project_root": str(PROJECT_ROOT),
        "dependencies": dependency_health(),
    }


@app.get("/api/dependencies")
def dependencies() -> Dict[str, Dict[str, Any]]:
    return dependency_health()


@app.get("/api/workers")
def workers() -> List[Dict[str, Any]]:
    return manager.list()


@app.get("/api/workers/{worker_id}")
def worker(worker_id: str) -> Dict[str, Any]:
    try:
        return manager.get(worker_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Worker not found") from e


@app.post("/api/workers/extract", status_code=201)
def extract(payload: ExtractRequest) -> Dict[str, Any]:
    if payload.date:
        status = read_extraction_status(OUTPUT_ROOT, payload.date)
        if payload.scan_mode == "normal" and status["status"] in {"complete", "accepted_with_gaps"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Date already has an accepted MVP 1 snapshot. Choose Rescan to refresh "
                    "current Dimensions data, or Retry missing for unresolved detail gaps."
                ),
            )
        if payload.scan_mode == "retry_missing" and status["missing_details"] == 0:
            raise HTTPException(
                status_code=409,
                detail="This date has no unresolved missing details. Use Normal scan or Rescan.",
            )
    return start_worker("extract", payload.model_dump(mode="json", exclude_none=True))


@app.post("/api/workers/evaluate", status_code=201)
def evaluate(payload: EvaluateRequest) -> Dict[str, Any]:
    return start_worker("evaluate", payload.model_dump(mode="json", exclude_none=True))


@app.post("/api/workers/report", status_code=201)
def report(payload: ReportRequest) -> Dict[str, Any]:
    readiness = report_readiness(payload.month)
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail=readiness["reason"])
    return start_worker("report", payload.model_dump(mode="json"))


@app.post("/api/workers/{worker_id}/stop")
def stop(worker_id: str) -> Dict[str, Any]:
    try:
        return manager.stop(worker_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Worker not found") from e


@app.delete("/api/workers/history/{worker_id}")
def hide_worker_history(worker_id: str) -> Dict[str, Any]:
    try:
        manager.hide_history(worker_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Worker history not found") from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"hidden": True, "worker_id": worker_id, "hidden_count": len(manager.hidden_history())}


@app.get("/api/workers/history/hidden")
def hidden_worker_history() -> Dict[str, Any]:
    return {"hidden": manager.hidden_history()}


@app.post("/api/workers/history/restore")
def restore_worker_history() -> Dict[str, Any]:
    return {"restored": manager.restore_history(), "hidden": manager.hidden_history()}


@app.post("/api/workers/{worker_id}/continue")
def continue_worker(worker_id: str) -> Dict[str, Any]:
    try:
        return manager.continue_worker(worker_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Worker not found") from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.get("/api/workers/{worker_id}/log", response_class=PlainTextResponse)
def log(worker_id: str) -> str:
    try:
        return manager.log(worker_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Worker not found") from e


@app.get("/api/providers")
def providers() -> Dict[str, Any]:
    saved = credential_store.configured()
    configured = {
        "gemini": bool(saved["GEMINI_API_KEY"] or os.getenv("GOOGLE_API_KEY")),
        "groq": saved["GROQ_API_KEY"],
        "cloudflare": bool(
            saved["CLOUDFLARE_API_TOKEN"]
            and (saved["CLOUDFLARE_ACCOUNT_ID"] or os.getenv("CF_ACCOUNT_ID"))
        ),
        "openrouter": saved["OPENROUTER_API_KEY"],
    }
    ollama: Dict[str, Any] = {"online": False, "models": []}
    try:
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        with urllib.request.urlopen(base.removesuffix("/v1") + "/api/tags", timeout=2) as response:
            data = json.load(response)
        ollama = {
            "online": True,
            "models": sorted(
                str(item.get("name") or item.get("model"))
                for item in data.get("models", [])
                if isinstance(item, dict) and (item.get("name") or item.get("model"))
            ),
        }
    except Exception:
        pass
    return {"configured": configured, "ollama": ollama}


@app.get("/api/settings/credentials")
def credential_status() -> Dict[str, bool]:
    return credential_store.configured()


@app.put("/api/settings/credentials/{name}")
def save_credential(name: str, payload: CredentialRequest) -> Dict[str, Any]:
    if name not in ALLOWED_CREDENTIALS:
        raise HTTPException(status_code=404, detail="Credential not supported")
    try:
        credential_store.set(name, payload.value)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"name": name, "configured": True}


@app.delete("/api/settings/credentials/{name}")
def remove_credential(name: str) -> Dict[str, Any]:
    if name not in ALLOWED_CREDENTIALS:
        raise HTTPException(status_code=404, detail="Credential not supported")
    try:
        deleted = credential_store.delete(name)
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"name": name, "configured": False, "deleted": deleted}


@app.get("/api/settings/gemini-accounts")
def gemini_accounts() -> Dict[str, Any]:
    return {"accounts": credential_store.list_gemini_accounts()}


@app.post("/api/settings/gemini-accounts", status_code=201)
def add_gemini_account(payload: GeminiAccountRequest) -> Dict[str, Any]:
    try:
        return {"account": credential_store.add_gemini_account(payload.label, payload.email, payload.api_key)}
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/settings/gemini-accounts/{account_id}")
def delete_gemini_account(account_id: str) -> Dict[str, Any]:
    try:
        deleted = credential_store.delete_gemini_account(account_id)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not deleted:
        raise HTTPException(status_code=404, detail="Gemini account not found")
    return {"deleted": True, "account_id": account_id}


@app.get("/api/dates")
def dates() -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if not OUTPUT_ROOT.exists():
        return result
    for root in sorted(OUTPUT_ROOT.iterdir(), reverse=True):
        if not root.is_dir() or len(root.name) != 10:
            continue
        summary = None
        path = root / "summary.json"
        if path.exists():
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        result.append({
            "date": root.name,
            "normalized": len(list((root / "normalized").glob("*.json"))),
            "extraction_status": (summary or {}).get("acceptance_status"),
            "has_evaluation": (root / "ai_reviews" / "evaluation_summary.json").exists(),
        })
    return result


@app.get("/api/extraction-status")
def extraction_status(month: str | None = None) -> Dict[str, Any]:
    selected = month or latest_output_month(OUTPUT_ROOT)
    if not selected:
        return {"month": None, "counts": {}, "dates": []}
    try:
        return month_extraction_status(OUTPUT_ROOT, selected)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/extraction-status/{date_value}")
def extraction_date_status(date_value: str) -> Dict[str, Any]:
    return read_extraction_status(OUTPUT_ROOT, parse_status_date(date_value))


@app.get("/api/evaluation-status")
def evaluation_status(month: str | None = None) -> Dict[str, Any]:
    selected = month or latest_evaluation_month(OUTPUT_ROOT)
    if not selected:
        return {"month": None, "counts": {}, "dates": []}
    try:
        return month_evaluation_status(OUTPUT_ROOT, selected)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def parse_status_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD") from e


@app.get("/api/evaluation-status/{date_value}")
def evaluation_date_status(date_value: str) -> Dict[str, Any]:
    return read_evaluation_status(OUTPUT_ROOT, parse_status_date(date_value))


@app.get("/api/evaluation-status/{date_value}/history")
def evaluation_date_history(date_value: str, limit: int = 100) -> Dict[str, Any]:
    return evaluation_history(
        OUTPUT_ROOT, parse_status_date(date_value), max(1, min(limit, 500))
    )


@app.get("/api/evaluation-status/{date_value}/errors")
def evaluation_date_errors(date_value: str, limit: int = 200) -> Dict[str, Any]:
    return evaluation_errors(
        OUTPUT_ROOT, parse_status_date(date_value), max(1, min(limit, 500))
    )


@app.get("/api/reports")
def reports() -> List[Dict[str, Any]]:
    root = OUTPUT_ROOT / "monthly_reports"
    if not root.exists():
        return []
    manifests: Dict[str, Dict[str, Any]] = {}
    for manifest_path in root.glob("report_build_*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            generated = Path(str(manifest.get("generated_path") or ""))
            if generated.name:
                manifests[generated.name] = manifest
        except Exception:
            continue
    out = []
    for path in sorted(root.glob("Conversation_Monitoring_*.xlsx"), reverse=True):
        manifest = manifests.get(path.name, {})
        match = REPORT_MONTH_RE.fullmatch(path.name)
        report_month = str(manifest.get("month") or (match.group(1) if match else "unknown"))
        out.append({
            "name": path.name,
            "month": report_month,
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
            "status": manifest.get("status") or "existing",
            "canonical": path.name == f"Conversation_Monitoring_{report_month}.xlsx",
            "warnings": manifest.get("warnings") or [],
        })
    return out


@app.get("/api/reports/readiness")
def reports_readiness(month: str) -> Dict[str, Any]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=400, detail="Invalid month")
    return report_readiness(month)


@app.get("/api/reports/{month}/download")
def download_report(month: str) -> FileResponse:
    if len(month) != 7 or month[4] != "-" or not month.replace("-", "").isdigit():
        raise HTTPException(status_code=400, detail="Invalid month")
    root = (OUTPUT_ROOT / "monthly_reports").resolve()
    path = (root / f"Conversation_Monitoring_{month}.xlsx").resolve()
    if path.parent != root or not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, filename=path.name)


@app.get("/api/reports/file/{filename}")
def download_report_file(filename: str) -> FileResponse:
    if Path(filename).name != filename or not re.fullmatch(r"Conversation_Monitoring_.+\.xlsx", filename):
        raise HTTPException(status_code=400, detail="Invalid report filename")
    root = (OUTPUT_ROOT / "monthly_reports").resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, filename=path.name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
