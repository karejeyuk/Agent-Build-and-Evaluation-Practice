#!/usr/bin/env python3
"""연간 절감 시간 참고치를 계산한다. 등급이나 제안은 계산하지 않는다."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _bounds(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value), float(value))
    if isinstance(value, dict):
        low = value.get("low", value.get("min"))
        high = value.get("high", value.get("max", low))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return (float(low), float(high))
    return None


def estimate_savings(
    annual_frequency: Any, duration_minutes: Any, human_intervention_rate: Any
) -> dict[str, Any]:
    frequency = _bounds(annual_frequency)
    duration = _bounds(duration_minutes)
    try:
        intervention = float(human_intervention_rate)
    except (TypeError, ValueError):
        intervention = -1
    if frequency is None or duration is None or not 0 <= intervention <= 1:
        return {"status": "unavailable", "label": "[측정 불가]"}
    if min(*frequency, *duration) < 0:
        raise ValueError("빈도와 소요 시간은 음수일 수 없습니다.")
    factor = 1 - intervention
    return {
        "status": "estimated",
        "unit": "시간/년",
        "low_hours": frequency[0] * duration[0] / 60 * factor,
        "high_hours": frequency[1] * duration[1] / 60 * factor,
        "human_intervention_rate": intervention,
        "label": "(추정)",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="사람 개입률을 반영한 연간 절감 시간 참고치를 계산합니다.")
    parser.add_argument("input", type=Path, help="annual_frequency, duration_minutes, human_intervention_rate JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = estimate_savings(
        data.get("annual_frequency"),
        data.get("duration_minutes"),
        data.get("human_intervention_rate"),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"절감 시간 참고치 계산 완료: {args.output}")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
