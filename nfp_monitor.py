#!/usr/bin/env python3
"""
Fetch BLS public API employment data and translate the surprise into a
simple Fed rate-expectation read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)


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


def render_markdown(data: NfpData, signal: RateSignal) -> str:
    payroll = f"{data.payrolls_k}k" if data.payrolls_k is not None else "未解析到"
    unemployment = f"{data.unemployment_rate:g}%" if data.unemployment_rate is not None else "未解析到"
    participation = (
        f"{data.labor_force_participation_rate:g}%"
        if data.labor_force_participation_rate is not None
        else "未解析到"
    )
    ahe_mom = (
        f"{data.average_hourly_earnings_mom:g}%"
        if data.average_hourly_earnings_mom is not None
        else "未解析到"
    )
    ahe_yoy = (
        f"{data.average_hourly_earnings_yoy:g}%"
        if data.average_hourly_earnings_yoy is not None
        else "未解析到"
    )
    revision = f"{data.revision_combined_k:+d}k" if data.revision_combined_k is not None else "未解析到"
    reasons = "\n".join(f"- {reason}" for reason in signal.reasons)
    missing = "、".join(signal.missing_expectations) if signal.missing_expectations else "无"

    return f"""# 美国非农快速解读

发布时间：{data.embargo_line or "未解析到"}
报告月份：{data.release_title or "未解析到"}
抓取时间 UTC：{data.fetched_at_utc}
官方来源：{data.source_url}

## 核心数据

- 非农新增就业：{payroll}
- 失业率：{unemployment}
- 劳动参与率：{participation}
- 平均时薪环比：{ahe_mom}
- 平均时薪同比：{ahe_yoy}
- 前两月合计修正：{revision}

## 降息预期判断

结论：**{signal.direction}**
置信度：{signal.confidence}
打分：{signal.score}

{reasons}

缺少的市场预期输入：{missing}
"""


def render_raw_markdown(data: NfpData) -> str:
    payroll = f"{data.payrolls_k}k" if data.payrolls_k is not None else "未解析到"
    unemployment = f"{data.unemployment_rate:g}%" if data.unemployment_rate is not None else "未解析到"
    participation = (
        f"{data.labor_force_participation_rate:g}%"
        if data.labor_force_participation_rate is not None
        else "未解析到"
    )
    ahe_mom = (
        f"{data.average_hourly_earnings_mom:g}%"
        if data.average_hourly_earnings_mom is not None
        else "未解析到"
    )
    ahe_yoy = (
        f"{data.average_hourly_earnings_yoy:g}%"
        if data.average_hourly_earnings_yoy is not None
        else "未解析到"
    )
    revision = f"{data.revision_combined_k:+d}k" if data.revision_combined_k is not None else "未解析到"

    return f"""# 美国非农原始数据快报

发布时间：{data.embargo_line or "未解析到"}
报告月份：{data.release_title or "未解析到"}
抓取时间 UTC：{data.fetched_at_utc}
官方来源：{data.source_url}

## 核心数据

