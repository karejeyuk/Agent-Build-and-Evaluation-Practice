#!/usr/bin/env python3
"""설계서 JSON을 고정 9목차 템플릿으로 조립하고 품질 게이트를 적용한다."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
TEMPLATE = SKILL_DIR / "templates" / "design_doc.md"
FIELDS = {
    "1": ("agent_name", "purpose", "user", "run_time"),
    "2": ("must_do", "must_not_do"),
    "3": ("workflow", "human_approval"),
    "4": ("tools", "integration"),
    "5": ("common_rules", "additional_rules"),
    "6": ("input_format", "output_format", "example"),
    "7": ("success_definition", "measurement"),
    "8": ("risks", "responses"),
}
APPROVAL_PHRASE = "사람 승인 후 실행"


def _text(value: Any) -> str:
    if value is None or value == "":
        return "[확인 필요: 내용]"
    if isinstance(value, list):
        if not value:
            return "- 없음"
        return "\n".join(f"- {item}" if not isinstance(item, dict) else "- " + "; ".join(f"{key}: {value}" for key, value in item.items()) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"- {key}: {_text(item)}" for key, item in value.items())
    return str(value).strip()


def _only_unknown(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_only_unknown(item) for item in value)
    if isinstance(value, dict):
        return not value or all(_only_unknown(item) for item in value.values())
    text = str(value or "").strip()
    return not text or text.startswith(("[확인 필요", "[정보 부족"))


def _workflow_text(workflow: Any) -> str:
    if isinstance(workflow, list):
        return "\n".join(
            f"- 누가: {_text(step.get('who'))} / 무엇을: {_text(step.get('what'))} / 도구: {_text(step.get('tool'))}"
            for step in workflow
            if isinstance(step, dict)
        )
    return _text(workflow)


def build_design_document(document: dict[str, Any]) -> dict[str, Any]:
    sections = document.get("sections")
    card = document.get("card", {})
    if not isinstance(sections, dict):
        raise ValueError("sections 객체가 필요합니다.")
    missing_keys = [f"{section}.{field}" for section, keys in FIELDS.items() for field in keys if field not in sections.get(section, {})]
    if missing_keys:
        raise ValueError(f"설계서 필드가 누락됐습니다: {', '.join(missing_keys)}")

    workflow = sections["3"].get("workflow", [])
    workflow_text = _workflow_text(workflow)
    error_score = card.get("error_impact_score")
    try:
        error_score = int(error_score) if error_score is not None else None
    except (TypeError, ValueError):
        error_score = None
    mandatory_approval = card.get("classification") == "B1형" or error_score in {1, 2}
    approval = str(sections["3"].get("human_approval", "")).strip()
    if mandatory_approval and APPROVAL_PHRASE not in workflow_text and APPROVAL_PHRASE not in approval:
        workflow_text = (workflow_text + "\n" if workflow_text else "") + f"- {APPROVAL_PHRASE}"
        sections["3"]["human_approval"] = APPROVAL_PHRASE

    missing = 0
    for section, fields in FIELDS.items():
        for field in fields:
            if section == "5" and field == "common_rules":
                continue
            if section == "3" and field == "human_approval" and mandatory_approval:
                continue
            missing += _only_unknown(sections[section].get(field))
    gate = "" if missing < 6 else "품질 게이트: 2단계 인터뷰 보강 권고"

    values = {
        "SOURCE": _text(document.get("source")),
        "INTERVIEW_ID": _text(document.get("interview_id")),
        "EXECUTION_VERSION": _text(document.get("execution_version")),
        "AGENT_NAME": _text(sections["1"].get("agent_name")),
        "PURPOSE": _text(sections["1"].get("purpose")),
        "USER": _text(sections["1"].get("user")),
        "RUN_TIME": _text(sections["1"].get("run_time")),
        "MUST_DO": _text(sections["2"].get("must_do")),
        "MUST_NOT_DO": _text(sections["2"].get("must_not_do")),
        "WORKFLOW": workflow_text,
        "TOOLS": _text(sections["4"].get("tools")),
        "INTEGRATION": _text(sections["4"].get("integration")),
        "ADDITIONAL_RULES": _text(sections["5"].get("additional_rules")),
        "INPUT_FORMAT": _text(sections["6"].get("input_format")),
        "OUTPUT_FORMAT": _text(sections["6"].get("output_format")),
        "EXAMPLE": _text(sections["6"].get("example")),
        "SUCCESS_DEFINITION": _text(sections["7"].get("success_definition")),
        "MEASUREMENT": _text(sections["7"].get("measurement")),
        "RISKS": _text(sections["8"].get("risks")) + "\n" + _text(sections["8"].get("responses")),
        "UNKNOWNS": _text(sections.get("9", {}).get("unknowns", [])),
        "SPECIAL_NOTES": _text(sections.get("9", {}).get("special_notes", [])),
        "BUSINESS_SUMMARY": _text(document.get("business_summary")),
        "QUALITY_GATE": gate,
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    if re.search(r"\{\{[A-Z_]+\}\}", template):
        raise ValueError("설계서 템플릿에 치환되지 않은 항목이 있습니다.")
    return {"markdown": template.rstrip() + "\n", "unknown_field_count": missing, "quality_gate": gate, "mandatory_approval": mandatory_approval}


def main() -> None:
    parser = argparse.ArgumentParser(description="설계서 초안 Markdown을 조립합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_design_document(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result["markdown"], encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "markdown"}, ensure_ascii=False))
    print(f"설계서 초안 작성 완료: {args.output}")


if __name__ == "__main__":
    main()
