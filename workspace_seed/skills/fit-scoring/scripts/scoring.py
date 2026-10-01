"""반복성 계산, 점수 환산, 총점과 등급, 상한 규칙, 연간 절감 시간, 정렬을 정한다(기획서 3.6).

같은 입력에는 늘 같은 결과가 나온다. 확인되지 않은 값([확인 필요])에서는 수치를 만들지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

CRITERIA = ("repeatability", "procedure_clarity", "data_accessibility", "error_impact")
CRITERIA_LABELS = {
    "repeatability": "반복성",
    "procedure_clarity": "절차 명확성",
    "data_accessibility": "데이터 접근성",
    "error_impact": "오류 영향도",
}
GRADE_ORDER = {"즉시 착수": 0, "검토 후 착수": 1, "착수 보류": 2, "평가 불가": 3}
_RANGE = r"(\d+(?:\.\d+)?)(?:\s*[~〜∼\-–—]\s*(\d+(?:\.\d+)?))?"
FREQUENCY_RULES = (
    (re.compile(r"^매일$"), None, 260.0),
    (re.compile(r"^격주$"), None, 26.0),
    (re.compile(rf"^주\s*{_RANGE}\s*회$"), 1, 52.0),
    (re.compile(rf"^월\s*{_RANGE}\s*회$"), 1, 12.0),
    (re.compile(rf"^분기\s*{_RANGE}\s*회$"), 1, 4.0),
    (re.compile(rf"^반기\s*{_RANGE}\s*회$"), 1, 2.0),
    (re.compile(rf"^연\s*{_RANGE}\s*회$"), 1, 1.0),
    (re.compile(rf"^사건\s*기반\s*월평균\s*{_RANGE}\s*건$"), 1, 12.0),
)
TAG = re.compile(r"\[[^\[\]]*\]")


def annual_frequency(value: Any) -> dict[str, float] | None:
    """빈도 필드 값을 연간 횟수(하한, 상한)로 바꾼다. 실행 계기 표시는 무시하고, 횟수를 모르면 None이다."""
    text = TAG.sub("", str(value or "")).strip()
    if not text or "확인 필요" in str(value).replace("[확인 필요: 실행 계기]", ""):
        return None
    for pattern, group, multiplier in FREQUENCY_RULES:
        match = pattern.match(text)
        if not match:
            continue
        if group is None:
            return {"low": multiplier, "high": multiplier}
        first = float(match.group(1))
        second = float(match.group(2)) if match.group(2) else first
        low, high = sorted((first * multiplier, second * multiplier))
        return {"low": low, "high": high}
    return None


def duration_minutes(value: Any) -> tuple[float, float] | None:
    """1회 소요 시간 값을 분 단위(하한, 상한)로 바꾼다. '30분', '20~30분', '1시간', '30분~1시간', '1시간 30분'을 읽는다."""
    text = str(value or "").strip()
    if not text or "[확인 필요" in text or "[측정 불가" in text:
        return None
    text = text.split(":", 1)[-1].strip()
    parts = re.split(r"\s*[~〜∼–—]\s*|(?<=[\d분간])\s*-\s*(?=\d)", text)
    if len(parts) > 2:
        return None

    def minutes(part: str, default_unit: str | None) -> float | None:
        found = re.findall(r"(\d+(?:\.\d+)?)\s*(시간|분)?", part)
        if not found:
            return None
        total = 0.0
        for number, unit in found:
            unit = unit or default_unit
            if unit is None:
                return None
            total += float(number) * (60 if unit == "시간" else 1)
        return total

    last_unit_match = re.findall(r"(시간|분)", parts[-1])
    trailing_unit = last_unit_match[-1] if last_unit_match else None
    low = minutes(parts[0], trailing_unit if len(parts) == 2 else None)
    high = minutes(parts[-1], None) if len(parts) == 2 else low
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def repeatability_score(annual_low: float) -> int:
    if annual_low >= 240:
        return 5
    if annual_low >= 48:
        return 4
    if annual_low >= 12:
        return 3
    if annual_low >= 2:
        return 2
    return 1


def grade(scores: dict[str, int | None]) -> tuple[int | None, str]:
    """평가 불가, 즉시 착수(16점 이상·모두 3점 이상·오류 영향도 3점 이상), 검토 후 착수(11점 이상), 착수 보류."""
    if any(scores.get(key) is None for key in CRITERIA):
        return None, "평가 불가"
    values = [int(scores[key]) for key in CRITERIA]
    total = sum(values)
    if total >= 16 and min(values) >= 3 and scores["error_impact"] not in (1, 2):
        return total, "즉시 착수"
    if total >= 11:
        return total, "검토 후 착수"
    return total, "착수 보류"


def annual_savings(frequency: dict[str, float] | None, duration: tuple[float, float] | None) -> dict[str, float] | None:
    if frequency is None or duration is None:
        return None
    return {"low_hours": frequency["low"] * duration[0] / 60, "high_hours": frequency["high"] * duration[1] / 60}


def round_number(interview_id: str) -> int:
    match = re.search(r"(\d+)회차", str(interview_id or ""))
    return int(match.group(1)) if match else 0


def select_latest(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """업무마다 최신 회차 카드를 고른다. 같은 업무는 카드 번호가 같거나 '직전 카드'로 이어진 카드다."""
    groups: dict[str, dict[str, Any]] = {}
    alias: dict[str, str] = {}
    for card in cards:
        key = str(card.get("previous_card") or "") or card["card_id"]
        key = alias.get(key, key)
        alias[card["card_id"]] = key
        current = groups.get(key)
        if current is None or round_number(card.get("interview_id")) > round_number(current.get("interview_id")):
            groups[key] = card
    return list(groups.values())


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """등급 순, 같은 등급은 총점 높은 순, 동점이면 절감 시간 아는 업무를 큰 순으로, 그다음 카드 번호 순."""
    def key(row: dict[str, Any]) -> tuple:
        rank = GRADE_ORDER[row["grade"]]
        if row["grade"] == "평가 불가":
            return (rank, 0, 1, 0.0, row["card_id"])
        savings = row.get("annual_savings")
        known = savings is not None
        return (rank, -row["total"], 0 if known else 1, -(savings["low_hours"] if known else 0.0), row["card_id"])

    ordered = sorted(rows, key=key)
    counts: dict[tuple[str, int], int] = {}
    for row in ordered:
        if row["total"] is not None:
            counts[(row["grade"], row["total"])] = counts.get((row["grade"], row["total"]), 0) + 1
    for row in ordered:
        if row["total"] is not None and counts[(row["grade"], row["total"])] > 1:
            row["notes"].append("동점, 엔지니어 확정 필요")
    return ordered


def score_row(card: dict[str, Any], selections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """카드 하나의 점수, 등급, 상한 확인, 참고 정보를 계산한다."""
    frequency = annual_frequency(card.get("frequency_value"))
    duration = duration_minutes(card.get("duration_value"))
    scores: dict[str, int | None] = {
        "repeatability": repeatability_score(frequency["low"]) if frequency else None,
        **{key: selections.get(key, {}).get("level") for key in CRITERIA[1:]},
    }
    total, proposed = grade(scores)
    notes: list[str] = []
    if scores["error_impact"] in (1, 2):
        notes.append("오류 영향도 1~2점: 즉시 착수 불가, 설계 때 사람 승인 지점 필수")
    if scores["data_accessibility"] in (1, 2):
        notes.append("전산화 선행 필요")
    if proposed == "즉시 착수" and scores["error_impact"] in (1, 2):  # 상한 규칙은 따로 다시 확인한다.
        raise AssertionError("상한 규칙 위반: 오류 영향도 1~2점 업무가 즉시 착수로 계산됐다.")
    return {
        "card_id": card["card_id"],
        "scores": scores,
        "total": total,
        "grade": proposed,
        "annual_frequency": frequency,
        "duration_minutes": duration,
        "annual_savings": annual_savings(frequency, duration),
        "missing": [CRITERIA_LABELS[key] for key in CRITERIA if scores[key] is None],
        "notes": notes,
    }
