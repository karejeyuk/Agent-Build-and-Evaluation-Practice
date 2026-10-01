"""design-doc(4단계) 파이프라인: 순위표 확정 검사, 설계서 초안 검사(목차·근거·필수 승인 지점·문체), 품질 게이트, 조립."""

from __future__ import annotations

import copy
import re
from typing import Any

import skillkit as K
import validate as V
from run import Blocked, Check, Material, Result, RunError

CONFIRMATION_HINT = "상단 '엔지니어 확정' 줄에 `승인`을 적는다."
INSUFFICIENT = "[정보 부족: 2단계 보강 필요]"
APPROVAL_LINE = "사람 승인 후 실행"
INTEGRATIONS = ("API", "파일", "수동 복사")
SUBITEMS = {
    "1": ("agent_name", "purpose", "user", "run_time"),
    "2": ("must_do", "must_not_do"),
    "3": ("workflow", "approval_points"),
    "4": ("tools.name", "tools.integration"),
    "5": ("common_rules", "additional_rules"),
    "6": ("input_format", "output_format", "example"),
    "7": ("success_definition", "measurement"),
    "8": ("risks.situation", "risks.response"),
}
LABELS = {
    "agent_name": "이름", "purpose": "목적", "user": "사용자", "run_time": "실행 시점",
    "must_do": "해야 할 일", "must_not_do": "하지 말아야 할 일",
    "input_format": "입력 형식", "output_format": "출력 형식", "example": "예시 1건",
    "success_definition": "성공 정의", "measurement": "측정 방법",
}


def add_arguments(parser) -> None:
    parser.add_argument("--ranking", required=True, help="업무별 확정이 끝난 3단계 순위표(outputs/…_3단계_….md)")
    parser.add_argument("--card", required=True, help="설계할 업무의 카드 번호(즉시 착수나 검토 후 착수로 확정된 업무)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명")


# ---------------------------------------------------------------------------
# 입력 확정 검사
# ---------------------------------------------------------------------------
def load_ranking(run, value: str, card_id: str | None):
    """순위표의 모든 업무 확정이 규칙에 맞고, 설계할 업무가 착수로 확정됐는지 본다."""
    record = run.load_output(value, "fit-scoring")
    cells = {row.get("카드 번호", ""): row.get("엔지니어 확정", "") for row in V.parse_table(record.markdown, "## 종합 순위")}
    errors, target = [], None
    for row in record.data.get("rows", []):
        confirmed, reason = V.split_confirmation(cells.get(row["card_id"], ""))
        error = V.grade_confirmation_error(row["card_id"], row["grade"], row["scores"].get("error_impact"), confirmed, reason)
        if error:
            errors.append(error)
        if row["card_id"] == card_id:
            target = {**row, "confirmed_grade": confirmed}
    if errors:
        raise Blocked("3단계 순위표의 엔지니어 확정이 규칙에 맞지 않는다. " + " ".join(errors))
    if card_id is None:
        return record, None
    if target is None:
        raise RunError(f"순위표에 없는 카드 번호다: {card_id}")
    if target["confirmed_grade"] not in V.START_GRADES:
        raise Blocked(f"{card_id}는 '{target['confirmed_grade']}'로 확정됐다. 즉시 착수나 검토 후 착수로 확정한 업무만 설계한다.")
    return record, target


def check_input(run, target: str, card: str | None) -> None:
    load_ranking(run, target, card)


def start(run) -> None:
    record, row = load_ranking(run, run.args["ranking"], run.args["card"])
    card = row["card"]
    naming = dict(record.sidecar.get("naming", {}))
    naming["person"] = card["card_id"].rsplit("-", 2)[-2]
    if run.args.get("dept"):
        naming["dept"] = run.args["dept"]
    run.state["naming"] = naming
    run.header["인터뷰 식별자"] = card["interview_id"]
    run.note_sources(*[run.resolve(path) for path in [card.get("output")] if path])
    error_impact = row["scores"].get("error_impact")
    run.state.update(
        {
            "row": row,
            "card": card,
            "mandatory_approval": card.get("confirmed") == "B1형" or error_impact in (1, 2),
            "approval_reason": ", ".join(
                reason for reason, hit in (("B1형", card.get("confirmed") == "B1형"), (f"오류 영향도 {error_impact}점", error_impact in (1, 2))) if hit
            ),
        }
    )


