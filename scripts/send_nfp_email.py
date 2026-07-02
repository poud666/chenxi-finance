#!/usr/bin/env python3
"""
Send the latest NFP raw-data and analysis Markdown files as two real emails.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


DEFAULT_RECIPIENTS = "lbs20060607@gmail.com,kyo1143845969@gmail.com"


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value or default


def recipients(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def latest_file(output_dir: Path, pattern: str) -> Path:
    matches = sorted(output_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        raise FileNotFoundError(f"No file matched {output_dir / pattern}")
    return matches[0]


def normalize_release(value: str) -> str:
    return " ".join(value.strip().lower().split())


def output_files(output_dir: Path) -> tuple[Path, Path, Path, str]:
    json_file = latest_file(output_dir, "nfp-*.json")
    stamp = json_file.stem.removeprefix("nfp-")
    raw_file = output_dir / f"nfp-raw-{stamp}.md"
    analysis_file = output_dir / f"nfp-analysis-{stamp}.md"
    if not raw_file.exists():
        raise FileNotFoundError(f"No raw Markdown file matched {raw_file}")
    if not analysis_file.exists():
        raise FileNotFoundError(f"No analysis Markdown file matched {analysis_file}")
    payload = json.loads(json_file.read_text(encoding="utf-8"))
    release_title = str(payload.get("data", {}).get("release_title", ""))
    return json_file, raw_file, analysis_file, release_title


def build_message(sender: str, to: list[str], subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body, subtype="plain", charset="utf-8")
    return message


def smtp_send(message: EmailMessage, args: argparse.Namespace) -> None:
    if args.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=args.timeout, context=context) as smtp:
            smtp.login(args.smtp_username, args.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=args.timeout) as smtp:
        smtp.ehlo()
        if args.starttls:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(args.smtp_username, args.smtp_password)
        smtp.send_message(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send NFP output files through SMTP.")
    parser.add_argument("--output-dir", default="outputs", help="Directory containing NFP output files.")
    parser.add_argument("--expected-release", default="", help='Required target report title, for example "June 2026".')
    parser.add_argument("--email-to", default=env("NFP_EMAIL_TO", DEFAULT_RECIPIENTS), help="Comma-separated recipients.")
    parser.add_argument("--email-from", default=env("NFP_EMAIL_FROM") or env("NFP_SMTP_USERNAME"), help="Sender address.")
    parser.add_argument("--smtp-host", default=env("NFP_SMTP_HOST", "smtp.gmail.com"), help="SMTP host.")
    parser.add_argument("--smtp-port", type=int, default=int(env("NFP_SMTP_PORT", "587")), help="SMTP port.")
    parser.add_argument("--smtp-username", default=env("NFP_SMTP_USERNAME"), help="SMTP username.")
    parser.add_argument("--smtp-password", default=env("NFP_SMTP_PASSWORD"), help="SMTP password or app password.")
    parser.add_argument("--timeout", type=int, default=30, help="SMTP network timeout in seconds.")
    parser.add_argument("--use-ssl", action="store_true", default=env("NFP_SMTP_SSL").lower() == "true", help="Use SMTP SSL, usually port 465.")
    parser.add_argument("--no-starttls", action="store_true", help="Disable STARTTLS for non-SSL SMTP.")
    parser.add_argument("--dry-run", action="store_true", help="Validate files and config without sending.")
    args = parser.parse_args()
    args.starttls = not args.no_starttls
    return args


def main() -> int:
    args = parse_args()
    json_file, raw_file, analysis_file, release_title = output_files(Path(args.output_dir))

    if not args.expected_release:
        print("Refusing to send email because --expected-release is empty.", file=sys.stderr)
        return 3
    if normalize_release(release_title) != normalize_release(args.expected_release):
        print(
            f"Refusing to send email: output {json_file.name} is {release_title!r}, "
            f"expected {args.expected_release!r}.",
            file=sys.stderr,
        )
        return 3

    to = recipients(args.email_to)
    sender = args.email_from or ("dry-run@example.invalid" if args.dry_run else "")
    required = {"NFP_EMAIL_TO": to, "NFP_EMAIL_FROM or NFP_SMTP_USERNAME": sender}
    if not args.dry_run:
        required.update({"NFP_SMTP_USERNAME": args.smtp_username, "NFP_SMTP_PASSWORD": args.smtp_password})
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"Missing email configuration: {', '.join(missing)}", file=sys.stderr)
        return 2

    raw_body = raw_file.read_text(encoding="utf-8")
    analysis_body = analysis_file.read_text(encoding="utf-8")
    raw_message = build_message(sender, to, "\u7f8e\u56fd\u975e\u519c\u539f\u59cb\u6570\u636e\u5feb\u62a5", raw_body)
    analysis_message = build_message(sender, to, "\u7f8e\u56fd\u975e\u519c\u964d\u606f\u9884\u671f\u5206\u6790", analysis_body)

    if args.dry_run:
        print(
            f"Dry run OK: {release_title} matches {args.expected_release}; "
            f"would send {raw_file.name} and {analysis_file.name} to {', '.join(to)}"
        )
        return 0

    smtp_send(raw_message, args)
    print(f"Sent raw NFP email to {', '.join(to)}")
    smtp_send(analysis_message, args)
    print(f"Sent NFP analysis email to {', '.join(to)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
