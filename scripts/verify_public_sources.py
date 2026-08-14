"""Audit public workbook provenance without changing engineering values.

Automatic scraping is intentionally excluded: a human must compare manufacturer
pages with workbook values before updating the files.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
WORKBOOKS = ((ROOT / "data/machines.xlsx", "Export"), (ROOT / "data/tools.xlsx", "Tool_Selection"))
ALLOWED_HOSTS = {"us.dmgmori.com", "en.dmgmori.com", "www.kapp-niles.com", "www.gleason.com", "www.iscar.com"}


def as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def workbook_records(path: Path, sheet: str) -> list[dict[str, object]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = workbook[sheet].iter_rows(values_only=True)
    headers = [str(value or "") for value in next(rows)]
    return [dict(zip(headers, row)) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="also confirm that each official URL responds")
    parser.add_argument("--max-age-days", type=int, default=180)
    args = parser.parse_args()
    findings: list[dict[str, object]] = []
    urls: set[str] = set()
    today = datetime.now(timezone.utc).date()
    for path, sheet in WORKBOOKS:
        for index, row in enumerate(workbook_records(path, sheet), start=2):
            url = next((str(value).strip() for key, value in row.items() if "url" in key.lower() and value), "")
            checked = next((as_date(value) for key, value in row.items() if "checked" in key.lower()), None)
            if not url or urlparse(url).scheme != "https" or urlparse(url).hostname not in ALLOWED_HOSTS:
                findings.append({"file": path.name, "row": index, "issue": "missing_or_unapproved_official_url"})
            else:
                urls.add(url)
            if checked is None or (today - checked).days > args.max_age_days:
                findings.append({"file": path.name, "row": index, "issue": "source_review_stale", "checked": str(checked)})
    if args.online:
        for url in sorted(urls):
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 ShaftPlanner-PublicSourceAudit/1.0",
                        "Range": "bytes=0-4095",
                    },
                )
                with urlopen(request, timeout=20) as response:
                    if response.status >= 400:
                        findings.append({"url": url, "issue": "source_unreachable", "status": response.status})
            except HTTPError as error:
                findings.append({"url": url, "issue": "source_unreachable", "status": error.code})
            except (URLError, TimeoutError) as error:
                findings.append({"url": url, "issue": "source_check_failed", "error": type(error).__name__})
    print(json.dumps({"checked_at": today.isoformat(), "unique_sources": len(urls), "findings": findings, "human_review_required_before_changes": True}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