def materials(run, call: str) -> list[Material]:
    card, row = run.state["card"], run.state["row"]
    card_text = "\n".join(
        [f"카드 번호: {card['card_id']} / 확정 분류: {card.get('confirmed')} / 인터뷰 식별자: {card['interview_id']}"]
        + [f"{key} {label}: {card['rendered'][key]}" for key, label in (("1", "업무명"), ("3", "빈도·소요 시간"), ("4", "사용 시스템"), ("5", "절차 요약"), ("6", "예외 상황"), ("7", "실수 영향"), ("8", "판단 개입 지점"), ("9", "근거 원문"), ("10", "미확인 항목"))]
    )
    scores = row["scores"]
    row_text = (
        f"등급 확정: {row['confirmed_grade']} / 반복성 {scores['repeatability']}, 절차 명확성 {scores['procedure_clarity']}, "
        f"데이터 접근성 {scores['data_accessibility']}, 오류 영향도 {scores['error_impact']}, 총점 {row['total']} / 비고: {', '.join(row['notes']) or '-'}"
    )
    approval = f"필요({run.state['approval_reason']}). 워크플로에 사람 승인 단계를 반드시 넣는다." if run.state["mandatory_approval"] else "필수 아님"
    return [
        Material("착수가 확정된 카드", card_text),
        Material("종합 순위표의 해당 업무 행", row_text),
        Material("필수 사람 승인 지점", approval),
    ]


# ---------------------------------------------------------------------------
# 검사
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _check_fact(run, item: dict[str, Any], path: str, failures, pool) -> dict[str, Any]:
    item = dict(item)
    basis = item.get("basis", "proposal")
    if basis not in ("fact", "proposal"):
        failures.append({"path": f"{path}.basis", "message": "basis는 fact 또는 proposal이다."})
    if basis == "fact":
        raw = item.get("evidence") or []
        if not raw:
            failures.append({"path": f"{path}.evidence", "message": "업무 사실에 카드 근거 인용이 없다."})
        matched = []
        for index, evidence in enumerate(raw):
            found = K.match_pool(evidence, pool)
            if found is None:
                failures.append({"path": f"{path}.evidence[{index}]", "message": "카드에 붙은 인용이 아니다. 근거는 그대로 옮긴다."})
            else:
                matched.append(found)
        item["evidence"] = matched
    else:
        item["evidence"] = []
    return item


