from __future__ import annotations
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        app_dir=os.path.dirname(os.path.abspath(__file__)),
        host=os.getenv("BACKEND_HOST", "127.0.0.1"),
        port=int(os.getenv("BACKEND_PORT", "8001")),
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
