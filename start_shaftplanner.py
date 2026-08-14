"""
Shaft Machining Planner one-click launcher
============================

Starts two services at the same time:

    1. peagent backend  :8001  (motor shaft process planning LangGraph workflow)
    2. peagent frontend :8000  (Web UI)

Usage:
    python start_shaftplanner.py                # Start everything and open the browser
    python start_shaftplanner.py --no-browser   # Do not open the browser
    python start_shaftplanner.py --python C:\\...\\python.exe  # Specify the interpreter

Notes:
- Automatically sets NO_PROXY=127.0.0.1,localhost so local requests are not blocked by the corporate proxy (403).
- Ports already running are skipped (idempotent); Ctrl+C or any process exit shuts down all services.
- The peagent backend auto-stops after 30s without a browser heartbeat (existing behavior; heartbeat.js keeps it alive once the browser is open).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PE_BACKEND_RUNNER = ROOT / "backend" / "run_backend.py"
PE_FRONTEND_RUNNER = ROOT / "frontend" / "run_frontend.py"

PE_BACKEND_HEALTH = "http://127.0.0.1:8001/health"
PE_FRONTEND_URL = "http://127.0.0.1:8000"


def _ready(url: str, timeout: float) -> bool:
    """Wait for a service to be ready (an HTTP 200 response means it is ready)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _wait_all(targets: list[tuple[str, str]], timeout: float = 60) -> None:
    """Wait for multiple services to be ready in parallel.

    Unlike waiting serially one by one: after startup all services are polled at
    the same time; total readiness time = the slowest service, not the sum of
    the individual startup times.
    """
    remaining = {name: url for name, url in targets}
    deadline = time.time() + timeout
    while remaining and time.time() < deadline:
        for name, url in list(remaining.items()):
            if _ready(url, 1):
                print(f"[Shaft Machining Planner] {name} ready")
                del remaining[name]
        if remaining:
            time.sleep(0.5)
    for name in remaining:
        print(f"[Shaft Machining Planner] Warning: {name} startup timed out, check the console error output.")


def _start(processes: list[tuple[str, subprocess.Popen]], name: str,
           cmd: list[str], cwd: Path, env: dict) -> None:
    print(f"[Shaft Machining Planner] Starting {name} ...")
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env)
    processes.append((name, proc))


def main() -> None:
    # Windows console/redirection defaults to cp1252 which cannot print non-ASCII characters; force UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Shaft Machining Planner one-click launcher")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--python", default=None,
                        help="Python interpreter path (defaults to the current environment's sys.executable)")
    args = parser.parse_args()

    python = args.python or sys.executable

    # Child-process environment: make sure local (127.0.0.1) requests bypass the corporate proxy
    env = dict(os.environ)
    no_proxy = [p for p in ("127.0.0.1", "localhost") if p not in (env.get("NO_PROXY") or "").split(",")]
    env["NO_PROXY"] = ",".join(no_proxy + ([env["NO_PROXY"]] if env.get("NO_PROXY") else []))
    if no_proxy:
        print(f"[Shaft Machining Planner] NO_PROXY={env['NO_PROXY']} set (so local requests are not intercepted by the proxy)")

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        # ── Backend first, then the frontend ──
        # The frontend runner (run_frontend.py --frontend-only) hard-checks backend
        # liveness, so it must wait for the backend to be ready.
        targets: list[tuple[str, str]] = []  # (display name, health check URL)

        # 1. peagent backend :8001
        if _ready(PE_BACKEND_HEALTH, 2):
            print(f"[Shaft Machining Planner] peagent backend already running ({PE_BACKEND_HEALTH})")
        else:
            _start(processes, "peagent backend (8001)",
                   [python, str(PE_BACKEND_RUNNER)], ROOT / "backend", env)
            targets.append(("peagent backend (8001)", PE_BACKEND_HEALTH))

        # Wait for the backend to be ready
        backend_ready_now = _ready(PE_BACKEND_HEALTH, 60)
        if not backend_ready_now:
            print("[Shaft Machining Planner] Warning: peagent backend startup timed out, check the console error output.")

        # 2. peagent frontend :8000 (only started if already running or the backend is ready)
        if _ready(PE_FRONTEND_URL, 2):
            print(f"[Shaft Machining Planner] peagent frontend already running ({PE_FRONTEND_URL})")
        elif backend_ready_now or _ready(PE_BACKEND_HEALTH, 1):
            frontend_cmd = [python, str(PE_FRONTEND_RUNNER), "--frontend-only", "--no-browser"]
            _start(processes, "peagent frontend (8000)", frontend_cmd,
                   ROOT / "frontend", env)
            targets.append(("peagent frontend (8000)", PE_FRONTEND_URL))
        else:
            print("[Shaft Machining Planner] Warning: peagent backend not ready, frontend not started.")

        # ── Wait for the remaining services to be ready ──
        _wait_all(targets, timeout=60)

        print()
        print("=" * 60)
        print("Shaft Machining Planner ready")
        print(f"  peagent frontend : {PE_FRONTEND_URL}")
        print(f"  peagent backend  : {PE_BACKEND_HEALTH}")
        print("  Press Ctrl+C or close this window to stop all services")
        print("=" * 60)

        if not args.no_browser:
            threading_delay = 1.5
            timer = __import__("threading").Timer(threading_delay, lambda: webbrowser.open(PE_FRONTEND_URL))
            timer.start()

        # Block: exit the loop when any process exits or Ctrl+C is received, then clean up
        while True:
            time.sleep(1)
            exited = [name for name, p in processes if p.poll() is not None]
            if exited:
                print(f"[Shaft Machining Planner] Process exited: {', '.join(exited)}, shutting down the remaining services...")
                break
    except KeyboardInterrupt:
        print("\n[Shaft Machining Planner] Interrupt received, shutting down all services...")
    finally:
        for name, proc in processes:
            if proc.poll() is None:
                print(f"[Shaft Machining Planner] Shutting down {name} ...")
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("[Shaft Machining Planner] All services shut down.")


if __name__ == "__main__":
    main()
