#!/usr/bin/env python3
"""
Fetch BLS public API employment data and translate the surprise into a
simple Fed rate-expectation read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class NfpData:
    release_title: str
    embargo_line: str
    payrolls_k: Optional[int]
    unemployment_rate: Optional[float]
    labor_force_participation_rate: Optional[float]
    average_hourly_earnings_mom: Optional[float]
    average_hourly_earnings_yoy: Optional[float]
    revision_combined_k: Optional[int]
    revision_text: str
    source_url: str
    fetched_at_utc: str
    series_values: dict[str, float]


@dataclass
class RateSignal:
    direction: str
    score: int
    confidence: str
    reasons: list[str]
    missing_expectations: list[str]


def read_last_release(state_file: str) -> str:
    if not state_file:
        return ""
    path = Path(state_file)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("last_release_title", ""))


def write_last_release(state_file: str, data: NfpData) -> None:
    if not state_file or not data.release_title:
        return
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_release_title": data.release_title,
        "last_seen_at_utc": data.fetched_at_utc,
        "source_url": data.source_url,
        "series_values": data.series_values,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_state(state_file: str) -> dict:
    if not state_file:
        return {}
    path = Path(state_file)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_bls_api() -> dict:
    current_year = datetime.now().year
    payload = {
        "seriesid": [
            "CES0000000001",
            "LNS14000000",
            "LNS11300000",
            "CES0500000003",
        ],
        "startyear": str(current_year - 1),
        "endyear": str(current_year),
    }
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        BLS_API_URL,
        data=body,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalize_month_target(target: str) -> str:
    return re.sub(r"\s+", " ", target.strip()).upper()


def period_key(item: dict) -> str:
    return f"{item['year']}-{item['period']}"


def month_title(item: dict) -> str:
    return f"{item['periodName']} {item['year']}"


def previous_month_key(year: int, month: int, offset: int = 1) -> str:
    month -= offset
    while month <= 0:
        month += 12
        year -= 1
    return f"{year}-M{month:02d}"


def series_map(api_payload: dict) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for series in api_payload.get("Results", {}).get("series", []):
        sid = series.get("seriesID", "")
        output[sid] = {}
        for item in series.get("data", []):
            if item.get("period", "").startswith("M"):
                try:
                    output[sid][period_key(item)] = float(item["value"])
                except (TypeError, ValueError):
                    continue
    return output


def latest_item(api_payload: dict, series_id: str) -> Optional[dict]:
    for series in api_payload.get("Results", {}).get("series", []):
        if series.get("seriesID") == series_id:
            monthly = [item for item in series.get("data", []) if item.get("period", "").startswith("M")]
            if not monthly:
                return None
            return max(monthly, key=lambda item: (int(item["year"]), int(item["period"][1:])))
    return None


def extract_api_release(api_payload: dict, source_url: str, state_file: str) -> NfpData:
    series = series_map(api_payload)
    latest = latest_item(api_payload, "CES0000000001")
    if not latest:
        raise ValueError("BLS API did not return total nonfarm payroll data.")

    year = int(latest["year"])
    month = int(latest["period"][1:])
    current_key = f"{year}-M{month:02d}"
    prev_key = previous_month_key(year, month)
    yoy_key = previous_month_key(year, month, 12)

    payroll_level = series.get("CES0000000001", {}).get(current_key)
    prev_payroll_level = series.get("CES0000000001", {}).get(prev_key)
    payrolls_k = int(round(payroll_level - prev_payroll_level)) if payroll_level and prev_payroll_level else None

    ahe = series.get("CES0500000003", {}).get(current_key)
    prev_ahe = series.get("CES0500000003", {}).get(prev_key)
    yoy_ahe = series.get("CES0500000003", {}).get(yoy_key)
    ahe_mom = round((ahe / prev_ahe - 1) * 100, 1) if ahe and prev_ahe else None
    ahe_yoy = round((ahe / yoy_ahe - 1) * 100, 1) if ahe and yoy_ahe else None

    state = read_state(state_file)
    old_values = state.get("series_values", {})
    revision_sum = 0
    revision_count = 0
    for key in (prev_key, previous_month_key(year, month, 2)):
        current_value = series.get("CES0000000001", {}).get(key)
        old_value = old_values.get(f"CES0000000001:{key}")
        if current_value is not None and old_value is not None:
            revision_sum += int(round(current_value - float(old_value)))
            revision_count += 1

    flat_values = {}
    for sid, values in series.items():
        for key, value in values.items():
            flat_values[f"{sid}:{key}"] = value

    return NfpData(
        release_title=month_title(latest),
        embargo_line="BLS API latest monthly data",
        payrolls_k=payrolls_k,
        unemployment_rate=series.get("LNS14000000", {}).get(current_key),
        labor_force_participation_rate=series.get("LNS11300000", {}).get(current_key),
        average_hourly_earnings_mom=ahe_mom,
        average_hourly_earnings_yoy=ahe_yoy,
        revision_combined_k=revision_sum if revision_count else None,
        revision_text="Computed from stored prior BLS API values." if revision_count else "",
        source_url=source_url,
        fetched_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        series_values=flat_values,
    )


def is_target_release(data: NfpData, target_release: str) -> bool:
    if not target_release:
        return True
    return normalize_month_target(data.release_title) == normalize_month_target(target_release)


def compare_value(
    label: str,
    actual: Optional[float],
    expected: Optional[float],
    higher_is_hawkish: bool,
    threshold: float,
    unit: str,
) -> tuple[int, Optional[str], bool]:
    if actual is None or expected is None:
        return 0, None, expected is None
    diff = actual - expected
    if abs(diff) < threshold:
        return 0, f"{label}基本符合预期：实际 {actual:g}{unit}，预期 {expected:g}{unit}。", False
    hawkish = diff > 0 if higher_is_hawkish else diff < 0
    score = 1 if hawkish else -1
    direction = "偏鹰，压低降息预期" if hawkish else "偏鸽，抬高降息预期"
    return score, f"{label}{direction}：实际 {actual:g}{unit}，预期 {expected:g}{unit}。", False


def analyze_rate_signal(data: NfpData, args: argparse.Namespace) -> RateSignal:
    score = 0
    reasons: list[str] = []
    missing: list[str] = []

    payroll_score, payroll_reason, payroll_missing = compare_value(
        "非农新增",
        data.payrolls_k,
        args.expected_payrolls_k,
        higher_is_hawkish=True,
        threshold=args.payroll_threshold_k,
        unit="k",
    )
    score += payroll_score * 2
    if payroll_reason:
        reasons.append(payroll_reason)
    if payroll_missing:
        missing.append("非农新增预期")

    unemp_score, unemp_reason, unemp_missing = compare_value(
        "失业率",
        data.unemployment_rate,
        args.expected_unemployment,
        higher_is_hawkish=False,
        threshold=0.05,
        unit="%",
    )
    score += unemp_score * 2
    if unemp_reason:
        reasons.append(unemp_reason)
    if unemp_missing:
        missing.append("失业率预期")

    wage_score, wage_reason, wage_missing = compare_value(
        "平均时薪环比",
        data.average_hourly_earnings_mom,
        args.expected_ahe_mom,
        higher_is_hawkish=True,
        threshold=0.05,
        unit="%",
    )
    score += wage_score
    if wage_reason:
        reasons.append(wage_reason)
    if wage_missing:
        missing.append("平均时薪环比预期")

    if data.revision_combined_k is not None:
        if data.revision_combined_k >= args.revision_threshold_k:
            score += 1
            reasons.append(f"前值合计上修 {data.revision_combined_k}k，偏鹰。")
        elif data.revision_combined_k <= -args.revision_threshold_k:
            score -= 1
            reasons.append(f"前值合计下修 {abs(data.revision_combined_k)}k，偏鸽。")
        else:
            reasons.append(f"前值修正幅度不大：{data.revision_combined_k:+d}k。")

    if score >= 2:
        direction = "降息预期下降"
    elif score <= -2:
        direction = "降息预期上升"
    else:
        direction = "信号混合，降息预期变化有限"

    confidence = "高" if len(missing) == 0 else "中" if len(missing) <= 2 else "低"
    if not reasons:
        reasons.append("缺少市场预期输入，只能先抓取数据，暂不做强判断。")

    return RateSignal(direction=direction, score=score, confidence=confidence, reasons=reasons, missing_expectations=missing)


def _value_or_unknown(value: Optional[object], suffix: str = "") -> str:
    if value is None:
        return "\u672a\u89e3\u6790\u5230"
    return f"{value}{suffix}"


def render_markdown(data: NfpData, signal: RateSignal) -> str:
    unknown = _value_or_unknown(None)
    payroll = _value_or_unknown(data.payrolls_k, "k")
    unemployment = _value_or_unknown(f"{data.unemployment_rate:g}" if data.unemployment_rate is not None else None, "%")
    participation = _value_or_unknown(
        f"{data.labor_force_participation_rate:g}" if data.labor_force_participation_rate is not None else None,
        "%",
    )
    ahe_mom = _value_or_unknown(
        f"{data.average_hourly_earnings_mom:g}" if data.average_hourly_earnings_mom is not None else None,
        "%",
    )
    ahe_yoy = _value_or_unknown(
        f"{data.average_hourly_earnings_yoy:g}" if data.average_hourly_earnings_yoy is not None else None,
        "%",
    )
    revision = _value_or_unknown(f"{data.revision_combined_k:+d}" if data.revision_combined_k is not None else None, "k")
    reasons = "\n".join(f"- {reason}" for reason in signal.reasons)
    missing = "\u3001".join(signal.missing_expectations) if signal.missing_expectations else "\u65e0"

    return f"""# \u7f8e\u56fd\u975e\u519c\u5feb\u901f\u89e3\u8bfb

