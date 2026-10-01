"""skill 파이프라인이 함께 쓰는 도구: 근거 검사, 표기, 템플릿 조립, 입력 게이트."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import validate as V


# ---------------------------------------------------------------------------
# 근거 검사
# ---------------------------------------------------------------------------
def check_evidence_list(
    run,
    items: Any,
    path: str,
    failures: list[dict[str, str]],
    notes: list[str],
    transcript: V.Transcript | None = None,
    documents: Any = None,
    required: bool = True,
    allowed_types: tuple[str, ...] = ("utterance", "memo", "document"),
) -> list[dict[str, Any]]:
    """근거 목록을 원문과 대조한다. 번호만 틀린 인용은 고치고, 실패는 failures에 남긴다."""
    if items is None:
        items = []
    if not isinstance(items, list):
        failures.append({"path": path, "message": "근거는 목록이어야 한다."})
        return []
    if required and not items:
        failures.append({"path": path, "message": "근거 인용이 없다."})
        return []
    checked = []
    for index, evidence in enumerate(items):
        item_path = f"{path}[{index}]"
        if isinstance(evidence, dict) and evidence.get("source_type") not in allowed_types:
            failures.append({"path": item_path, "message": f"이 자리에는 {', '.join(allowed_types)} 근거만 쓸 수 있다."})
            continue
        result = V.check_quote(evidence, transcript, documents)
        if result.status == "failed":
            failures.append({"path": item_path, "message": result.message})
            continue
        if result.status == "corrected":
            notes.append(f"{item_path}: {result.message}")
            run.count("자동 교정")
        checked.append(result.evidence or evidence)
    return checked


def render_evidence(items: Any, interview_id: str = "") -> str:
    if not isinstance(items, list):
        return ""
    return " ".join(V.format_evidence(item, interview_id) for item in items if isinstance(item, dict))


def with_evidence(value: str, items: Any, interview_id: str = "") -> str:
    citations = render_evidence(items, interview_id)
    return f"{value} {citations}".strip() if citations else str(value)


# ---------------------------------------------------------------------------
# 산출물 조립
# ---------------------------------------------------------------------------
def header_lines(run, confirmation: str = "(비어 있음)") -> list[str]:
    return [
        f"출처: {run.header.get('출처', '')}",
        f"인터뷰 식별자: {run.header.get('인터뷰 식별자', '')}",
        f"실행 버전: {run.header.get('실행 버전', '')}",
        f"엔지니어 확정: {confirmation}",
    ]


def fill_template(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if remaining:
        raise ValueError(f"템플릿에 채우지 못한 자리가 있다: {remaining}")
    return template.rstrip() + "\n"


def template_text(run, name: str) -> str:
    return (run.skill_dir / "templates" / name).read_text(encoding="utf-8")


def warnings_block(run, extra: list[str] | None = None) -> str:
    items = list(run.manifest["warnings"]) + list(extra or [])
    if not items:
        return ""
    return "## 검사 경고\n\n" + "\n".join(f"- {item}" for item in items) + "\n"


# ---------------------------------------------------------------------------
# 다시 만들기 병합: 실패한 항목만 새 출력에서 가져온다(3.5의 6)).
# ---------------------------------------------------------------------------
def failed_keys(failures: list[dict[str, str]], pattern: str) -> set[tuple[str, ...]]:
    keys = set()
    regex = re.compile(pattern)
    for failure in failures:
        match = regex.match(failure.get("path", ""))
        if match:
            keys.add(match.groups())
    return keys


def merge_list_items(previous: list[Any], new: list[Any], failed_indexes: set[int]) -> list[Any]:
    merged = copy.deepcopy(previous)
    for index in failed_indexes:
        if index < len(merged) and index < len(new):
            merged[index] = copy.deepcopy(new[index])
    return merged


# ---------------------------------------------------------------------------
# 입력 게이트
# ---------------------------------------------------------------------------
def load_pre_research(run, value: str | None):
    """검토 완료로 확정된 0단계 산출물만 받는다(규칙 2)."""
    if not value:
        return None
    record = run.load_output(value, "pre-research")
    run.require_header_confirmation(record, "검토 완료")
    return record


def naming_from_interview(run, interview_id: str, dept: str | None = None, profile: dict[str, Any] | None = None) -> dict[str, str]:
    from run import parse_interview_id

    parts = parse_interview_id(interview_id)
    department = dept or (profile or {}).get("부서") or parts["dept"]
    naming = {"client": parts["client"], "dept": str(department), "person": parts["person"], "dept_short": parts["dept"], "round": parts["round"]}
    run.state["naming"] = naming
    run.header["인터뷰 식별자"] = interview_id
    return naming


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def number_document(path: Path, text: str) -> dict[str, Any]:
    lines = text.splitlines()
    return {
        "filename": path.name,
        "status": "ready",
        "lines": lines,
        "numbered_text": "\n".join(f"L{index}: {line}" for index, line in enumerate(lines, 1)),
    }


# ---------------------------------------------------------------------------
# 앞 단계 카드의 근거와 대조 (3·4단계: 근거는 카드에 붙은 인용을 그대로 옮긴다)
# ---------------------------------------------------------------------------
def card_evidence_pool(card: dict[str, Any]) -> list[dict[str, Any]]:
    """2단계 카드의 필드와 조건표에 붙은 모든 근거."""
    fields = card["fields"]
    pool = list(fields["3"]["frequency"]["evidence"]) + list(fields["3"]["duration"]["evidence"])
    for key in ("4", "6", "7"):
        for item in fields[key]:
            pool += item["evidence"]
    pool += fields["5"]["manual"]["evidence"] + list(fields["9"])
    for condition in card.get("conditions", {}).values():
        pool += condition.get("evidence") or []
    return pool


def _same_source(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("source_type") != b.get("source_type"):
        return False
    if a.get("source_type") == "utterance":
        return str(a.get("q")) == str(b.get("q"))
    if a.get("source_type") == "memo":
        return str(a.get("m")) == str(b.get("m"))
    return str(a.get("filename")) == str(b.get("filename"))


def match_pool(evidence: Any, pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    """인용이 근거 목록의 어느 인용 안에 그대로 들어 있으면 그 근거를 돌려준다."""
    if not isinstance(evidence, dict):
        return None
    quote = V.normalize_quote(evidence.get("quote", ""))
    if not quote:
        return None
    for item in pool:
        if _same_source(evidence, item) and quote in V.normalize_quote(item.get("quote", "")):
            return {**item, "quote": evidence["quote"]}
    return None
