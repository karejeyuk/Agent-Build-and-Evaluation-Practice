#!/usr/bin/env python3
"""검증 가능한 리포트 JSON을 성과 보고 템플릿으로 조립한다."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "report.md"


def _metric(value: Any, label: str) -> str:
    if value is None or value == "":
        return "[측정 불가]"
    if isinstance(value, dict):
        low, high = value.get("low"), value.get("high")
        if low is not None and high is not None and low != high:
            return f"{low}~{high} {label}"
        value = low if low is not None else high if high is not None else value.get("value")
        if value is None:
            return "[측정 불가]"
    return f"{value} {label}".strip()


def _evidence_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return " ".join(
        str(item.get("quote", ""))
        for item in items
        if isinstance(item, dict) and item.get("quote")
    )


def render_report(document: dict[str, Any]) -> dict[str, Any]:
    metrics_rows = []
    for metric in document.get("metrics", []):
        target = _metric(metric.get("target"), "(목표)")
        actual = _metric(metric.get("actual"), "(실측)")
        name = metric.get("name", "[확인 필요: 지표]")
        source = metric.get("source", "[확인 필요: 실측 출처]")
        metrics_rows.append(f"| {name} | {target} | {actual} | {source} |")

    misses = []
    for item in document.get("misses", []):
        category = item.get("cause")
        evidence = _evidence_text(item.get("evidence"))
        if category not in {"프롬프트 문제", "데이터 문제", "절차 표준화 부족(B2형으로 되돌림)", "측정 방식 문제"}:
            category = "[확인 필요: 미달 원인]"
        misses.append(f"- {item.get('metric', '[확인 필요: 지표]')}: {category} / 근거: {evidence or '[확인 필요: 근거]'}")

    grouped: dict[str, list[str]] = {"긍정": [], "개선": [], "추가 기대": []}
    invalid_quotes = []
    source_text = str(document.get("feedback_source", ""))
    for item in document.get("feedback", []):
        category = item.get("category")
        quote = str(item.get("quote", ""))
        if category not in grouped:
            invalid_quotes.append(f"알 수 없는 피드백 분류: {category}")
            continue
        if not quote or not source_text or quote not in source_text:
            invalid_quotes.append("피드백 인용이 가명 처리된 원문과 일치하지 않습니다.")
        if quote:
            grouped[category].append(f"- ‘{quote}’ ({item.get('source', '[확인 필요: 피드백 출처]')})")

    recommendations = [
        f"- 우선순위: {item.get('priority', '[확인 필요]')} / {item.get('action', '[확인 필요: 개선안]')} / 근거: {item.get('basis', '[확인 필요: 근거]')}"
        for item in document.get("recommendations", [])
    ]
    savings = document.get("savings", {})
    if savings.get("status") == "estimated":
        savings_text = f"연간 {savings.get('low_hours', 0):.1f}~{savings.get('high_hours', 0):.1f}시간 (추정)"
    else:
        savings_text = "[측정 불가]"
    summary = str(document.get("executive_summary", "[확인 필요: 임원용 요약]"))

    values = {
        "SOURCE": document.get("source", "[확인 필요: 입력 파일]"),
        "INTERVIEW_ID": document.get("interview_id", "[확인 필요: 인터뷰 식별자]"),
        "EXECUTION_VERSION": document.get("execution_version", "[확인 필요: 실행 버전]"),
        "METRICS": "\n".join(metrics_rows) or "| [확인 필요: 지표] | [확인 필요: 목표] | [측정 불가] | [확인 필요: 출처] |",
        "MISSES": "\n".join(misses) or "- 미달 항목 없음",
        "POSITIVE_FEEDBACK": "\n".join(grouped["긍정"]) or "- 없음",
        "IMPROVEMENT_FEEDBACK": "\n".join(grouped["개선"]) or "- 없음",
        "EXPECTATION_FEEDBACK": "\n".join(grouped["추가 기대"]) or "- 없음",
        "RECOMMENDATIONS": "\n".join(recommendations) or "- [확인 필요: 개선 권고안]",
        "SAVINGS": savings_text,
        "EXECUTIVE_SUMMARY": summary,
    }
    output = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", str(value))
    if re.search(r"\{\{[A-Z_]+\}\}", output):
        raise ValueError("성과 리포트 템플릿에 치환되지 않은 항목이 있습니다.")
    return {"markdown": output.rstrip() + "\n", "quote_errors": invalid_quotes}


def main() -> None:
    parser = argparse.ArgumentParser(description="성과 리포트 JSON을 Markdown으로 조립합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render_report(json.loads(args.input.read_text(encoding="utf-8")))
    if result["quote_errors"]:
        raise SystemExit("\n".join(result["quote_errors"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result["markdown"], encoding="utf-8")
    print(f"성과 리포트 초안 작성 완료: {args.output}")


if __name__ == "__main__":
    main()