\u53d1\u5e03\u65f6\u95f4\uff1a{data.embargo_line or unknown}
\u62a5\u544a\u6708\u4efd\uff1a{data.release_title or unknown}
\u6293\u53d6\u65f6\u95f4 UTC\uff1a{data.fetched_at_utc}
\u5b98\u65b9\u6765\u6e90\uff1a{data.source_url}

## \u6838\u5fc3\u6570\u636e

- \u975e\u519c\u65b0\u589e\u5c31\u4e1a\uff1a{payroll}
- \u5931\u4e1a\u7387\uff1a{unemployment}
- \u52b3\u52a8\u53c2\u4e0e\u7387\uff1a{participation}
- \u5e73\u5747\u65f6\u85aa\u73af\u6bd4\uff1a{ahe_mom}
- \u5e73\u5747\u65f6\u85aa\u540c\u6bd4\uff1a{ahe_yoy}
- \u524d\u4e24\u6708\u5408\u8ba1\u4fee\u6b63\uff1a{revision}

## \u964d\u606f\u9884\u671f\u5224\u65ad

\u7ed3\u8bba\uff1a**{signal.direction}**
\u7f6e\u4fe1\u5ea6\uff1a{signal.confidence}
\u6253\u5206\uff1a{signal.score}

{reasons}

