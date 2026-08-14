from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn


FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = FRONTEND_DIR.parent
BACKEND_RUNNER = PROJECT_DIR / "backend" / "run_backend.py"
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")


def backend_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def start_backend() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(BACKEND_RUNNER)],
        cwd=str(PROJECT_DIR / "backend"),
    )


def wait_backend(timeout: int = 35) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if backend_ready():
            return
        time.sleep(0.5)
    raise RuntimeError("Backend startup timed out. Check the Conda dependencies and console errors.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Shaft Machining Planner frontend.")
    parser.add_argument(
        "--frontend-only",
        action="store_true",
        help="Do not auto-start the backend.",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    backend_process = None
    try:
        if not backend_ready():
            if args.frontend_only:
                raise RuntimeError(
                    "Backend is not running. Run python backend/run_backend.py first."
                )
            print("[Shaft Machining Planner] Backend not running, starting a separate backend process...")
            backend_process = start_backend()
            wait_backend()

        print(f"[Shaft Machining Planner] Backend: {BACKEND_URL}")
        print(f"[Shaft Machining Planner] Frontend: {FRONTEND_URL}")

        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(FRONTEND_URL)).start()

        uvicorn.run(
            "main:app",
            app_dir=str(FRONTEND_DIR),
            host=os.getenv("FRONTEND_HOST", "127.0.0.1"),
            port=int(os.getenv("FRONTEND_PORT", "8000")),
            reload=False,
            log_level=os.getenv("LOG_LEVEL", "info"),
        )
    finally:
        if backend_process is not None and backend_process.poll() is None:
            print("[Shaft Machining Planner] Closing the backend process started by the frontend...")
            backend_process.terminate()
            try:
                backend_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend_process.kill()


if __name__ == "__main__":
    main()
