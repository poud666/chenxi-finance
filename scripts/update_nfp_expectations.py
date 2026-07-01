#!/usr/bin/env python3
"""Fetch NFP consensus expectations and store them for release-day analysis."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TE_NFP_URL = "https://tradingeconomics.com/united-states/non-farm-payrolls"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update NFP consensus expectations.")
    parser.add_argument("--calendar", default=".github/nfp_release_dates.json")
    parser.add_argument("--expectations", default=".github/nfp_expectations.json")
    parser.add_argument("--date", default="", help="Override HK date in YYYY-MM-DD for tests.")
    return parser.parse_args()


def hk_today(override: str) -> date:
    if override:
        return date.fromisoformat(override)
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date()


def reference_month_for_date(run_date: date) -> str:
    first = run_date.replace(day=1)
    previous = first - timedelta(days=1)
    return previous.strftime("%B %Y")


def target_release_from_calendar(calendar_path: Path, run_date: date) -> str:
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
    for item in calendar.get("dates", []):
        release_date = date.fromisoformat(item["release_date_hk"])
        if release_date.year == run_date.year and release_date.month == run_date.month:
            return item["reference_month"]
    return reference_month_for_date(run_date)


def fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_expectations(page_text: str) -> dict[str, float]:
    clean = clean_html(page_text)
    payroll = re.search(r"expected to have added\s+([0-9.]+)\s*K jobs", clean, flags=re.IGNORECASE)
    unemployment = re.search(
        r"unemployment rate is forecast to (?:remain unchanged at|rise to|fall to|edge up to|edge down to)\s+([0-9.]+)%",
        clean,
        flags=re.IGNORECASE,
    )
    ahe_mom = re.search(
        r"Average hourly earnings are expected to rise by\s+([0-9.]+)%\s+month-over-month",
        clean,
        flags=re.IGNORECASE,
    )
    missing = []
    if not payroll:
        missing.append("payrolls")
    if not unemployment:
        missing.append("unemployment")
    if not ahe_mom:
        missing.append("average hourly earnings MoM")
    if missing:
        raise ValueError(f"Could not parse expectations: {', '.join(missing)}")

    return {
        "expected_payrolls_k": float(payroll.group(1)),
        "expected_unemployment": float(unemployment.group(1)),
        "expected_ahe_mom": float(ahe_mom.group(1)),
    }


def read_expectations(path: Path) -> dict:
    if not path.exists():
        return {
            "source": TE_NFP_URL,
            "description": "NFP consensus expectations captured before release day.",
            "expectations": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_expectation(payload: dict, reference_month: str, values: dict[str, float], run_date: date) -> None:
    item = {
        "reference_month": reference_month,
        **values,
        "source": TE_NFP_URL,
        "captured_date_hk": run_date.isoformat(),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    entries = [
        entry
        for entry in payload.get("expectations", [])
        if entry.get("reference_month") != reference_month
    ]
    entries.append(item)
    payload["expectations"] = entries
    payload["source"] = TE_NFP_URL
    payload["last_checked_hk"] = run_date.isoformat()


def main() -> int:
    args = parse_args()
    run_date = hk_today(args.date)
    calendar_path = Path(args.calendar)
    expectations_path = Path(args.expectations)
    reference_month = target_release_from_calendar(calendar_path, run_date)

    values = parse_expectations(fetch_text(TE_NFP_URL))
    payload = read_expectations(expectations_path)
    upsert_expectation(payload, reference_month, values, run_date)
    expectations_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Updated NFP expectations for "
        f"{reference_month}: payrolls {values['expected_payrolls_k']}k, "
        f"unemployment {values['expected_unemployment']}%, "
        f"AHE MoM {values['expected_ahe_mom']}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