\u7f3a\u5c11\u7684\u5e02\u573a\u9884\u671f\u8f93\u5165\uff1a{missing}
"""


def render_raw_markdown(data: NfpData) -> str:
    unknown = _value_or_unknown(None)
    payroll = _value_or_unknown(data.payrolls_k, "k")
    unemployment = _value_or_unknown(f"{data.unemployment_rate:g}" if data.unemployment_rate is not None else None, "%")
    participation = _value_or_unknown(
        f"{data.labor_force_participation_rate:g}" if data.labor_force_participation_rate is not None else None,
        "%",
    )
    ahe_mom = _value_or_unknown(
        f"{data.average_hourly_earnings_mom:g}" if data.average_hourly_earnings_mom is not None else None,
        "%",
    )
    ahe_yoy = _value_or_unknown(
        f"{data.average_hourly_earnings_yoy:g}" if data.average_hourly_earnings_yoy is not None else None,
        "%",
    )
    revision = _value_or_unknown(f"{data.revision_combined_k:+d}" if data.revision_combined_k is not None else None, "k")

    return f"""# \u7f8e\u56fd\u975e\u519c\u539f\u59cb\u6570\u636e\u5feb\u62a5

\u53d1\u5e03\u65f6\u95f4\uff1a{data.embargo_line or unknown}
\u62a5\u544a\u6708\u4efd\uff1a{data.release_title or unknown}
\u6293\u53d6\u65f6\u95f4 UTC\uff1a{data.fetched_at_utc}
\u5b98\u65b9\u6765\u6e90\uff1a{data.source_url}

## \u6838\u5fc3\u6570\u636e

