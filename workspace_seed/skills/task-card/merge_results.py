#!/usr/bin/env python3
"""업무 카드 추출 결과와 별도 조건표 결과를 카드 번호로 병합한다."""

import argparse
import json
from pathlib import Path
from typing import Any


def merge_results(extracted: dict[str, Any], condition_result: dict[str, Any]) -> dict[str, Any]:
    cards = extracted.get("cards")
    condition_cards = condition_result.get("cards")
    if not isinstance(cards, list) or not isinstance(condition_cards, list):
        raise ValueError("두 입력 모두 cards 목록이 필요합니다.")
    by_id: dict[str, dict[str, Any]] = {}
    for condition_card in condition_cards:
        card_id = str(condition_card.get("card_id", "")).strip()
        if not card_id or card_id in by_id:
            raise ValueError("조건표의 card_id는 비어 있지 않고 중복되면 안 됩니다.")
        by_id[card_id] = condition_card

    merged = []
    recommendations = list(extracted.get("standardization_recommendations", []))
    for card in cards:
        card_id = str(card.get("card_id", "")).strip()
        if not card_id or card_id not in by_id:
            raise ValueError(f"추출 카드 {card_id or '[카드 번호 없음]'}에 대응하는 조건표가 없습니다.")
        condition_card = by_id.pop(card_id)
        conditions = condition_card.get("conditions")
        if not isinstance(conditions, dict):
            raise ValueError(f"{card_id}: conditions 객체가 없습니다.")
        recommendation = condition_card.get("standardization_recommendation")
        if recommendation:
            recommendations.append({"card_id": card_id, **recommendation})
        merged.append(
            {
                **card,
                "conditions": conditions,
                "judgment_point": condition_card.get("judgment_point", ""),
                "standardization_recommendation": recommendation,
            }
        )
    if by_id:
        raise ValueError(f"추출 결과에 없는 조건표 카드가 있습니다: {', '.join(by_id)}")
    return {**extracted, "cards": merged, "standardization_recommendations": recommendations}


def main() -> None:
    parser = argparse.ArgumentParser(description="카드 추출과 조건표 JSON을 병합합니다.")
    parser.add_argument("--extract", type=Path, required=True)
    parser.add_argument("--conditions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extracted = json.loads(args.extract.read_text(encoding="utf-8"))
    conditions = json.loads(args.conditions.read_text(encoding="utf-8"))
    result = merge_results(extracted, conditions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"카드와 조건표 병합 완료: {args.output}")


if __name__ == "__main__":
    main()
