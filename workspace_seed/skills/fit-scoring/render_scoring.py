#!/usr/bin/env python3
"""점수 계산 JSON과 LLM 요약을 순위표 Markdown으로 조립한다."""

import argparse
import json
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).resolve().parent / "templates" / "scoring_table.md"


def _score(row: dict[str, Any], key: str) -> str:
    item = row.get("criteria", {}).get(key, {})
    if isinstance(item, dict):
        return str(item.get("score")) if item.get("score") is not None else "[확인 필요]"
    return str(item) if item is not None else "[확인 필요]"


def render(document: dict[str, Any]) -> str:
    rows = []
    standardization = []
    for index, item in enumerate(document.get("ranking", []), 1):
        savings = item.get("annual_savings")
        savings_text = "[확인 필요]"
        if savings:
            savings_text = f"{savings['low_hours']:.1f}~{savings['high_hours']:.1f}시간/년 (추정)"
        notes = ", ".join(item.get("notes", []))
        rows.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                index,
                item.get("card_id", ""),
                item.get("task_name", ""),
                item.get("classification", ""),
                _score(item, "repeatability"),
                _score(item, "procedure_clarity"),
                _score(item, "data_accessibility"),
                _score(item, "error_impact"),
                item.get("total_score") if item.get("total_score") is not None else "[평가 불가]",
                item.get("grade_proposal", "[평가 불가]"),
                savings_text,
                notes,
            )
        )
        if item.get("grade_proposal") == "표준화 선행":
            standardization.append(f"- {item.get('card_id')}: {item.get('standardization_recommendation', '[확인 필요: 권고 작성]')}")
    summaries = "\n\n".join(
        f"- {summary.get('text', '')}" for summary in document.get("summaries", [])
    ) or "- 추천 근거 요약 없음"
    values = {
        "SOURCE": document.get("source", "[확인 필요: 입력 파일]"),
        "INTERVIEW_ID": document.get("interview_id", "[확인 필요: 인터뷰 식별자]"),
        "EXECUTION_VERSION": document.get("execution_version", "[확인 필요: 실행 버전]"),
        "RANKING_ROWS": "\n".join(rows) if rows else "| - | - | 채점 대상 없음 | - | - | - | - | - | - | - | - | - |",
        "SUMMARIES": summaries,
        "STANDARDIZATION": "\n".join(standardization) or "- 없음",
    }
    output = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", str(value))
    return output.rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="적합성 점수 JSON을 Markdown 순위표로 조립합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")
    print(f"순위표 작성 완료: {args.output}")


if __name__ == "__main__":
    main()
