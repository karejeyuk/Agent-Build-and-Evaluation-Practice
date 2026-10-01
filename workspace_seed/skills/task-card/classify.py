#!/usr/bin/env python3
"""판정 조건표만으로 업무 카드의 형을 결정한다."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SATISFIED = {"충족", "satisfied", "met", True}
UNSATISFIED = {"미충족", "unsatisfied", "not_met", False}


def _condition(conditions: dict[str, Any], number: int) -> dict[str, Any]:
    value = conditions.get(str(number), conditions.get(f"condition_{number}"))
    if isinstance(value, str):
        return {"status": value}
    if isinstance(value, bool):
        return {"status": value}
    return value if isinstance(value, dict) else {"status": None}


def _status(condition: dict[str, Any]) -> str | None:
    value = condition.get("status", condition.get("result"))
    if value in SATISFIED:
        return "satisfied"
    if value in UNSATISFIED:
        return "unsatisfied"
    return None


def _has_failed_satisfied_evidence(conditions: dict[str, Any]) -> bool:
    for number in range(1, 7):
        condition = _condition(conditions, number)
        if _status(condition) != "satisfied":
            continue
        if condition.get("citation_valid") is False or condition.get("evidence_valid") is False:
            return True
        if condition.get("citation_status") == "failed":
            return True
        if condition.get("quote") and condition.get("validated") is False:
            return True
    return False


def classify_conditions(conditions: dict[str, Any]) -> dict[str, Any]:
    """C형 우선, 나머지는 정확히 한 유형만 일치할 때 제안한다."""
    if not isinstance(conditions, dict):
        raise TypeError("conditions는 조건 번호를 키로 하는 객체여야 합니다.")

    statuses = {number: _status(_condition(conditions, number)) for number in range(1, 7)}
    if _has_failed_satisfied_evidence(conditions):
        proposal = "판정 보류"
        reason = "[판정 보류: 인용 대조 실패]"
    elif statuses[1] == "satisfied":
        proposal = "C형"
        reason = "조건 1 충족"
    else:
        matches: list[str] = []
        if statuses[2] == "satisfied":
            matches.append("B2형")
        if all(statuses[number] == "satisfied" for number in (3, 4, 5)):
            matches.append("B1형")
        if (
            statuses[3] == "satisfied"
            and statuses[6] == "satisfied"
            and statuses[4] == "unsatisfied"
        ):
            matches.append("A형")

        if len(matches) == 1:
            proposal = matches[0]
            reason = f"판정 조건 일치: {proposal}"
        elif matches:
            proposal = "판정 보류"
            reason = f"[판정 보류: 복수 조건 일치 ({', '.join(matches)})]"
        else:
            proposal = "판정 보류"
            missing = [
                str(number)
                for number in range(2, 7)
                if statuses[number] is None
                or (number in (3, 5, 6) and statuses[number] != "satisfied")
            ]
            reason = "[판정 보류: 조건 불충분" + (f" ({', '.join(missing)})" if missing else "") + "]"

    condition_four = _condition(conditions, 4)
    if statuses[4] == "satisfied":
        judgment_point = str(condition_four.get("judgment_point", "")).strip()
        decision_point = judgment_point or "[확인 필요: 판단 개입 지점 설명]"
    elif (
        condition_four.get("reason") == "판단 없음 확인"
        and condition_four.get("citation_valid") is True
    ):
        decision_point = "없음"
    else:
        decision_point = "[확인 필요: 판단이 필요한 경우가 있는지]"

    return {
        "classification_proposal": proposal,
        "classification_reason": reason,
        "condition_statuses": {str(number): statuses[number] for number in range(1, 7)},
        "judgment_intervention_point": decision_point,
        "standardization_recommendation_required": (
            proposal == "B2형" and statuses[2] == "satisfied"
        ),
        "c_type_record_required": proposal == "C형",
    }


def classify_document(document: dict[str, Any]) -> dict[str, Any]:
    """cards 목록 안의 각 카드에 계산된 판정을 붙인다."""
    if not isinstance(document, dict) or not isinstance(document.get("cards"), list):
        raise ValueError("입력 JSON에는 cards 목록이 필요합니다.")
    result = dict(document)
    cards = []
    for index, card in enumerate(document["cards"], 1):
        if not isinstance(card, dict) or not isinstance(card.get("conditions"), dict):
            raise ValueError(f"cards[{index - 1}]에 conditions 객체가 필요합니다.")
        cards.append({**card, **classify_conditions(card["conditions"])})
    result["cards"] = cards
    result["c_type_records"] = [
        card
        for card in cards
        if card["c_type_record_required"]
    ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="판정 조건표로 업무 카드의 형을 계산합니다.")
    parser.add_argument("input", type=Path, help="cards와 conditions가 든 JSON 파일")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = classify_document(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"카드 판정 완료: {args.output}")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()