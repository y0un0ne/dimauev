from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app_paths import get_paths


def test_production_paths_live_outside_install_tree() -> None:
    old_mode = os.environ.get("DIMENSIONS_PRODUCTION_MODE")
    old_root = os.environ.get("DIMENSIONS_APP_DATA_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DIMENSIONS_PRODUCTION_MODE"] = "1"
        os.environ["DIMENSIONS_APP_DATA_ROOT"] = tmp
        try:
            paths = get_paths()
            paths.ensure_runtime_dirs()
            assert paths.root == Path(tmp).resolve()
            assert paths.output == Path(tmp).resolve() / "data"
            assert paths.browser_profile == Path(tmp).resolve() / "browser-profile"
            assert paths.reports == paths.output / "monthly_reports"
            assert paths.output.exists()
            assert paths.browser_profile.exists()
        finally:
            if old_mode is None:
                os.environ.pop("DIMENSIONS_PRODUCTION_MODE", None)
            else:
                os.environ["DIMENSIONS_PRODUCTION_MODE"] = old_mode
            if old_root is None:
                os.environ.pop("DIMENSIONS_APP_DATA_ROOT", None)
            else:
                os.environ["DIMENSIONS_APP_DATA_ROOT"] = old_root


if __name__ == "__main__":
    test_production_paths_live_outside_install_tree()
    print("PASS production LocalAppData paths")
