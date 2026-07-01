#!/usr/bin/env python3
"""Update the local NFP release calendar from the BLS schedule."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BLS_SOURCES = [
    "https://www.bls.gov/schedule/news_release/empsit.htm",
    "https://www.bls.gov/schedule/news_release/current_year.asp",
    "https://www.bls.gov/schedule/news_release/bls.ics",
]

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update BLS NFP release dates.")
    parser.add_argument("--calendar", default=".github/nfp_release_dates.json")
    parser.add_argument("--date", default="", help="Override HK date in YYYY-MM-DD for tests.")
    return parser.parse_args()


def hk_today(override: str) -> date:
    if override:
        return date.fromisoformat(override)
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date()


def reference_month_for_release(run_date: date) -> str:
    first = run_date.replace(day=1)
    previous = first - timedelta(days=1)
    return previous.strftime("%B %Y")


def fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,text/calendar,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=25) as response:
        text = response.read().decode("utf-8", errors="replace")
    if "Access Denied" in text and "Employment Situation" not in text:
        raise HTTPError(url, 403, "Access Denied", hdrs=None, fp=None)
    return text


def html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text)


def parse_ics_events(text: str) -> Iterable[date]:
    events = re.split(r"BEGIN:VEVENT", text, flags=re.IGNORECASE)
    eastern = ZoneInfo("America/New_York")
    hk = ZoneInfo("Asia/Hong_Kong")
    for event in events:
        if "Employment Situation" not in event:
            continue
        match = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})(?:T(\d{6})Z?)?", event)
        if not match:
            continue
        stamp = match.group(1)
        time_part = match.group(2) or "083000"
        local_dt = datetime.strptime(stamp + time_part, "%Y%m%d%H%M%S").replace(tzinfo=eastern)
        yield local_dt.astimezone(hk).date()


def parse_text_dates(text: str) -> Iterable[date]:
    clean = html_to_text(text)
    pattern = re.compile(
        r"(?:Employment Situation|THE EMPLOYMENT SITUATION).{0,160}?"
        r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s+(\d{4})",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(clean):
        month_name = match.group(1).lower().rstrip(".")
        month = MONTHS.get(month_name)
        if month:
            yield date(int(match.group(3)), month, int(match.group(2)))


def first_friday(year: int, month: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != 4:
        current += timedelta(days=1)
    return current


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def us_federal_holidays(year: int) -> set[date]:
    return {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        last_weekday(year, 5, 0),
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 10, 0, 2),
        observed_fixed_holiday(year, 11, 11),
        nth_weekday(year, 11, 3, 4),
        observed_fixed_holiday(year, 12, 25),
    }


def fallback_release_date(year: int, month: int) -> date:
    candidate = first_friday(year, month)
    holidays = us_federal_holidays(year)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate -= timedelta(days=1)
    return candidate


def find_bls_release_date(run_date: date) -> tuple[date, str]:
    candidates: list[date] = []
    errors: list[str] = []
    for source in BLS_SOURCES:
        try:
            text = fetch_text(source)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        parser = parse_ics_events if source.endswith(".ics") else parse_text_dates
        candidates.extend(parser(text))

    month_candidates = sorted({item for item in candidates if item.year == run_date.year and item.month == run_date.month})
    if month_candidates:
        return month_candidates[0], "bls_schedule"

    fallback = fallback_release_date(run_date.year, run_date.month)
    print("BLS schedule lookup did not return this month; using rule-based fallback.")
    for error in errors:
        print(error)
    return fallback, "fallback_first_friday_adjusted_for_us_holidays"


def upsert_date(calendar: dict, release_date: date, reference_month: str, source_mode: str) -> None:
    item = {
        "release_date_hk": release_date.isoformat(),
        "reference_month": reference_month,
        "verified_by": source_mode,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    dates = [entry for entry in calendar.get("dates", []) if entry.get("release_date_hk") != item["release_date_hk"]]
    dates = [entry for entry in dates if entry.get("reference_month") != reference_month]
    dates.append(item)
    dates.sort(key=lambda entry: entry["release_date_hk"])
    calendar["dates"] = dates


def main() -> int:
    args = parse_args()
    run_date = hk_today(args.date)
    calendar_path = Path(args.calendar)
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))

    release_date, source_mode = find_bls_release_date(run_date)
    reference_month = reference_month_for_release(run_date)
    upsert_date(calendar, release_date, reference_month, source_mode)

    calendar["source"] = BLS_SOURCES[0]
    calendar["last_checked_hk"] = run_date.isoformat()
    calendar_path.write_text(json.dumps(calendar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {reference_month}: release date {release_date.isoformat()} HK ({source_mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
