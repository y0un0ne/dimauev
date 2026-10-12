from __future__ import annotations

import importlib.util
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REQUIREMENTS = {
    "extract": {
        "playwright": "playwright",
    },
    "evaluate": {
        "google.genai": "google-genai",
        "pydantic": "pydantic",
        "openai": "openai",
        "httpx": "httpx",
    },
    "report": {
        "openpyxl": "openpyxl",
        "PIL": "Pillow",
    },
}


def dependency_health() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for worker, modules in REQUIREMENTS.items():
        missing = [package for module, package in modules.items() if importlib.util.find_spec(module) is None]
        result[worker] = {
            "ready": not missing,
            "missing": missing,
        }
    if result["extract"]["ready"]:
        try:
            ZoneInfo("Asia/Jakarta")
        except ZoneInfoNotFoundError:
            result["extract"] = {
                "ready": False,
                "missing": ["tzdata (Asia/Jakarta timezone data)"],
            }
    return result
