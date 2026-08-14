"""Small dependency-free release guard for accidentally committed credentials."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
IGNORED = {".git", ".pytest_cache", "__pycache__", "node_modules", "output", "chroma"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
findings: list[str] = []

for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in IGNORED for part in path.parts):
        continue
    if path.suffix.lower() in {".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{label}: {path.relative_to(ROOT)}")

if findings:
    raise SystemExit("\n".join(findings))
print("Secret scan passed.")
