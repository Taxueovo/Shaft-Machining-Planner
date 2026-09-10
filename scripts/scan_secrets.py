# 扫描文本中的凭据特征，仅报告命中文件位置而不打印秘密值。
"""Small dependency-free release guard for accidentally committed credentials."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
IGNORED = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "output",
    "chroma",
    ".venv",
    "venv",
    ".coverage",
}
# 需拦截的凭据类别：私钥、GitHub/AWS token、OpenAI 风格密钥
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
findings: list[str] = []

# 逐文件扫描发布树：跳过忽略目录中的内容及明显的二进制/图片/办公文件
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in IGNORED for part in path.parts):
        continue
    if path.suffix.lower() in {".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}:
        continue
    # 无法按 UTF-8 解码（多为二进制）的文件直接跳过，不计为失败
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{label}: {path.relative_to(ROOT)}")

# 任一命中都以非零退出并列出全部位置，作为误提交凭据的发布前守卫
if findings:
    raise SystemExit("\n".join(findings))
print("Secret scan passed.")
