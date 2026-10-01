"""interview-sheet(1단계) 파이프라인: 재료 배분, 맞춤 질문 검사, 시트 조립, 템플릿·수량·F번호 검사."""

from __future__ import annotations

import json
import re
from typing import Any

import build_sheet as B
import skillkit as K
import validate as V
from run import Check, Material, Result, RunError

CONFIRMATION_HINT = "상단 '엔지니어 확정' 줄에 `사용 승인`을 적는다."
COMMON_QUESTIONS_HEADING = "## ② 공통 질문"


def add_arguments(parser) -> None:
    parser.add_argument("--interview-id", required=True, help="예: A사-영업-01-1회차")
    parser.add_argument("--profile", required=True, help="담당자 프로필 JSON(부서, 직무명, 직급, 담당 업무, 연차)")
    parser.add_argument("--format", required=True, choices=("대면", "온라인", "메일", "interview", "mail"))
    parser.add_argument("--duration", type=int, choices=(30, 60), help="대면·온라인일 때 30 또는 60")
    parser.add_argument("--pre-research", help="검토 완료된 0단계 산출물(outputs/…md)")
    parser.add_argument("--followup", help="2회차 이상: 확인 목록 파일('[F번호][종류] 내용' 줄 또는 JSON)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명(예: 영업부)")


def start(run) -> None:
    path, text = run.read_input(run.args["profile"])
    run.note_sources(path)
    try:
        profile = json.loads(text)
    except json.JSONDecodeError as error:
        raise RunError(f"프로필 JSON을 읽을 수 없다: {error}") from error
    naming = K.naming_from_interview(run, run.args["interview_id"], run.args.get("dept"), profile)
    round_number = int(naming["round"])

    pre_research_items: list[dict[str, Any]] = []
    record = K.load_pre_research(run, run.args.get("pre_research"))
    if record and not record.data.get("no_documents"):
        pre_research_items = record.data.get("followup_items", [])
    if record is None and round_number == 1:
        run.warn("0단계 산출물을 넣지 않았다. 사전 조사 기반 질문 없이 시트를 만든다.")

    followups: list[dict[str, Any]] = []
    if run.args.get("followup"):
        followup_path, followup_text = run.read_input(run.args["followup"])
        run.note_sources(followup_path)
        try:
            data = json.loads(followup_text)
            followups = data.get("items", data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            followups = B.parse_followup_text(followup_text)
    if round_number >= 2 and not followups:
        run.warn("2회차 이상인데 확인 목록이 없다. 필수 확인 주제로만 질문을 채운다.")

    try:
        plan = B.allocate_materials(profile, run.args["format"], run.args.get("duration"), round_number, pre_research_items, followups)
    except ValueError as error:
        raise RunError(str(error)) from error
    run.state["profile"] = profile
    run.state["plan"] = plan


def materials(run, call: str) -> list[Material]:
    plan = run.state["plan"]
    lines = []
    for index, material in enumerate(plan["materials"], 1):
        kind = {
            "필수확인주제": "필수 확인 주제",
            "필수확인주제심화": "필수 확인 주제(심화)",
            "사전조사확인": "사전 조사 확인 항목",
        }.get(material["kind"], f"확인 목록 항목({material['kind']})")
        extra = f" / 빠진 요소: {', '.join(material['missing_fields'])}" if material.get("missing_fields") else ""
        numbers = f" / {', '.join(material['f_numbers'])}" if material.get("f_numbers") else ""
        lines.append(f"{index}. [{kind}] {material['content']}{extra}{numbers}")
    template = K.template_text(run, B.template_name(plan))
    common = template.split("## 공통 질문" if plan["interview_format"] == "mail" else COMMON_QUESTIONS_HEADING, 1)[1].split("##", 1)[0]
    form = "메일" if plan["interview_format"] == "mail" else f"{run.args['format']} {plan['duration']}분"
    return [
        Material("담당자 프로필", K.dumps(run.state["profile"])),
        Material("인터뷰 형태", f"{form}, {plan['round']}회차, 직무 유형: {plan['job_type']['type_label']}"),
        Material("공통 질문 8개(참고용, 다시 쓰지 말 것)", re.sub(r"<!--.*?-->", "", common).strip()),
        Material("코드가 정한 맞춤 질문 개수", str(plan["question_count"])),
        Material("코드가 배분한 재료 항목", "\n".join(lines)),
    ]


def _clean_question(text: str) -> tuple[str, bool]:
    question = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", str(text)).strip()
    stripped = re.sub(r"\s*\((?:F\d+(?:,\s*)?)+\)\s*$", "", question)
    return stripped, stripped != question


def validate(run, call: str, data: Any) -> Check:
    plan = run.state["plan"]
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError('{"questions": [...]} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    cleaned = []
    for index, raw in enumerate(questions):
        question, had_f = _clean_question(raw)
        if had_f:
            notes.append(f"questions[{index}]: 질문 끝의 F번호는 코드가 붙이므로 뺐다.")
        if not question:
            failures.append({"path": f"questions[{index}]", "message": "질문이 비어 있다."})
        for error in V.style_errors(question, "polite"):
            failures.append({"path": f"questions[{index}]", "message": f"담당자에게 건네는 질문은 존댓말로 쓴다. {error}"})
        cleaned.append(question)
    if len(cleaned) != plan["question_count"]:
        failures.append({"path": "questions", "message": f"질문은 재료 항목마다 하나씩, 정확히 {plan['question_count']}개여야 한다(현재 {len(cleaned)}개)."})
    else:
        sheet = B.render_sheet(plan, cleaned, "\n".join(K.header_lines(run)))
        for error in V.interview_output_errors(sheet, plan["interview_format"], plan["question_count"], plan["followup_f_numbers"], B.fixed_phrases(plan)):
            failures.append({"path": "sheet", "message": error})
    return Check({"questions": cleaned}, failures, notes)


def merge_retry(run, call: str, previous: dict[str, Any], new: Any, failures: list[dict[str, str]]) -> dict[str, Any]:
    new_questions = new.get("questions") if isinstance(new, dict) else new
    old_questions = previous.get("questions", [])
    if not isinstance(new_questions, list) or len(old_questions) != run.state["plan"]["question_count"] or len(new_questions) != len(old_questions):
        return {"questions": new_questions if isinstance(new_questions, list) else old_questions}
    indexes = {int(key[0]) for key in K.failed_keys(failures, r"questions\[(\d+)\]")}
    return {"questions": K.merge_list_items(old_questions, new_questions, indexes)}


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    count = run.state["plan"]["question_count"]
    questions = list(data.get("questions", []))[:count]
    questions += ["[확인 필요: 맞춤 질문 작성 실패]"] * (count - len(questions))
    for failure in failures:
        run.warn(f"다시 만든 뒤에도 통과하지 못했다: {failure['path']}: {failure['message']}")
    return {"questions": questions}


def finish(run) -> Result:
    plan = run.state["plan"]
    questions = run.state["calls"]["custom_questions"]["questions"]
    sheet = B.render_sheet(plan, questions, "\n".join(K.header_lines(run)))
    block = K.warnings_block(run)
    markdown = sheet + ("\n" + block if block else "")
    data = {"plan": plan, "questions": questions, "interview_id": run.header.get("인터뷰 식별자")}
    return Result(markdown=markdown, data=data, stage=1)
