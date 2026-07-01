#!/usr/bin/env python3
"""Check whether today is a configured BLS Employment Situation release day."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate NFP workflow runs by release date.")
    parser.add_argument("--calendar", default=".github/nfp_release_dates.json")
    parser.add_argument("--date", default="", help="Override date in YYYY-MM-DD for tests.")
    parser.add_argument("--github-output", default="", help="Path from GITHUB_OUTPUT.")
    return parser.parse_args()


def write_outputs(path: str, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = parse_args()
    calendar = json.loads(Path(args.calendar).read_text(encoding="utf-8"))
    timezone = ZoneInfo(calendar.get("timezone", "Asia/Hong_Kong"))
    today = args.date or datetime.now(timezone).date().isoformat()

    match = next((item for item in calendar["dates"] if item["release_date_hk"] == today), None)
    outputs = {
        "is_release_day": "true" if match else "false",
        "today_hk": today,
        "target_release": match["reference_month"] if match else "",
    }
    write_outputs(args.github_output, outputs)

    if match:
        print(f"NFP release day: {today} HK, target {match['reference_month']}")
    else:
        print(f"Not an NFP release day in configured calendar: {today} HK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
