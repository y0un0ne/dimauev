from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app_paths import get_paths


ERROR_ALREADY_EXISTS = 183
_standard_streams: list[object] = []


def ensure_standard_streams(log_path: Path) -> None:
    """Give windowed PyInstaller processes valid streams without changing redirected workers."""
    if sys.stdin is None:
        stream = open(os.devnull, "r", encoding="utf-8")
        _standard_streams.append(stream)
        sys.stdin = stream
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            stream = log_path.open("a", encoding="utf-8", buffering=1)
            _standard_streams.append(stream)
            setattr(sys, name, stream)


def acquire_single_instance() -> object | None:
    if os.name != "nt":
        return object()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, "Local\\DimensionsAutoEvaluateProduction")
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def open_existing(paths) -> bool:
    state_file = paths.runtime / "server.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        port = int(state["port"])
        webbrowser.open(f"http://127.0.0.1:{port}")
        return True
    except Exception:
        return False


def _run_module_main(module_name: str, args: list[str]) -> int:
    module = __import__(module_name, fromlist=["main"])
    sys.argv = [module_name, *args]
    return int(module.main())


def run_worker(worker: str, args: list[str]) -> int:
    modules = {
        "pipeline": "dimensions_pipeline",
        "extract": "dimensions_extractor_mvp1_5_2",
        "evaluate": "dimensions_ai_evaluator_mvp2_6",
        "report": "dimensions_excel_generator_mvp3",
    }
    if worker not in modules:
        raise SystemExit(f"Unknown worker: {worker}")
    return _run_module_main(modules[worker], args)


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_server(port: int = 0, open_browser: bool = True) -> int:
    os.environ["DIMENSIONS_PRODUCTION_MODE"] = "1"
    paths = get_paths()
    paths.ensure_runtime_dirs()
    ensure_standard_streams(paths.logs / "application.log")
    instance = acquire_single_instance()
    if instance is None:
        open_existing(paths)
        return 0
    os.environ["DIMENSIONS_APP_DATA_ROOT"] = str(paths.root)
    os.environ["DIMENSIONS_OUTPUT_ROOT"] = str(paths.output)
    os.environ["DIMENSIONS_PROFILE_ROOT"] = str(paths.browser_profile)
    browser_root = Path(__file__).resolve().parent / "browsers"
    if browser_root.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    selected_port = port or available_port()
    url = f"http://127.0.0.1:{selected_port}"
    (paths.runtime / "server.json").write_text(
        json.dumps({"pid": os.getpid(), "port": selected_port, "url": url}, indent=2),
        encoding="utf-8",
    )

    if open_browser:
        def launch() -> None:
            time.sleep(1.2)
            webbrowser.open(url)

        threading.Thread(target=launch, daemon=True).start()

    from Frontend.server import app as app_module
    import uvicorn

    try:
        server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=selected_port, log_level="info"))
        app_module.shutdown_callback = lambda: setattr(server, "should_exit", True)
        server.run()
    finally:
        app_module.manager.stop_all()
        (paths.runtime / "server.json").unlink(missing_ok=True)
    return 0


def main() -> int:
    paths = get_paths()
    paths.ensure_runtime_dirs()
    ensure_standard_streams(paths.logs / "application.log")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", choices=["pipeline", "extract", "evaluate", "report"])
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    known, remaining = parser.parse_known_args()
    if known.worker:
        return run_worker(known.worker, remaining)
    return run_server(known.port, not known.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