- \u975e\u519c\u65b0\u589e\u5c31\u4e1a\uff1a{payroll}
- \u5931\u4e1a\u7387\uff1a{unemployment}
- \u52b3\u52a8\u53c2\u4e0e\u7387\uff1a{participation}
- \u5e73\u5747\u65f6\u85aa\u73af\u6bd4\uff1a{ahe_mom}
- \u5e73\u5747\u65f6\u85aa\u540c\u6bd4\uff1a{ahe_yoy}
- \u524d\u4e24\u6708\u5408\u8ba1\u4fee\u6b63\uff1a{revision}
"""


def write_outputs(data: NfpData, signal: RateSignal, output_dir: Path, raw_markdown: str, analysis_markdown: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {"data": asdict(data), "signal": asdict(signal)}
    (output_dir / f"nfp-{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"nfp-raw-{stamp}.md").write_text(raw_markdown, encoding="utf-8")
    (output_dir / f"nfp-analysis-{stamp}.md").write_text(analysis_markdown, encoding="utf-8")


def run_once(args: argparse.Namespace) -> tuple[bool, Optional[NfpData], Optional[RateSignal]]:
    data = extract_api_release(fetch_bls_api(), BLS_API_URL, args.state_file)
    unknown_title = "\u672a\u77e5"
    if args.mark_current_seen:
        write_last_release(args.state_file, data)
        print(f"\u5df2\u8bb0\u5f55\u5f53\u524d\u62a5\u544a\u4e3a\u5df2\u5904\u7406\uff1a{data.release_title or unknown_title}", flush=True)
        return True, data, None
    if args.only_new:
        last_release = read_last_release(args.state_file)
        if not last_release:
            if args.target_release and is_target_release(data, args.target_release):
                pass
            else:
                write_last_release(args.state_file, data)
                print(
                    f"\u9996\u6b21\u8fd0\u884c\uff0c\u5df2\u8bb0\u5f55\u5f53\u524d\u62a5\u544a\u4e3a\u57fa\u7ebf\uff1a{data.release_title or unknown_title}\uff0c\u7ee7\u7eed\u7b49\u5f85\u65b0\u62a5\u544a\u3002",
                    flush=True,
                )
                return False, data, None
        elif normalize_month_target(data.release_title) == normalize_month_target(last_release):
            write_last_release(args.state_file, data)
            print(
                f"\u5f53\u524d\u4ecd\u662f\u5df2\u5904\u7406\u62a5\u544a\uff1a{data.release_title or unknown_title}\uff0c\u7ee7\u7eed\u7b49\u5f85\u65b0\u62a5\u544a\u3002",
                flush=True,
            )
            return False, data, None
    if not is_target_release(data, args.target_release):
        print(
            f"\u5c1a\u672a\u66f4\u65b0\u5230\u76ee\u6807\u62a5\u544a\uff1a\u5f53\u524d\u662f {data.release_title or unknown_title}\uff0c\u76ee\u6807\u662f {args.target_release}",
            flush=True,
        )
        return False, data, None
    raw_markdown = render_raw_markdown(data)

    signal = analyze_rate_signal(data, args)
    analysis_markdown = render_markdown(data, signal)
    print(analysis_markdown, flush=True)
    if args.output_dir:
        write_outputs(data, signal, Path(args.output_dir), raw_markdown, analysis_markdown)
    if args.only_new:
        write_last_release(args.state_file, data)
    return True, data, signal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BLS nonfarm payrolls monitor and Fed-cut signal analyzer.")
    parser.add_argument("--target-release", default="", help='Expected report title, for example "June 2026". Empty means accept the next new release.')
    parser.add_argument("--watch", action="store_true", help="Poll until target release appears.")
    parser.add_argument("--interval-seconds", type=int, default=5, help="Polling interval in watch mode.")
    parser.add_argument("--timeout-seconds", type=int, default=1200, help="Maximum watch time.")
    parser.add_argument("--only-new", action="store_true", help="Only analyze when the BLS page changes to a release not recorded in the state file.")
    parser.add_argument("--state-file", default="state/nfp_state.json", help="State file used by --only-new.")
    parser.add_argument("--mark-current-seen", action="store_true", help="Record the currently visible BLS release in the state file, then exit.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for Markdown and JSON output. Empty string disables files.")
    parser.add_argument("--expected-payrolls-k", type=float, default=None, help="Consensus NFP forecast in thousands.")
    parser.add_argument("--expected-unemployment", type=float, default=None, help="Consensus unemployment-rate forecast, percent.")
    parser.add_argument("--expected-ahe-mom", type=float, default=None, help="Consensus average hourly earnings MoM forecast, percent.")
    parser.add_argument("--payroll-threshold-k", type=float, default=50, help="Payroll surprise threshold in thousands.")
    parser.add_argument("--revision-threshold-k", type=int, default=50, help="Combined revision threshold in thousands.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = time.monotonic()
    while True:
        try:
            released, _, _ = run_once(args)
            if released or not args.watch:
                return 0 if released else 2
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"\u6293\u53d6\u5931\u8d25\uff0c\u7a0d\u540e\u91cd\u8bd5\uff1a{exc}", file=sys.stderr, flush=True)
            if not args.watch:
                return 1

        if time.monotonic() - start >= args.timeout_seconds:
            print("\u76d1\u63a7\u8d85\u65f6\uff1a\u76ee\u6807\u62a5\u544a\u4ecd\u672a\u51fa\u73b0\u5728 BLS \u5f53\u524d\u53d1\u5e03\u9875\u3002", file=sys.stderr, flush=True)
            return 3
        time.sleep(args.interval_seconds)

if __name__ == "__main__":
    raise SystemExit(main())
