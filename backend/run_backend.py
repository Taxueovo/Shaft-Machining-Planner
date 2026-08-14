from __future__ import annotations
import os
import ipaddress
import secrets
import uvicorn

if __name__ == "__main__":
    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError as error:
        raise SystemExit("BACKEND_HOST must be localhost or a loopback IP address.") from error
    if not is_loopback:
        raise SystemExit("BACKEND_HOST must be a loopback address; remote hosting is intentionally disabled.")
    os.environ.setdefault("LOCAL_API_TOKEN", secrets.token_urlsafe(32))
    uvicorn.run(
        "app:app",
        app_dir=os.path.dirname(os.path.abspath(__file__)),
        host=host,
        port=int(os.getenv("BACKEND_PORT", "8001")),
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
