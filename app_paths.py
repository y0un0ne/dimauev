from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "DimensionsAutoEvaluate"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent


def install_root() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else bundle_root()


def local_app_data() -> Path:
    raw = os.getenv("LOCALAPPDATA")
    if not raw:
        raw = str(Path.home() / "AppData" / "Local")
    return Path(raw).resolve() / APP_NAME


@dataclass(frozen=True)
class AppPaths:
    root: Path
    output: Path
    browser_profile: Path
    logs: Path
    pipeline_status: Path
    controls: Path
    progress: Path
    runtime: Path
    config: Path
    reports: Path

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.root,
            self.output,
            self.browser_profile,
            self.logs,
            self.pipeline_status,
            self.controls,
            self.progress,
            self.runtime,
            self.config,
            self.reports,
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_paths() -> AppPaths:
    override = os.getenv("DIMENSIONS_APP_DATA_ROOT")
    if override:
        root = Path(override).resolve()
    elif is_frozen() or os.getenv("DIMENSIONS_PRODUCTION_MODE") == "1":
        root = local_app_data()
    else:
        root = bundle_root()

    output_override = os.getenv("DIMENSIONS_OUTPUT_ROOT")
    profile_override = os.getenv("DIMENSIONS_PROFILE_ROOT")
    output = Path(output_override).resolve() if output_override else (
        root / "data" if root != bundle_root() else root / "dimensions_output"
    )
    profile = Path(profile_override).resolve() if profile_override else (
        root / "browser-profile" if root != bundle_root() else root / ".dimensions_playwright_profile"
    )
    production = root != bundle_root()
    state_root = root if production else local_app_data()
    return AppPaths(
        root=root,
        output=output,
        browser_profile=profile,
        logs=(state_root / "logs") if production else (output / "logs"),
        pipeline_status=output / "pipeline_status",
        controls=state_root / "controls",
        progress=state_root / "progress",
        runtime=state_root / "runtime",
        config=state_root / "config",
        reports=output / "monthly_reports",
    )
