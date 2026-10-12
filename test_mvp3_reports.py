from __future__ import annotations

import json
import tempfile
from pathlib import Path

import dimensions_excel_generator_mvp3 as mvp3


def test_first_report_uses_canonical_filename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "input" / "2026-09-01").mkdir(parents=True)
        output = root / "reports"
        report = mvp3.build_month_workbook(root / "input", output, "2026-09")
        assert report.name == "Conversation_Monitoring_2026-09.xlsx"
        manifest = json.loads((output / "report_build_2026-09.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "complete"
        assert manifest["canonical_replaced"] is True


def test_locked_canonical_generates_versioned_copy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "input" / "2026-09-01").mkdir(parents=True)
        output = root / "reports"
        canonical = mvp3.build_month_workbook(root / "input", output, "2026-09")
        original_replace = Path.replace

        def locked_replace(path: Path, target: Path) -> Path:
            if path.name == "Conversation_Monitoring_2026-09.xlsx.tmp" and Path(target) == canonical:
                raise PermissionError("locked for test")
            return original_replace(path, target)

        Path.replace = locked_replace
        try:
            generated = mvp3.build_month_workbook(root / "input", output, "2026-09")
        finally:
            Path.replace = original_replace

        assert generated != canonical
        assert generated.exists()
        assert generated.name.startswith("Conversation_Monitoring_2026-09_")
        manifest = json.loads((output / "report_build_2026-09.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "complete_with_locked_copy"
        assert manifest["canonical_replaced"] is False
        assert Path(manifest["generated_path"]) == generated
        assert manifest["warnings"]


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} CHECKS PASSED")
