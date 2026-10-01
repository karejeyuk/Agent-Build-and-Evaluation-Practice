"""절감 시간 추정치를 계산한다: 연간 횟수 × 1회 소요 시간 × (1 - 사람 개입률), 범위면 하한과 상한(기획서 3.8의 5)).

등급이나 제안은 계산하지 않는다. 입력이 없으면 수치를 만들지 않고 [측정 불가]로 둔다.
"""

from __future__ import annotations

import re
from typing import Any


def _bounds(value: Any) -> tuple[float, float] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return (float(value), float(value))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = {"low": value[0], "high": value[1]}
    if isinstance(value, dict):
        low = value.get("low", value.get("min"))
        high = value.get("high", value.get("max", low))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and not isinstance(low, bool):
            return (float(min(low, high)), float(max(low, high)))
    return None


def intervention_rate(value: Any) -> tuple[float | None, str]:
    """사람 개입률을 0~1로 읽는다. '25%'나 25처럼 백분율로 준 값은 바꾸고 그 사실을 알린다."""
    note = ""
    if isinstance(value, str):
        text = value.strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
        if match:
            return float(match.group(1)) / 100, ""
        try:
            value = float(text)
        except ValueError:
            return None, "사람 개입률을 숫자로 읽을 수 없다."
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "사람 개입률이 없다."
    rate = float(value)
    if 1 < rate <= 100:
        rate, note = rate / 100, f"사람 개입률 {value}를 백분율로 보고 {rate:g}로 계산했다."
    if not 0 <= rate <= 1:
        return None, "사람 개입률은 0~1(또는 0~100%) 사이여야 한다."
    return rate, note


def estimate_savings(annual_frequency: Any, duration_minutes: Any, human_intervention_rate: Any) -> dict[str, Any]:
    frequency = _bounds(annual_frequency)
    duration = _bounds(duration_minutes)
    rate, note = intervention_rate(human_intervention_rate)
    if frequency is None or duration is None or rate is None:
        missing = [name for name, value in (("연간 횟수", frequency), ("1회 소요 시간", duration), ("사람 개입률", rate)) if value is None]
        return {"status": "unavailable", "label": "[측정 불가]", "missing": missing, "note": note}
    if min(*frequency, *duration) < 0:
        raise ValueError("빈도와 소요 시간은 음수일 수 없다.")
    factor = 1 - rate
    return {
        "status": "estimated",
        "unit": "시간/년",
        "low_hours": frequency[0] * duration[0] / 60 * factor,
        "high_hours": frequency[1] * duration[1] / 60 * factor,
        "human_intervention_rate": rate,
        "label": "(추정)",
        "note": note,
    }
