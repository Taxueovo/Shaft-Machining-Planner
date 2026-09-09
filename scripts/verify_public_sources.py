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
# 厂商官方域名白名单：能力来源链接仅接受这些站点
ALLOWED_HOSTS = {
    "us.dmgmori.com",
    "en.dmgmori.com",
    "www.kapp-niles.com",
    "www.gleason.com",
    "www.iscar.com",
}


def as_date(value: object) -> date | None:
    """把单元格值统一转为 date：datetime 取日期部分、字符串按 ISO 解析，解析失败返回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def workbook_records(path: Path, sheet: str) -> list[dict[str, object]]:
    """以只读方式读取工作簿，返回表头->单元格值的字典列表；data_only 保证取到计算后的静态值而非公式。"""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = workbook[sheet].iter_rows(values_only=True)
    headers = [str(value or "") for value in next(rows)]
    return [dict(zip(headers, row)) for row in rows]


def main() -> int:
    """逐行核对公共工作簿来源：URL 须属官方域名白名单且带近期人工复核；--online 时再实际验证链接可达性。"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--online", action="store_true", help="also confirm that each official URL responds"
    )
    parser.add_argument("--max-age-days", type=int, default=180)
    args = parser.parse_args()
    findings: list[dict[str, object]] = []
    urls: set[str] = set()
    today = datetime.now(timezone.utc).date()
    for path, sheet in WORKBOOKS:
        for index, row in enumerate(workbook_records(path, sheet), start=2):
            url = next(
                (
                    str(value).strip()
                    for key, value in row.items()
                    if "url" in key.lower() and value
                ),
                "",
            )
            checked = next(
                (as_date(value) for key, value in row.items() if "checked" in key.lower()), None
            )
            # 无来源链接、非 https 或域名不在官方白名单内，一律视为来源不合规
            if (
                not url
                or urlparse(url).scheme != "https"
                or urlparse(url).hostname not in ALLOWED_HOSTS
            ):
                findings.append(
                    {"file": path.name, "row": index, "issue": "missing_or_unapproved_official_url"}
                )
            else:
                urls.add(url)
            # 未记录复核日期或复核距今已超过期限，变更前需人工重新核对
            if checked is None or (today - checked).days > args.max_age_days:
                findings.append(
                    {
                        "file": path.name,
                        "row": index,
                        "issue": "source_review_stale",
                        "checked": str(checked),
                    }
                )
    # 在线校验为可选项（默认不联网）；仅用 Range 请求每个 URL 的前 4KB 判断其可达性，避免整页下载
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
                        findings.append(
                            {"url": url, "issue": "source_unreachable", "status": response.status}
                        )
            except HTTPError as error:
                findings.append({"url": url, "issue": "source_unreachable", "status": error.code})
            except (URLError, TimeoutError) as error:
                findings.append(
                    {"url": url, "issue": "source_check_failed", "error": type(error).__name__}
                )
    print(
        json.dumps(
            {
                "checked_at": today.isoformat(),
                "unique_sources": len(urls),
                "findings": findings,
                "human_review_required_before_changes": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
