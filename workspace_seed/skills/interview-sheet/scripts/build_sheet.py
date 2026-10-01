"""맞춤 질문 재료를 배분하고, 확인 목록을 정렬해 몫에 맞춰 자른 뒤 고정 템플릿으로 시트를 조립한다(3.4).

LLM은 코드가 넘긴 재료 항목마다 질문 문장 하나만 만든다. 질문 수, 재료, 이월 블록,
메일 질문지의 엔지니어용 부록은 이 코드가 정한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from job_type import detect_job_type

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
FOLLOWUP_PRIORITY = {"이월": 0, "다음회차확인": 1, "문서후보": 2, "확인필요": 3, "정보부족": 4}
FOLLOWUP_ALIASES = {
    "다음 회차 확인": "다음회차확인",
    "문서에만 있는 후보": "문서후보",
    "확인 필요": "확인필요",
    "정보 부족": "정보부족",
}
FOLLOWUP_LINE = re.compile(r"^\s*\[(F\d+(?:\s*,\s*F\d+)*)\]\s*\[([^\]]+)\]\s*(.+?)\s*$")
QUESTION_COUNTS = {("interview", 30): 5, ("interview", 60): 10, ("mail", None): 2}


def _f_sort_key(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"F(\d+)", value)
    return (int(match.group(1)), value) if match else (sys.maxsize, value)


def _f_numbers(values: Any) -> list[str]:
    if isinstance(values, (str, int)):
        values = re.split(r"\s*,\s*", str(values))
    numbers = {str(value).strip() if str(value).strip().startswith("F") else f"F{str(value).strip()}" for value in values or [] if str(value).strip()}
    return sorted(numbers, key=_f_sort_key)


def parse_followup_text(text: str) -> list[dict[str, Any]]:
    """'[F3][확인필요] 내용' 한 줄 형식(합친 항목은 '[F3, F8][확인필요] 내용')을 읽는다."""
    items = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = FOLLOWUP_LINE.match(line)
        if not match:
            raise ValueError(f"확인 목록 {number}번째 줄이 '[F번호][종류] 내용' 형식이 아니다.")
        items.append({"f_numbers": _f_numbers(match.group(1)), "kind": match.group(2).strip(), "content": match.group(3)})
    return items


def _dedupe_key(item: dict[str, Any]) -> str:
    explicit = str(item.get("key", "")).strip()
    if explicit:
        return explicit.casefold()
    return re.sub(r"[\W_]+", "", item["content"], flags=re.UNICODE).casefold()


def normalize_followup(items: list[Any]) -> list[dict[str, Any]]:
    """같은 항목을 합치고(앞 순위 종류, F번호 모두 보존) 종류 순서와 F번호 순으로 정렬한다."""
    merged: dict[str, dict[str, Any]] = {}
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("content", "")).strip():
            raise ValueError("확인 목록 항목은 kind와 content가 있어야 한다.")
        original_kind = str(raw.get("kind", raw.get("type", ""))).strip()
        kind = FOLLOWUP_ALIASES.get(original_kind, original_kind)
        if kind not in FOLLOWUP_PRIORITY:
            raise ValueError(f"알 수 없는 확인 목록 종류다: {original_kind}")
        item = {"kind": kind, "content": str(raw["content"]).strip(), "f_numbers": _f_numbers(raw.get("f_numbers", []))}
        if not item["f_numbers"]:
            raise ValueError(f"확인 목록 항목에 F번호가 없다: {item['content'][:20]}")
        key = _dedupe_key(item)
        current = merged.get(key)
        if current is None:
            merged[key] = item
            continue
        current["f_numbers"] = sorted(set(current["f_numbers"] + item["f_numbers"]), key=_f_sort_key)
        if FOLLOWUP_PRIORITY[kind] < FOLLOWUP_PRIORITY[current["kind"]]:
            current["kind"] = kind
    return sorted(merged.values(), key=lambda item: (FOLLOWUP_PRIORITY[item["kind"]], _f_sort_key(item["f_numbers"][0])))


def _missing_fields(item: dict[str, Any]) -> list[str]:
    value = item.get("missing_fields", [])
    if isinstance(value, str):
        value = re.split(r"\s*[,/·]\s*", value)
    return [str(field).replace(" ", "") for field in value or [] if str(field).strip()]


def sort_pre_research_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """절차가 빠진 항목, 담당자가 빠진 항목, 나머지 순으로 고르고 같은 순위는 요약서 순서를 지킨다."""
    def priority(item: dict[str, Any]) -> int:
        missing = _missing_fields(item)
        if "절차" in missing:
            return 0
        if "담당자" in missing:
            return 1
        return 2

    return sorted(items, key=priority)


def _pre_research_material(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "사전조사확인",
        "content": str(item.get("content", "")).strip(),
        "missing_fields": [field if field != "사용시스템" else "사용 시스템" for field in _missing_fields(item)],
        "f_numbers": [],
    }


def _topic(content: str, kind: str = "필수확인주제") -> dict[str, Any]:
    return {"kind": kind, "content": content, "f_numbers": []}


def allocate_materials(
    profile: dict[str, Any],
    interview_format: str,
    duration: int | None,
    round_number: int,
    pre_research_items: list[dict[str, Any]] | None = None,
    followup_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """입력 조건에 맞는 고정 개수의 질문 재료와 이월 항목을 정한다."""
    aliases = {"대면": "interview", "온라인": "interview", "메일": "mail"}
    interview_format = aliases.get(interview_format, interview_format)
    if interview_format not in {"interview", "mail"}:
        raise ValueError("형태는 대면, 온라인, 메일 가운데 하나여야 한다.")
    if interview_format == "mail":
        duration = None
    elif duration not in (30, 60):
        raise ValueError("대면·온라인 인터뷰 시간은 30분 또는 60분이어야 한다.")
    if round_number < 1:
        raise ValueError("회차는 1 이상이어야 한다.")

    job = detect_job_type(profile)
    topics = job["required_topics"]
    deeper = [_topic(topic, "필수확인주제심화") for topic in topics]
    count = QUESTION_COUNTS[(interview_format, duration)]
    research = sort_pre_research_items([_pre_research_material(item) for item in pre_research_items or []])
    followups = normalize_followup(followup_items or [])

    if round_number >= 2:
        # 3.4의 4): 확인 목록이 사전 조사 확인 항목을 대신하고, 남는 자리는 필수 확인 주제, 심화 순으로 채운다.
        selected = followups[:count]
        selected += [_topic(topic) for topic in topics][: count - len(selected)]
        selected += deeper[: count - len(selected)]
        carryover = followups[count:]
    elif interview_format == "mail":
        if research:
            selected, carryover = [research[0], _topic(topics[0])], research[1:]
        else:
            selected, carryover = [_topic(topic) for topic in topics[:2]], []
    elif duration == 30:
        selected = [_topic(topic) for topic in topics[:3]] + research[:2]
        selected += [_topic(topic) for topic in topics[3:]][: count - len(selected)]
        carryover = research[2:]
    else:
        selected = [_topic(topic) for topic in topics] + research[:5]
        selected += deeper[: count - len(selected)]
        carryover = research[5:]

    if len(selected) != count:
        raise ValueError(f"질문 재료 수가 {count}개가 아니다: {len(selected)}개")
    return {
        "interview_format": interview_format,
        "duration": duration,
        "round": round_number,
        "job_type": job,
        "question_count": count,
        "materials": selected,
        "carryover": carryover,
        "followup_f_numbers": sorted({number for item in followups for number in item["f_numbers"]}, key=_f_sort_key),
    }


def _render_questions(plan: dict[str, Any], questions: list[str]) -> str:
    rendered = []
    for index, (question, material) in enumerate(zip(questions, plan["materials"]), 1):
        suffix = ""
        if plan["interview_format"] == "interview" and material.get("f_numbers"):
            suffix = f" ({', '.join(material['f_numbers'])})"
        rendered.append(f"{index}. {str(question).strip()}{suffix}")
    return "\n".join(rendered)


def _render_carryover(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- 없음"
    rows = []
    for item in items:
        prefix = f"[{', '.join(item['f_numbers'])}] " if item.get("f_numbers") else ""
        missing = f" (빠진 요소: {', '.join(item['missing_fields'])})" if item.get("missing_fields") else ""
        rows.append(f"- {prefix}{item['content']}{missing}")
    return "\n".join(rows)


def _render_mail_appendix(plan: dict[str, Any]) -> str:
    mappings = [
        f"- 맞춤 문항 {index}: {', '.join(material['f_numbers'])}"
        for index, material in enumerate(plan["materials"], 1)
        if material.get("f_numbers")
    ] or ["- 확인 목록에서 가져온 문항 없음"]
    return "\n".join(["## 엔지니어용 부록", "", "### 문항과 확인 목록 대응", "", *mappings, "", "### 다음 회차 이월", "", _render_carryover(plan["carryover"])])


def template_name(plan: dict[str, Any]) -> str:
    return "mail_questionnaire.md" if plan["interview_format"] == "mail" else "interview_sheet.md"


def fixed_phrases(plan: dict[str, Any], template_dir: Path = TEMPLATE_DIR) -> list[str]:
    """템플릿의 고정 문구(채울 자리가 없는 줄)를 돌려준다."""
    text = (template_dir / template_name(plan)).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and "{{" not in line]


def render_sheet(plan: dict[str, Any], questions: list[str], header: str, template_dir: Path = TEMPLATE_DIR) -> str:
    """고정 문구와 맞춤 질문을 템플릿으로 조립한다."""
    if len(questions) != plan["question_count"]:
        raise ValueError(f"맞춤 질문은 {plan['question_count']}개여야 한다. 현재 {len(questions)}개다.")
    values = {"HEADER": header, "CUSTOM_QUESTIONS": _render_questions(plan, questions)}
    if plan["interview_format"] == "mail":
        values["ENGINEER_APPENDIX"] = _render_mail_appendix(plan)
    else:
        values["DURATION"] = str(plan["duration"])
        values["CARRYOVER"] = _render_carryover(plan["carryover"])
    text = (template_dir / template_name(plan)).read_text(encoding="utf-8")
    for token, value in values.items():
        text = text.replace("{{" + token + "}}", value)
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if remaining:
        raise ValueError(f"템플릿에 채우지 못한 자리가 있다: {remaining}")
    return text.rstrip() + "\n"
