#!/usr/bin/env python3
"""Read stored NFP expectations for GitHub Actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read stored NFP expectations.")
    parser.add_argument("--expectations", default=".github/nfp_expectations.json")
    parser.add_argument("--target-release", required=True)
    parser.add_argument("--github-output", default="")
    return parser.parse_args()


def write_outputs(path: str, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.expectations).read_text(encoding="utf-8"))
    match = next(
        (
            item
            for item in payload.get("expectations", [])
            if item.get("reference_month", "").lower() == args.target_release.lower()
        ),
        None,
    )
    if not match:
        print(f"No stored expectations for {args.target_release}")
        write_outputs(args.github_output, {"found": "false"})
        return 0
    parsed_reference_month = match.get("parsed_reference_month", match.get("reference_month", ""))
    if parsed_reference_month.lower() != args.target_release.lower():
        raise ValueError(
            "Stored expectation month mismatch: "
            f"target is {args.target_release}, stored source month is {parsed_reference_month}."
        )

    outputs = {
        "found": "true",
        "expected_payrolls_k": str(match["expected_payrolls_k"]),
        "expected_unemployment": str(match["expected_unemployment"]),
        "expected_ahe_mom": str(match["expected_ahe_mom"]),
        "expectations_source": str(match.get("source", "")),
        "expectations_reference_month": parsed_reference_month,
    }
    write_outputs(args.github_output, outputs)
    print(
        f"Stored expectations for {args.target_release}: "
        f"{outputs['expected_payrolls_k']}k, "
        f"{outputs['expected_unemployment']}%, "
        f"{outputs['expected_ahe_mom']}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
