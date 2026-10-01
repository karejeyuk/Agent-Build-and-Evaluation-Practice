#!/usr/bin/env python3
"""기획서 규칙으로 인터뷰 질문 재료를 배분하고 고정 템플릿을 조립한다."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from job_type import detect_job_type

SKILL_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SKILL_DIR / "templates"
FOLLOWUP_PRIORITY = {
    "이월": 0,
    "다음회차확인": 1,
    "문서후보": 2,
    "확인필요": 3,
    "정보부족": 4,
}
FOLLOWUP_ALIASES = {
    "다음 회차 확인": "다음회차확인",
    "문서에만 있는 후보": "문서후보",
    "확인 필요": "확인필요",
    "정보 부족": "정보부족",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: JSON 최상위 값은 객체여야 합니다.")
        return data
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: YAML front matter가 없습니다.")
    _, front_matter, _ = text.split("---", 2)
    data = yaml.safe_load(front_matter)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML front matter는 객체여야 합니다.")
    return data


def _as_material(item: Any, kind: str) -> dict[str, Any]:
    if isinstance(item, str):
        return {"kind": kind, "content": item, "f_numbers": []}
    if not isinstance(item, dict) or not str(item.get("content", "")).strip():
        raise ValueError(f"질문 재료는 content가 있는 문자열 또는 객체여야 합니다: {item!r}")
    material = dict(item)
    material["kind"] = kind
    material["content"] = str(material["content"]).strip()
    numbers = material.get("f_numbers", [])
    if isinstance(numbers, (str, int)):
        numbers = [numbers]
    found = re.findall(r"\bF\d+\b", material["content"])
    material["f_numbers"] = sorted(
        {str(value) if str(value).startswith("F") else f"F{value}" for value in [*numbers, *found]},
        key=_f_sort_key,
    )
    return material


def _f_sort_key(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"F(\d+)", value)
    return (int(match.group(1)), value) if match else (sys.maxsize, value)


def _dedupe_key(item: dict[str, Any]) -> str:
    explicit = str(item.get("key", "")).strip()
    if explicit:
        return explicit.casefold()
    return re.sub(r"[\W_]+", "", item["content"], flags=re.UNICODE).casefold()


def normalize_followup(items: list[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("확인 목록 항목은 kind와 content가 있는 객체여야 합니다.")
        original_kind = str(raw.get("kind", raw.get("type", ""))).strip()
        kind = FOLLOWUP_ALIASES.get(original_kind, original_kind)
        if kind not in FOLLOWUP_PRIORITY:
            raise ValueError(f"알 수 없는 확인 목록 종류입니다: {original_kind}")
        item = _as_material(raw, kind)
        key = _dedupe_key(item)
        if key not in merged:
            merged[key] = item
            continue
        current = merged[key]
        current["f_numbers"] = sorted(
            set(current["f_numbers"] + item["f_numbers"]), key=_f_sort_key
        )
        if FOLLOWUP_PRIORITY[kind] < FOLLOWUP_PRIORITY[current["kind"]]:
            current["kind"] = kind
            current["content"] = item["content"]
    return sorted(
        merged.values(),
        key=lambda item: (
            FOLLOWUP_PRIORITY[item["kind"]],
            min((_f_sort_key(number) for number in item["f_numbers"]), default=(sys.maxsize, "")),
        ),
    )


def _load_pre_research(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    metadata = _metadata(path)
    if metadata.get("status") == "no_pre_research":
        return []
    items = metadata.get("followup_items", [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: followup_items는 목록이어야 합니다.")
    return sort_pre_research_items(
        [_as_material(item, "사전조사확인") for item in items]
    )


def sort_pre_research_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """절차 누락, 담당자 누락, 그 밖의 항목 순으로 안정 정렬한다."""
    def priority(item: dict[str, Any]) -> int:
        missing = {str(value).replace(" ", "") for value in item.get("missing_fields", [])}
        if any("절차" in value for value in missing):
            return 0
        if any("담당자" in value for value in missing):
            return 1
        return 2

    return sorted(items, key=priority)


def _load_followup(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = _read_json(path)
    items = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: 확인 목록은 객체 또는 목록이어야 합니다.")
    return normalize_followup(items)


def _topic_item(content: str, kind: str) -> dict[str, Any]:
    return {"kind": kind, "content": content, "f_numbers": []}


def allocate_materials(
    profile: dict[str, Any],
    interview_format: str,
    duration: int | None,
    round_number: int,
    pre_research_items: list[dict[str, Any]] | None = None,
    followup_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """입력 조건에 맞는 고정 개수의 LLM 질문 재료를 반환한다."""
    aliases = {"대면": "interview", "온라인": "interview", "메일": "mail"}
    interview_format = aliases.get(interview_format, interview_format)
    if interview_format not in {"interview", "mail"}:
        raise ValueError("형태는 interview 또는 mail이어야 합니다.")
    if round_number not in {1, 2}:
        raise ValueError("회차는 1 또는 2여야 합니다. v1은 2회차 이상을 2로 표현합니다.")
    if interview_format == "interview" and duration not in {30, 60}:
        raise ValueError("대면·온라인 인터뷰 시간은 30 또는 60분이어야 합니다.")

    profile_responsibilities = profile.get(
        "담당 업무", profile.get("담당업무", profile.get("responsibilities", ""))
    )
    detection_profile = dict(profile)
    detection_profile["담당 업무"] = profile_responsibilities
    job = detect_job_type(detection_profile)
    required_topics = job["required_topics"]
    count = 2 if interview_format == "mail" else (5 if duration == 30 else 10)
    research = sort_pre_research_items(list(pre_research_items or []))
    followups = list(followup_items or [])
    selected: list[dict[str, Any]] = []

    if round_number >= 2:
        selected.extend(followups[:count])
        remaining = count - len(selected)
        selected.extend(
            _topic_item(topic, "필수확인주제") for topic in required_topics[:remaining]
        )
        remaining = count - len(selected)
        advanced_topics = [f"{topic}: 예외 상황과 세부 처리 절차" for topic in required_topics]
        selected.extend(
            _topic_item(topic, "필수확인주제심화")
            for topic in advanced_topics[:remaining]
        )
        carryover = followups[count:]
    elif interview_format == "mail":
        if research:
            selected.append(research[0])
            selected.append(_topic_item(required_topics[0], "필수확인주제"))
            carryover = research[1:]
        else:
            selected = [
                _topic_item(topic, "필수확인주제") for topic in required_topics[:2]
            ]
            carryover = []
    elif duration == 30:
        selected = [_topic_item(topic, "필수확인주제") for topic in required_topics[:3]]
        selected.extend(research[:2])
        if len(selected) < count:
            selected.extend(
                _topic_item(topic, "필수확인주제")
                for topic in required_topics[3 : 3 + count - len(selected)]
            )
        carryover = research[2:]
    else:
        selected = [_topic_item(topic, "필수확인주제") for topic in required_topics]
        selected.extend(research[:5])
        if len(selected) < count:
            advanced_topics = [f"{topic}: 예외 상황과 세부 처리 절차" for topic in required_topics]
            selected.extend(
                _topic_item(topic, "필수확인주제심화")
                for topic in advanced_topics[: count - len(selected)]
            )
        carryover = research[5:]

    if len(selected) != count:
        raise ValueError(f"질문 재료 수가 {count}개가 아닙니다: {len(selected)}개")

    return {
        "profile": profile,
        "interview_format": interview_format,
        "duration": duration,
        "round": round_number,
        "job_type": job,
        "question_count": count,
        "materials": selected,
        "carryover": carryover,
    }


def _format_f_numbers(numbers: list[str]) -> str:
    return ", ".join(numbers)


def _render_questions(plan: dict[str, Any], questions: list[str]) -> str:
    if len(questions) != plan["question_count"]:
        raise ValueError(
            f"맞춤 질문은 {plan['question_count']}개여야 합니다. 현재 {len(questions)}개입니다."
        )
    rendered: list[str] = []
    for index, (question, material) in enumerate(zip(questions, plan["materials"]), 1):
        question = str(question).strip()
        if not question:
            raise ValueError(f"{index}번 맞춤 질문이 비어 있습니다.")
        suffix = ""
        if plan["interview_format"] == "interview" and material["kind"] in {
            "확인목록항목",
            "이월",
            "다음회차확인",
            "문서후보",
            "확인필요",
            "정보부족",
        } and material.get("f_numbers"):
            suffix = f" ({_format_f_numbers(material['f_numbers'])})"
        rendered.append(f"{index}. {question}{suffix}")
    return "\n".join(rendered)


def _render_carryover(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- 없음"
    rows = []
    for item in items:
        numbers = _format_f_numbers(item.get("f_numbers", []))
        prefix = f"[{numbers}] " if numbers else ""
        rows.append(f"- {prefix}{item['content']}")
    return "\n".join(rows)


def _render_mail_appendix(plan: dict[str, Any]) -> str:
    mappings = []
    for index, material in enumerate(plan["materials"], 1):
        if material.get("f_numbers"):
            mappings.append(f"- 맞춤 문항 {index}: {_format_f_numbers(material['f_numbers'])}")
    if not mappings:
        mappings = ["- 확인 목록 항목에서 가져온 문항 없음"]
    return "\n".join(
        [
            "## 엔지니어용 부록",
            "### 문항과 확인 목록 대응",
            *mappings,
            "### 다음 회차 이월",
            _render_carryover(plan["carryover"]),
        ]
    )


def _replace_tokens(template: str, values: dict[str, str]) -> str:
    for token, value in values.items():
        template = template.replace("{{" + token + "}}", value)
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if remaining:
        raise ValueError(f"템플릿에 치환되지 않은 항목이 있습니다: {remaining}")
    return template.rstrip() + "\n"


def render_sheet(
    plan: dict[str, Any],
    questions: list[str],
    source: str,
    interview_id: str,
    execution_version: str,
    template_dir: Path = TEMPLATE_DIR,
) -> str:
    """코드 고정 문구와 검증된 LLM 질문을 템플릿으로 조립한다."""
    values = {
        "SOURCE": source,
        "INTERVIEW_ID": interview_id,
        "EXECUTION_VERSION": execution_version,
        "CUSTOM_QUESTIONS": _render_questions(plan, questions),
    }
    if plan["interview_format"] == "mail":
        values["ENGINEER_APPENDIX"] = _render_mail_appendix(plan)
        template = (template_dir / "mail_questionnaire.md").read_text(encoding="utf-8")
    else:
        values["DURATION"] = str(plan["duration"])
        values["CARRYOVER"] = _render_carryover(plan["carryover"])
        template = (template_dir / "interview_sheet.md").read_text(encoding="utf-8")
    return _replace_tokens(template, values)


def main() -> None:
    parser = argparse.ArgumentParser(description="인터뷰 질문 재료를 배분하고 템플릿을 조립합니다.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("allocate")
    plan_parser.add_argument("--profile", type=Path, required=True)
    plan_parser.add_argument("--format", choices=("interview", "mail", "대면", "온라인", "메일"), required=True)
    plan_parser.add_argument("--duration", type=int, choices=(30, 60))
    plan_parser.add_argument("--round", type=int, choices=(1, 2), default=1)
    plan_parser.add_argument("--pre-research", type=Path)
    plan_parser.add_argument("--followup", type=Path)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--plan", type=Path, required=True)
    build_parser.add_argument("--questions", type=Path, required=True)
    build_parser.add_argument("--source", required=True)
    build_parser.add_argument("--interview-id", default="[확인 필요: 인터뷰 식별자]")
    build_parser.add_argument("--execution-version", required=True)
    build_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "allocate":
        profile = _read_json(args.profile)
        pre_research_items = _load_pre_research(args.pre_research)
        followup_items = _load_followup(args.followup)
        result = allocate_materials(
            profile,
            args.format,
            args.duration,
            args.round,
            pre_research_items,
            followup_items,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    plan = _read_json(args.plan)
    question_data = _read_json(args.questions)
    questions = question_data["questions"] if isinstance(question_data, dict) else question_data
    output = render_sheet(
        plan,
        questions,
        args.source,
        args.interview_id,
        args.execution_version,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"인터뷰 자료 작성 완료: {args.output}")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
