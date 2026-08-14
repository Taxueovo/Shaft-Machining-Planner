"""Fail closed when private data or unsafe workbook content enters a public release."""

from __future__ import annotations

import re
import sys
import zipfile
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORED_DIRS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", "output", "node_modules"}
FORBIDDEN_PARTS = {
    "data/cases.json", "data/jobs.sqlite3", "backend/rag/data/cases",
    "backend/rag/data/chroma", "backend/rag/data/specs",
}
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{16,}['\"]", re.I),
]
LOCAL_PATHS = [
    re.compile("".join(("/", "Users", "/", "[^/]+", "/"))),
    re.compile("".join((r"[A-Za-z]:", r"\\", "Users", r"\\", r"[^\\]+", r"\\"))),
]
TEXT_SUFFIXES = {".css", ".html", ".ini", ".js", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
PUBLIC_DATA_HASHES = {
    "machines.xlsx": "c12b50b5c8b2e9f2ae5b3498197e0c0e49a0679ff02cf548fd2c8cf50e948aba",
    "tools.xlsx": "b8693ebba7d27a866c5392b8faa1d16825fa25ebd6338a59679bd3436400d2dd",
}


def files() -> list[Path]:
    output: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not publishable: {path.relative_to(ROOT)}")
        if path.is_file():
            output.append(path)
    return output


def audit_workbook(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError(f"Unsafe ZIP path in {path.name}")
        forbidden = ("vbaProject", "externalLinks", "connections.xml", "customXml")
        if any(any(item in name for item in forbidden) for name in names):
            raise ValueError(f"External, macro, or custom data in {path.name}")
        for name in names:
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            text = archive.read(name).decode("utf-8", "replace")
            if "<f" in text:
                raise ValueError(f"Formula found in public workbook {path.name}")


def main() -> int:
    findings: list[str] = []
    scanned = files()
    for path in scanned:
        relative = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > 25 * 1024 * 1024:
            findings.append(f"file exceeds 25 MiB: {relative}")
        if (relative != ".env.example" and (relative == ".env" or relative.startswith(".env."))) or any(relative == part or relative.startswith(f"{part}/") for part in FORBIDDEN_PARTS):
            findings.append(f"forbidden release file: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text("utf-8", errors="replace")
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                findings.append(f"possible credential: {relative}")
            if any(pattern.search(text) for pattern in LOCAL_PATHS):
                findings.append(f"local absolute path: {relative}")
    for workbook in (ROOT / "data/machines.xlsx", ROOT / "data/tools.xlsx"):
        try:
            audit_workbook(workbook)
            digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
            if digest != PUBLIC_DATA_HASHES[workbook.name]:
                findings.append(f"public workbook checksum changed without review: {workbook.name}")
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            findings.append(str(error))
    for required in ("LICENSE", "README.md", ".github/SECURITY.md", ".env.example", "PUBLICATION.md"):
        if not (ROOT / required).is_file():
            findings.append(f"missing release file: {required}")
    if findings:
        print("Release audit failed:\n- " + "\n- ".join(sorted(set(findings))), file=sys.stderr)
        return 1
    print(f"Release audit passed ({len(scanned)} publishable files and 2 workbooks scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
