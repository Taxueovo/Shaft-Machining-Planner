"""
cadagent launcher (3D CAD feature extraction + rendering service)
=================================================================

Starts the cadagent service on its own (port 8100, away from the peagent frontend 8000 / backend 8001).

Usage:
    python run_cadagent.py            # Default 0.0.0.0:8100
    python run_cadagent.py --port 8200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CADAGENT_DIR = ROOT / "cadagent"


def main() -> None:
    # Windows console/redirection defaults to cp1252 which cannot print non-ASCII characters; force UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Start cadagent (3D CAD analysis) FastAPI service")
    parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    parser.add_argument("--port", type=int, default=8100, help="Listen port (avoids peagent 8000/8001)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload in development mode")
    args = parser.parse_args()

    # Make sure the project root is on sys.path so the `cadagent` package can be imported
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import uvicorn

    print("=" * 60)
    print("cadagent (3D CAD analysis) FastAPI Backend")
    print(f"  Directory: {CADAGENT_DIR}")
    print(f"  http://127.0.0.1:{args.port}/docs")
    print("  Endpoints: /health, /upload_and_render, /extract_features,")
    print("             /api/v1/planning-input, /chat, ...")
    print("=" * 60)

    uvicorn.run(
        "cadagent.ui.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