- 非农新增就业：{payroll}
- 失业率：{unemployment}
- 劳动参与率：{participation}
- 平均时薪环比：{ahe_mom}
- 平均时薪同比：{ahe_yoy}
- 前两月合计修正：{revision}
"""


def send_email(args: argparse.Namespace, subject: str, body: str) -> None:
    if not args.email_to:
        return

    missing = [
        name
        for name, value in {
            "SMTP_HOST": args.smtp_host,
            "SMTP_USERNAME": args.smtp_username,
            "SMTP_PASSWORD": args.smtp_password,
        }.items()
        if not value
    ]
    if missing:
        print(f"邮件未发送，缺少配置：{', '.join(missing)}", file=sys.stderr, flush=True)
        return

    sender = args.email_from or args.smtp_username
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = args.email_to
    message.set_content(body, subtype="plain", charset="utf-8")

    if args.smtp_ssl:
        with smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=30) as smtp:
            smtp.login(args.smtp_username, args.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=30) as smtp:
            if not args.smtp_no_tls:
                smtp.starttls()
            smtp.login(args.smtp_username, args.smtp_password)
            smtp.send_message(message)
    print(f"邮件已发送：{subject}", flush=True)


def write_outputs(data: NfpData, signal: RateSignal, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {"data": asdict(data), "signal": asdict(signal)}
    (output_dir / f"nfp-{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"nfp-{stamp}.md").write_text(render_markdown(data, signal), encoding="utf-8")


def run_once(args: argparse.Namespace) -> tuple[bool, Optional[NfpData], Optional[RateSignal]]:
    data = extract_api_release(fetch_bls_api(), BLS_API_URL, args.state_file)
    if args.mark_current_seen:
        write_last_release(args.state_file, data)
        print(f"已记录当前报告为已处理：{data.release_title or '未知'}", flush=True)
        return True, data, None
    if args.only_new:
        last_release = read_last_release(args.state_file)
        if not last_release:
            if args.target_release and is_target_release(data, args.target_release):
                pass
            else:
                write_last_release(args.state_file, data)
                print(
                    f"首次运行，已记录当前报告为基线：{data.release_title or '未知'}，继续等待新报告。",
                    flush=True,
                )
                return False, data, None
        elif normalize_month_target(data.release_title) == normalize_month_target(last_release):
            write_last_release(args.state_file, data)
            print(
                f"当前仍是已处理报告：{data.release_title or '未知'}，继续等待新报告。",
                flush=True,
            )
            return False, data, None
    if not is_target_release(data, args.target_release):
        print(
            f"尚未更新到目标报告：当前是 {data.release_title or '未知'}，目标是 {args.target_release}",
            flush=True,
        )
        return False, data, None
    raw_markdown = render_raw_markdown(data)
    send_email(args, f"美国非农原始数据：{data.release_title}", raw_markdown)

    signal = analyze_rate_signal(data, args)
    analysis_markdown = render_markdown(data, signal)
    print(analysis_markdown, flush=True)
    send_email(args, f"美国非农降息预期判断：{signal.direction}", analysis_markdown)
    if args.output_dir:
        write_outputs(data, signal, Path(args.output_dir))
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
    parser.add_argument("--email-to", default=os.getenv("NFP_EMAIL_TO", ""), help="Recipient email address. Defaults to NFP_EMAIL_TO.")
    parser.add_argument("--email-from", default=os.getenv("NFP_EMAIL_FROM", ""), help="Sender email address. Defaults to NFP_EMAIL_FROM or SMTP username.")
    parser.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", ""), help="SMTP host. Defaults to SMTP_HOST.")
    parser.add_argument("--smtp-port", type=int, default=env_int("SMTP_PORT", 587), help="SMTP port. Defaults to SMTP_PORT or 587.")
    parser.add_argument("--smtp-username", default=os.getenv("SMTP_USERNAME", ""), help="SMTP username. Defaults to SMTP_USERNAME.")
    parser.add_argument("--smtp-password", default=os.getenv("SMTP_PASSWORD", ""), help="SMTP password or app password. Defaults to SMTP_PASSWORD.")
    parser.add_argument("--smtp-ssl", action="store_true", default=env_bool("SMTP_SSL", False), help="Use SMTP over SSL, usually port 465.")
    parser.add_argument("--smtp-no-tls", action="store_true", default=env_bool("SMTP_NO_TLS", False), help="Disable STARTTLS for non-SSL SMTP.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = time.monotonic()
    while True:
        try:
            released, _, _ = run_once(args)
            if released or not args.watch:
                return 0 if released else 2
        except (URLError, TimeoutError, OSError) as exc:
            print(f"抓取失败，稍后重试：{exc}", file=sys.stderr, flush=True)
            if not args.watch:
                return 1

        if time.monotonic() - start >= args.timeout_seconds:
            print("监控超时：目标报告仍未出现在 BLS 当前发布页。", file=sys.stderr, flush=True)
            return 3
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