def validate(run, call: str, data: Any) -> Check:
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        raise ValueError('{"sections": {...}, "business_summary": ""} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    pool = K.card_evidence_pool(run.state["card"])
    sections: dict[str, Any] = {}
    for number in map(str, range(1, 10)):
        raw = data["sections"].get(number)
        path = f"sections.{number}"
        if raw is None:
            failures.append({"path": path, "message": "목차가 빠졌다(9목차를 더하거나 빼지 않는다)."})
            raw = INSUFFICIENT
        if isinstance(raw, str):
            if not V.only_unknown(raw):
                failures.append({"path": path, "message": "목차는 객체이거나 '[정보 부족: 2단계 보강 필요]'여야 한다."})
                raw = INSUFFICIENT
            if number != "3":
                sections[number] = raw
                continue
            raw = {"workflow": raw, "approval_points": []}
        if not isinstance(raw, dict):
            failures.append({"path": path, "message": "목차는 객체여야 한다."})
            sections[number] = INSUFFICIENT
            continue
        section = dict(raw)
        if number == "3":
            workflow = section.get("workflow", INSUFFICIENT)
            if isinstance(workflow, list):
                checked = []
                for index, step in enumerate(workflow):
                    step = step if isinstance(step, dict) else {}
                    step_path = f"{path}.workflow[{index}]"
                    for key in ("who", "what", "tool"):
                        if not str(step.get(key) or "").strip():
                            failures.append({"path": f"{step_path}.{key}", "message": "워크플로 단계는 누가, 무엇을, 어떤 도구로를 모두 적는다."})
                    if step.get("who") not in ("사람", "에이전트", None, ""):
                        failures.append({"path": f"{step_path}.who", "message": "who는 '사람' 또는 '에이전트'다."})
                    checked.append(_check_fact(run, step, step_path, failures, pool))
                section["workflow"] = checked
            elif not V.only_unknown(workflow):
                failures.append({"path": f"{path}.workflow", "message": "워크플로는 단계 목록이거나 '[정보 부족: 2단계 보강 필요]'여야 한다."})
            section["approval_points"] = [str(item) for item in _as_list(section.get("approval_points"))]
        elif number == "4":
            tools = []
            for index, tool in enumerate(_as_list(section.get("tools"))):
                tool = tool if isinstance(tool, dict) else {"name": str(tool), "basis": "proposal"}
                integration = str(tool.get("integration") or "[확인 필요: 연동 방식]")
                if integration not in INTEGRATIONS and not V.only_unknown(integration):
                    failures.append({"path": f"{path}.tools[{index}].integration", "message": "연동 방식은 API, 파일, 수동 복사 가운데 하나다."})
                tools.append(_check_fact(run, {**tool, "integration": integration}, f"{path}.tools[{index}]", failures, pool))
            section["tools"] = tools
        elif number == "8":
            section["risks"] = [
                _check_fact(run, risk if isinstance(risk, dict) else {"situation": str(risk), "basis": "proposal"}, f"{path}.risks[{index}]", failures, pool)
                for index, risk in enumerate(_as_list(section.get("risks")))
            ]
        else:
            for key in SUBITEMS.get(number, ()):
                if key != "common_rules" and key not in section:
                    failures.append({"path": f"{path}.{key}", "message": "세부 항목이 빠졌다."})
        sections[number] = section

    if run.state["mandatory_approval"]:
        workflow = sections["3"].get("workflow")
        has_step = isinstance(workflow, list) and any(step.get("who") == "사람" and step.get("approval") for step in workflow)
        has_line = any(APPROVAL_LINE in point for point in sections["3"].get("approval_points", []))
        if not has_step and not (V.only_unknown(workflow) and has_line):
            failures.append({"path": "sections.3", "message": f"필수 사람 승인 지점이 없다({run.state['approval_reason']}). 워크플로에 who '사람', approval true 단계를 넣는다."})

    summary = str(data.get("business_summary") or "").strip()
    if not summary:
        failures.append({"path": "business_summary", "message": "현업용 1쪽 요약이 비어 있다."})
    for error in V.style_errors(summary, "polite"):
        failures.append({"path": "business_summary", "message": f"현업용 요약은 존댓말로 쓴다. {error}"})
    for error in V.style_errors(_section_text(sections), "plain"):
        failures.append({"path": "sections", "message": f"설계서 본문은 한다체로 쓴다. {error}"})
    return Check({"sections": sections, "business_summary": summary}, failures, [])


def _section_text(sections: dict[str, Any]) -> str:
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in ("evidence", "basis", "who", "integration", "approval"):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            texts.append(value)

    walk(sections)
    return "\n".join(texts)


def merge_retry(run, call: str, previous: dict[str, Any], new: Any, failures: list[dict[str, str]]) -> dict[str, Any]:
    merged = copy.deepcopy(previous)
    if not isinstance(new, dict):
        return merged
    new_sections = new.get("sections") or {}
    for failure in failures:
        match = re.match(r"sections\.(\d)", failure["path"])
        if match and match.group(1) in new_sections:
            merged["sections"][match.group(1)] = new_sections[match.group(1)]
        elif failure["path"] == "sections":
            merged["sections"] = new_sections or merged["sections"]
        elif failure["path"] == "business_summary":
            merged["business_summary"] = new.get("business_summary", merged["business_summary"])
    return merged


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    for failure in failures:
        run.warn(f"다시 만든 뒤에도 통과하지 못했다: {failure['path']}: {failure['message']}")
        match = re.match(r"sections\.(\d)\.(workflow|tools|risks)\[(\d+)\]", failure["path"])
        if match and ".evidence" in failure["path"]:
            number, key, index = match.group(1), match.group(2), int(match.group(3))
            items = result["sections"][number].get(key)
            if isinstance(items, list) and index < len(items):
                items[index]["basis"] = "unverified"
                items[index]["evidence"] = []
    if any(failure["path"] == "sections.3" for failure in failures):
        result["approval_missing"] = True
    return result


# ---------------------------------------------------------------------------
# 품질 게이트와 조립
# ---------------------------------------------------------------------------
def unknown_subitems(sections: dict[str, Any]) -> list[str]:
    """목차 1~8의 세부 항목 19개 가운데 [확인 필요]·[정보 부족]뿐인 항목을 센다. [정보 부족] 목차는 세부 항목을 모두 센다."""
    unknown = []
    for number, keys in SUBITEMS.items():
        section = sections.get(number)
        if isinstance(section, str):
            unknown += [f"{number}.{key}" for key in keys]
            continue
        for key in keys:
            if key == "common_rules":
                continue
            if "." in key:
                list_key, field = key.split(".")
                items = section.get(list_key) or []
                values = [item.get(field) for item in items if isinstance(item, dict)]
                if not values or all(V.only_unknown(value) for value in values):
                    unknown.append(f"{number}.{key}")
            elif number == "3" and key == "workflow":
                workflow = section.get("workflow")
                if not isinstance(workflow, list) or not workflow:
                    unknown.append("3.workflow")
            elif V.only_unknown(section.get(key)):
                unknown.append(f"{number}.{key}")
    return unknown


def _basis(item: dict[str, Any], prefix: str) -> str:
    if item.get("basis") == "fact":
        return K.render_evidence(item.get("evidence"), prefix) or "-"
    if item.get("basis") == "unverified":
        return "[확인 필요: 근거 인용 대조 실패]"
    return "(제안)"


def _bullets(value: Any) -> str:
    items = [str(item) for item in _as_list(value) if str(item).strip()]
    return "\n".join(f"- {item}" for item in items) or "- [확인 필요: 내용]"


def _render_sections(sections: dict[str, Any], prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}

    def plain(number: str, render) -> str:
        section = sections[number]
        return section if isinstance(section, str) else render(section)

    out["SECTION_1"] = plain("1", lambda s: "\n".join(f"- {LABELS[key]}: {s.get(key) or '[확인 필요: 내용]'}" for key in SUBITEMS["1"]))
    out["SECTION_2"] = plain("2", lambda s: f"### 해야 할 일\n\n{_bullets(s.get('must_do'))}\n\n### 하지 말아야 할 일\n\n{_bullets(s.get('must_not_do'))}")

    def workflow(section: dict[str, Any]) -> str:
        steps = section.get("workflow")
        if isinstance(steps, list) and steps:
            rows = ["| 단계 | 누가 | 무엇을 | 어떤 도구로 | 근거 |", "|---:|---|---|---|---|"]
            for index, step in enumerate(steps, 1):
                what = f"{step.get('what', '')}{' (사람 승인 지점)' if step.get('approval') else ''}"
                rows.append(f"| {index} | {step.get('who', '')} | {what} | {step.get('tool', '')} | {_basis(step, prefix)} |".replace("\n", " "))
            body = "\n".join(rows)
        else:
            body = str(steps or INSUFFICIENT)
        return f"### 단계별 흐름\n\n{body}\n\n### 사람 승인 지점\n\n{_bullets(section.get('approval_points')) if section.get('approval_points') else '- 없음'}"

    out["SECTION_3"] = plain("3", workflow)

    def tools(section: dict[str, Any]) -> str:
        items = section.get("tools") or []
        if not items:
            return "- [확인 필요: 사용 도구]"
        rows = ["| 도구명 | 연동 방식 | 근거 |", "|---|---|---|"]
        rows += [f"| {item.get('name', '')} | {item.get('integration', '')} | {_basis(item, prefix)} |" for item in items]
        return "\n".join(rows)

    out["SECTION_4"] = plain("4", tools)
    out["SECTION_5"] = plain("5", lambda s: _bullets(s.get("additional_rules")) if s.get("additional_rules") else "- 없음")
    out["SECTION_6"] = plain("6", lambda s: "\n".join(f"- {LABELS[key]}: {s.get(key) or '[확인 필요: 내용]'}" for key in SUBITEMS["6"]))
    out["SECTION_7"] = plain("7", lambda s: "\n".join(f"- {LABELS[key]}: {s.get(key) or '[확인 필요: 내용]'}" for key in SUBITEMS["7"]))

    def risks(section: dict[str, Any]) -> str:
        items = section.get("risks") or []
        if not items:
            return "- [확인 필요: 예상 오류 상황]"
        rows = ["| 예상 오류 상황 | 대응 행동 | 근거 |", "|---|---|---|"]
        rows += [f"| {item.get('situation', '')} | {item.get('response', '')} | {_basis(item, prefix)} |" for item in items]
        return "\n".join(rows)

    out["SECTION_8"] = plain("8", risks)
    out["SECTION_9"] = plain(
        "9",
        lambda s: f"### 미확인 항목\n\n{_bullets(s.get('unknowns')) if s.get('unknowns') else '- 없음'}\n\n### 업무 특이사항\n\n{_bullets(s.get('special_notes')) if s.get('special_notes') else '- 없음'}",
    )
    return out


def finish(run) -> Result:
    draft = run.state["calls"]["draft"]
    sections = draft["sections"]
    unknown = unknown_subitems(sections)
    gate = f"품질 게이트: 세부 항목 19개 가운데 미확인 {len(unknown)}개" + (" → 2단계 인터뷰 보강 권고" if len(unknown) >= 6 else " (통과)")
    if run.state["mandatory_approval"]:
        gate += f"\n필수 사람 승인 지점: 필요({run.state['approval_reason']})"
        if draft.get("approval_missing"):
            gate += " → 경고: 다시 만든 뒤에도 워크플로에 승인 지점이 없다. 엔지니어가 넣기 전에는 승인하지 않는다."
    values = {
        "HEADER": "\n".join(K.header_lines(run)),
        "GATE": gate,
        "BUSINESS_SUMMARY": draft.get("business_summary") or "[확인 필요: 현업용 요약]",
        "WARNINGS": K.warnings_block(run),
        **_render_sections(sections, ""),
    }
    markdown = K.fill_template(K.template_text(run, "design_doc.md"), values)
    row = run.state["row"]
    data = {
        "card_id": run.state["card"]["card_id"],
        "card": run.state["card"],
        "confirmed_type": run.state["card"].get("confirmed"),
        "confirmed_grade": row["confirmed_grade"],
        "scores": row["scores"],
        "annual_frequency": row.get("annual_frequency"),
        "duration_minutes": row.get("duration_minutes"),
        "sections": sections,
        "business_summary": draft.get("business_summary"),
        "unknown_subitems": unknown,
        "mandatory_approval": run.state["mandatory_approval"],
        "approval_missing": bool(draft.get("approval_missing")),
    }
    return Result(markdown=markdown, data=data, stage=4, card_id=run.state["card"]["card_id"])
