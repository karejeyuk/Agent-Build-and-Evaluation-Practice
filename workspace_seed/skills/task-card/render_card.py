#!/usr/bin/env python3
"""판정 코드가 붙은 카드 JSON을 고정 Markdown 템플릿으로 조립한다."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from classify import classify_document

SKILL_DIR = Path(__file__).resolve().parent
TEMPLATE = SKILL_DIR / "templates" / "task_card.md"
FIELD_LABELS = {
    "1": "업무명",
    "2": "분류(제안)",
    "3": "빈도·소요 시간",
    "4": "사용 시스템",
    "5": "절차 요약",
    "6": "예외 상황",
    "7": "실수 영향",
    "8": "판단 개입 지점",
    "9": "근거 원문",
    "10": "미확인 항목",
}


def _quote(evidence: dict[str, Any], interview_id: str) -> str:
    quote = str(evidence.get("quote", ""))
    if evidence.get("source_type") == "utterance":
        prefix = f"{interview_id} " if interview_id and evidence.get("interview_id") else ""
        return f"(발화 근거: {prefix}{evidence.get('q', evidence.get('source_id', ''))} '{quote}')"
    if evidence.get("source_type") == "memo":
        return f"(발화 근거: {evidence.get('m', evidence.get('source_id', ''))} '{quote}')"
    return f"(문서 근거: {evidence.get('filename', '')}, L{evidence.get('start_line')}~L{evidence.get('end_line')} '{quote}')"


def _field_evidence(items: Any, interview_id: str) -> str:
    if not isinstance(items, list):
        return ""
    return " ".join(_quote(item, interview_id) for item in items if isinstance(item, dict))


def _fill_unconfirmed_fields(card: dict[str, Any]) -> dict[str, Any]:
    result = dict(card)
    fields = result.get("fields", {})
    result["field_10_unconfirmed"] = [
        str(fields.get(str(number), fields.get(number, "")))
        for number in range(1, 10)
        if "[확인 필요" in str(fields.get(str(number), fields.get(number, "")))
    ]
    return result


def _card_markdown(card: dict[str, Any], source: str, interview_id: str, execution_version: str) -> str:
    result = _fill_unconfirmed_fields(card)
    fields = dict(result.get("fields", {}))
    fields["2"] = f"{result.get('classification_proposal', '[판정 보류: 사유]')} (판정 조건표 참조)"
    fields["8"] = result.get("judgment_intervention_point", "[확인 필요: 판단이 필요한 경우가 있는지]")
    fields["10"] = " ".join(result.get("field_10_unconfirmed", [])) or "없음"
    evidence = result.get("field_evidence", {})
    for field_number in ("3", "4", "5", "6", "7", "9"):
        citations = _field_evidence(evidence.get(field_number, []), interview_id)
        value = str(fields.get(field_number, "[확인 필요: 내용]"))
        if field_number in {"3", "4", "6", "7"} and not citations and "[확인 필요" not in value:
            fields[field_number] = "[확인 필요: 근거 없음]"
        elif field_number == "5" and "매뉴얼·양식:" in value and not citations:
            fields[field_number] = value.replace("매뉴얼·양식:", "매뉴얼·양식: [확인 필요: 근거 없음]")
        elif citations:
            fields[field_number] = f"{value} {citations}".strip()
    extraction = result.get("extraction_basis", {})
    head = [
        f"### 카드 번호: {result.get('card_id', '[확인 필요: 카드 번호]')}",
        f"추출 근거: 주기·사건 {extraction.get('trigger', '[확인 필요]')} / 동작 {extraction.get('action', '[확인 필요]')} / 대상 {extraction.get('object', '[확인 필요]')}",
        f"출처: {source}",
        f"인터뷰 식별자: {interview_id}",
        f"실행 버전: {execution_version}",
        "엔지니어 확정: (비어 있음)",
    ]
    body = [f"{number} {FIELD_LABELS[number]}: {fields.get(number, '[확인 필요: 내용]')}" for number in FIELD_LABELS]
    if result.get("next_round_recommended"):
        body.append("다음 회차 확인 권고")
    return "\n".join([*head, *body])


def _conditions_markdown(cards: list[dict[str, Any]]) -> str:
    rows = []
    for card in cards:
        values = []
        for number in range(1, 7):
            condition = card.get("conditions", {}).get(str(number), {})
            status = condition.get("status", "미충족(해당 발화 없음)") if isinstance(condition, dict) else condition
            evidence = _field_evidence(condition.get("evidence", []), "") if isinstance(condition, dict) else ""
            reason = condition.get("reason", "") if isinstance(condition, dict) else ""
            values.append(f"조건 {number}: {status} {reason} {evidence}".strip())
        rows.append(f"#### {card.get('card_id', '[카드 번호 확인 필요]')}\n" + " / ".join(values))
    return "\n\n".join(rows) if rows else "- 판정 조건표 없음"


def _candidates_markdown(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "- 없음"
    return "\n".join(
        f"- {candidate.get('name', candidate.get('content', '[확인 필요]'))} "
        f"{_field_evidence(candidate.get('evidence', []), '')}".strip()
        for candidate in candidates
    )


def _recommendations_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- 없음"
    return "\n".join(
        f"- 매번 달라지는 부분: {item.get('variable_part', '[확인 필요: 근거]')} / "
        f"표준화할 대상: {item.get('target', '[확인 필요: 대상]')} / "
        f"담당자에게 확인할 질문: {item.get('question', '[확인 필요: 질문]')}"
        for item in items
    )


def render(document: dict[str, Any], source: str, interview_id: str, execution_version: str) -> str:
    classified = classify_document(document)
    values = {
        "SOURCE": source,
        "INTERVIEW_ID": interview_id,
        "EXECUTION_VERSION": execution_version,
        "CARDS": "\n\n".join(
            _card_markdown(card, source, interview_id, execution_version)
            for card in classified["cards"]
        ) or "- 카드 없음",
        "CONDITIONS": _conditions_markdown(classified["cards"]),
        "DOCUMENT_CANDIDATES": _candidates_markdown(classified.get("document_candidates", [])),
        "STANDARDIZATION_RECOMMENDATIONS": _recommendations_markdown(classified.get("standardization_recommendations", [])),
        "C_TYPE_RECORDS": "\n".join(
            f"- {card.get('card_id', '')}: {card.get('fields', {}).get('1', '')}"
            for card in classified["c_type_records"]
        ) or "- 없음",
    }
    output = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", value)
    return output.rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="업무 카드 JSON을 Markdown으로 조립합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--interview-id", required=True)
    parser.add_argument("--execution-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = render(json.loads(args.input.read_text(encoding="utf-8")), args.source, args.interview_id, args.execution_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(f"카드 산출물 작성 완료: {args.output}")


if __name__ == "__main__":
    main()
