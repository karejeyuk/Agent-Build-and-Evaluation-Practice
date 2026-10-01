#!/usr/bin/env python3
"""업무 적합성 점수, 등급, 연간 절감 시간을 결정론적으로 계산한다."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CRITERIA = ("procedure_clarity", "data_accessibility", "error_impact")
GRADE_ORDER = {"즉시 착수": 0, "검토 후 착수": 1, "착수 보류": 2, "평가 불가": 3}
CONFIRMED_TYPES = {"A형", "B1형", "B2형", "C형"}
FREQUENCY_PATTERNS = (
    (re.compile(r"매일"), 260.0),
    (re.compile(r"격주"), 26.0),
    (re.compile(r"주\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*회"), 52.0),
    (re.compile(r"월\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*회"), 12.0),
    (re.compile(r"분기\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*회"), 4.0),
    (re.compile(r"반기\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*회"), 2.0),
    (re.compile(r"연\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*회"), 1.0),
    (re.compile(r"사건\s*기반\s*월평균\s*(\d+(?:\.\d+)?)(?:\s*[~〜-]\s*(\d+(?:\.\d+)?))?\s*건"), 12.0),
)


def _range(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return (number, number) if number >= 0 else None
    if not isinstance(value, str):
        return None
    numbers = re.findall(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if not numbers:
        return None
    low = float(numbers[0])
    high = float(numbers[1]) if len(numbers) > 1 and re.search(r"[~〜-]", value) else low
    return (min(low, high), max(low, high))


def annual_frequency(value: Any) -> dict[str, float] | None:
    """기획서 빈도 문법을 연간 실행 횟수로 바꾼다. 범위는 양 끝을 보존한다."""
    if not isinstance(value, str):
        return None
    for pattern, multiplier in FREQUENCY_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        if pattern.pattern == r"매일":
            low = high = multiplier
        elif pattern.pattern == r"격주":
            low = high = multiplier
        else:
            first = float(match.group(1))
            second = float(match.group(2)) if match.group(2) else first
            low, high = sorted((first * multiplier, second * multiplier))
        return {"low": low, "high": high}
    return None


def _duration_minutes(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        low = _range(value.get("min"))
        high = _range(value.get("max"))
        if low and high:
            return (low[0], high[0])
        return None
    if isinstance(value, str) and re.search(r"시간|hour", value, re.IGNORECASE):
        duration = _range(value)
        return (duration[0] * 60, duration[1] * 60) if duration else None
    return _range(value)


def _criterion(choice: Any) -> int | None:
    if isinstance(choice, dict):
        choice = choice.get("score", choice.get("level"))
    if isinstance(choice, bool):
        return None
    try:
        score = int(choice)
    except (TypeError, ValueError):
        return None
    return score if 1 <= score <= 5 else None


def _round_number(card: dict[str, Any]) -> int:
    value = card.get("round", card.get("round_number", 0))
    if isinstance(value, int):
        return value
    match = re.search(r"(\d+)회차", str(card.get("interview_id", value)))
    return int(match.group(1)) if match else 0


def select_latest_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 work_key로 연결된 카드는 회차가 가장 최신인 카드만 남긴다."""
    latest: dict[str, dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []
    for card in cards:
        key = str(card.get("work_key", "")).strip()
        if not key:
            standalone.append(card)
            continue
        current = latest.get(key)
        if current is None or _round_number(card) > _round_number(current):
            latest[key] = card
    return standalone + list(latest.values())


def _annual_savings(card: dict[str, Any], frequency: dict[str, float] | None) -> dict[str, float] | None:
    duration = _duration_minutes(card.get("duration_minutes", card.get("duration")))
    if frequency is None or duration is None:
        return None
    return {
        "low_hours": frequency["low"] * duration[0] / 60,
        "high_hours": frequency["high"] * duration[1] / 60,
    }


def _grade(scores: dict[str, int], error_impact: int | None) -> tuple[int | None, str]:
    if len(scores) != 4:
        return None, "평가 불가"
    total = sum(scores.values())
    if total >= 16 and min(scores.values()) >= 3 and error_impact not in {1, 2}:
        return total, "즉시 착수"
    if total >= 11:
        return total, "검토 후 착수"
    return total, "착수 보류"


def score_card(card: dict[str, Any]) -> dict[str, Any]:
    confirmed_type = card.get("engineer_confirmation", card.get("confirmation", ""))
    task_type = card.get("classification", card.get("classification_proposal", ""))
    result = {
        "card_id": card.get("card_id", card.get("id", "")),
        "task_name": card.get("task_name", card.get("name", "")),
        "classification": task_type,
        "engineer_confirmation": confirmed_type,
        "rank": None,
    }

    if task_type == "B2형" and confirmed_type == "B2형":
        result["standardization_recommendation"] = card.get(
            "standardization_recommendation", "[확인 필요: 권고 작성]"
        )
        result["grade_proposal"] = "표준화 선행"
        return result

    if confirmed_type not in {"A형", "B1형"}:
        result["status"] = "채점 제외: 엔지니어가 A형 또는 B1형으로 확정하지 않음"
        return result

    frequency = annual_frequency(card.get("frequency", card.get("frequency_text", "")))
    selections = card.get("criteria", {})
    scores: dict[str, int] = {}
    breakdown: dict[str, Any] = {}
    if frequency is not None:
        annual_count = frequency["low"]
        repeatability = 5 if annual_count >= 240 else 4 if annual_count >= 48 else 3 if annual_count >= 12 else 2 if annual_count >= 2 else 1
        scores["repeatability"] = repeatability
        breakdown["repeatability"] = {
            "score": repeatability,
            "annual_frequency": frequency,
            "evidence": card.get("frequency_evidence", []),
        }
    else:
        breakdown["repeatability"] = {"score": None, "status": "[확인 필요: 빈도]"}

    for key in CRITERIA:
        score = _criterion(selections.get(key))
        evidence = selections.get(key, {}).get("evidence", []) if isinstance(selections.get(key), dict) else []
        if score is not None and evidence:
            scores[key] = score
            breakdown[key] = {"score": score, "evidence": evidence}
        else:
            breakdown[key] = {"score": None, "status": "[확인 필요: 근거 또는 평가 기준]"}

    error_impact = scores.get("error_impact")
    total, grade = _grade(scores, error_impact)
    result.update(
        {
            "status": "scored" if total is not None else "insufficient_information",
            "criteria": breakdown,
            "total_score": total,
            "grade_proposal": grade,
            "annual_frequency": frequency,
            "annual_savings": _annual_savings(card, frequency),
            "notes": [],
        }
    )
    if error_impact in {1, 2}:
        result["notes"].append("사람 승인 필수: 오류 영향도 1~2점")
    if breakdown.get("data_accessibility", {}).get("score") in {1, 2}:
        result["notes"].append("전산화 선행 필요")
    return result


def score_cards(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("cards"), list):
        raise ValueError("입력 JSON에는 cards 목록이 필요합니다.")
    selected = select_latest_cards(document["cards"])
    rows = [score_card(card) for card in selected]
    for row in rows:
        row["rank"] = GRADE_ORDER.get(row.get("grade_proposal"), 3)
    rows.sort(
        key=lambda row: (
            row["rank"],
            -(row.get("total_score") or 0),
            -(row.get("annual_savings") or {}).get("low_hours", 0),
            str(row.get("card_id", "")),
        )
    )
    tied: dict[tuple[str, int | None], int] = {}
    for row in rows:
        if row.get("total_score") is not None:
            key = (row["grade_proposal"], row["total_score"])
            tied[key] = tied.get(key, 0) + 1
    for row in rows:
        if row.get("total_score") is not None and tied[(row["grade_proposal"], row["total_score"])] > 1:
            row.setdefault("notes", []).append("동점, 엔지니어 확정 필요")
    result = dict(document)
    result["ranking"] = rows
    result["summary_targets"] = [
        row for row in rows if row.get("grade_proposal") in {"즉시 착수", "검토 후 착수"}
    ][:3]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="확정 카드의 적합성 점수와 등급을 계산합니다.")
    parser.add_argument("input", type=Path, help="카드 JSON 파일")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = score_cards(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"적합성 평가 완료: {args.output}")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()