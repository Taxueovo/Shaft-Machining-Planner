"""
================================================

Application Base Settings

================================================
"""

import os
import sys
import getpass
import logging
from pathlib import Path
from typing import List, Optional


# ==============================================================================
# Application Info
# ==============================================================================

APP_NAME = "ShaftPlanner"
APP_VERSION = "1.0.0"


# ==============================================================================
# API Server Settings
# ==============================================================================

API_HOST = "0.0.0.0"
API_PORT = 8000


# ==============================================================================
# File Extensions
# ==============================================================================

ALLOWED_EXTENSIONS: List[str] = ['.stp', '.step', '.brep']


# ==============================================================================
# Proxy Configuration (Corporate Network)
# ==============================================================================
# Security: Proxy credentials are NEVER hardcoded. They are read from
# environment variables at runtime:
#   HTTP_PROXY_USER       - proxy username (required to enable proxy)
#   HTTP_PROXY_PASSWORD   - proxy password (required to enable proxy)
#   HTTP_PROXY_HOST       - proxy host (default: <proxy-host>)
#   HTTP_PROXY_PORT       - proxy port (default: 8080)
#
# If creds are missing AND stdin is a TTY (interactive CLI), the user is
# prompted via input()/getpass. If creds are missing AND not in a TTY
# (e.g. Chainlit web UI, nohup, Docker), the proxy is silently skipped and
# a WARNING is logged - startup is NOT blocked.

DEFAULT_PROXY_HOST = "<proxy-host>"
DEFAULT_PROXY_PORT = "8080"


def _get_proxy_credentials() -> tuple[Optional[str], Optional[str]]:
    """
    Resolve proxy credentials from environment variables, with interactive
    fallback in a TTY.

    Returns:
        (user, password) tuple. Either entry may be None if unavailable.
    """
    logger = logging.getLogger(__name__)
    user = os.environ.get("HTTP_PROXY_USER")
    password = os.environ.get("HTTP_PROXY_PASSWORD")

    if user and password:
        return user, password

    # Fallback: interactive prompt ONLY in a real TTY. Chainlit and other
    # non-interactive contexts must not block on input().
    try:
        is_tty = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        is_tty = False

    if not is_tty:
        logger.warning(
            "HTTP_PROXY_USER / HTTP_PROXY_PASSWORD not set and stdin is not a "
            "TTY; proxy is disabled. Set the env vars to enable it."
        )
        return user, password

    try:
        if not user:
            entered = input("HTTP_PROXY_USER (proxy username): ").strip()
            user = entered or None
        if not password:
            entered = getpass.getpass("HTTP_PROXY_PASSWORD (proxy password): ")
            password = entered or None
    except (EOFError, KeyboardInterrupt):
        logger.warning("Proxy credential input interrupted; proxy is disabled.")
        return user, password

    return user, password


def apply_proxy_settings() -> bool:
    """
    Apply proxy settings to os.environ from env vars / interactive prompt.

    Callers should invoke this explicitly; importing config.settings no
    longer triggers it (avoids "import-time side effect" that previously
    leaked hardcoded credentials into the environment).

    Returns:
        True if the proxy was applied, False if disabled (missing creds
        and not in a TTY, or user cancelled).
    """
    from urllib.parse import quote  # stdlib; local import keeps top tidy

    user, password = _get_proxy_credentials()
    if not user or not password:
        return False

    host = os.environ.get("HTTP_PROXY_HOST", DEFAULT_PROXY_HOST)
    port = os.environ.get("HTTP_PROXY_PORT", DEFAULT_PROXY_PORT)

    # URL-encode credentials in case the password contains special chars.
    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    proxy_url = f"http://{user_q}:{pass_q}@{host}:{port}"

    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
    return True


# ==============================================================================
# Path Configuration
# ==============================================================================

def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def get_3d_models_dir() -> Path:
    """Get the 3D models directory."""
    return get_project_root() / "3D"


# ==============================================================================
# Logging Configuration
# ==============================================================================

def setup_logger(name: str = __name__, level: int = logging.INFO) -> logging.Logger:
    """
    Configure and return a logger instance.

    Args:
        name: Logger name
        level: Log level

    Returns:
        The configured Logger instance
    """
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    return logging.getLogger(name)


# NOTE: apply_proxy_settings() is intentionally NOT called on import.
# Callers (e.g. run.py, ui/app.py) must invoke it explicitly so that
# credential prompts only happen when the user actually runs the app,
# and so that mere imports (e.g. tests, tools) never leak secrets.
