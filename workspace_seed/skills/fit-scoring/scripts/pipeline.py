"""fit-scoring(3단계) 파이프라인: 카드 확정 검사, 채점 대상 선별, 칸 선택 검사, 점수·등급 계산, 순위표 조립."""

from __future__ import annotations

import re
from typing import Any

import scoring as S
import skillkit as K
import validate as V
from run import Blocked, Check, Material, Result

CONFIRMATION_HINT = (
    "순위표 '엔지니어 확정' 칸에 업무마다 즉시 착수, 검토 후 착수, 착수 보류, 다음 회차 확인 가운데 하나를 적는다. "
    "제안과 다르면 같은 칸에 '(변경 사유: …)'를 덧붙인다. 평가 불가 업무는 다음 회차 확인이나 착수 보류만 쓸 수 있다."
)
SELECTED = ("procedure_clarity", "data_accessibility", "error_impact")


def add_arguments(parser) -> None:
    parser.add_argument("--cards", action="append", required=True, help="카드별 확정이 끝난 2단계 산출물(outputs/…_2단계_….md). 여러 번 줄 수 있다")
    parser.add_argument("--pre-research", help="검토 완료된 0단계 산출물(시스템·데이터 원천 정리, 선택)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명")


# ---------------------------------------------------------------------------
# 입력 확정 검사 (규칙 2, 5.4 엔지니어 확정)
# ---------------------------------------------------------------------------
def load_confirmed_cards(run, values: list[str]) -> list[dict[str, Any]]:
    """카드마다 확정 값이 규칙에 맞는 2단계 산출물만 받는다. 하나라도 어긋나면 멈춘다."""
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for value in values:
        record = run.load_output(value, "task-card")
        confirmations = V.parse_card_confirmations(record.markdown)
        file_ids: set[str] = set()
        for card in record.data.get("cards", []):
            card_id = card["card_id"]
            if card_id in file_ids:
                errors.append(f"{card_id}: 한 산출물 안에서 카드 번호가 겹친다.")
            file_ids.add(card_id)
            confirmed, reason = confirmations.get(card_id, ("", ""))
            error = V.card_confirmation_error(card_id, card["proposal"], confirmed, reason)
            if error:
                errors.append(error)
                continue
            if card_id in seen:
                errors.append(f"{card_id}: 같은 카드 번호가 여러 산출물에 있다. 회차가 다른 같은 업무면 하나만 넣거나 '직전 카드'를 확인한다.")
            seen.add(card_id)
            cards.append({**card, "confirmed": confirmed, "change_reason": reason, "output": run.rel(record.path), "naming": record.sidecar.get("naming", {})})
    if errors:
        raise Blocked("2단계 산출물의 엔지니어 확정이 끝나지 않았다. " + " ".join(errors))
    return cards


def check_input(run, target: str, card: str | None) -> None:
    load_confirmed_cards(run, [target])


# ---------------------------------------------------------------------------
# 시작
# ---------------------------------------------------------------------------
def start(run) -> None:
    cards = load_confirmed_cards(run, run.args["cards"])
    latest = S.select_latest(cards)
    targets = sorted((card for card in latest if card["confirmed"] in ("A형", "B1형")), key=lambda card: card["card_id"])
    standardization = sorted((card for card in latest if card["confirmed"] == "B2형"), key=lambda card: card["card_id"])
    excluded = sorted(f"{card['card_id']}({card['confirmed']})" for card in latest if card["confirmed"] not in ("A형", "B1형", "B2형"))
    interview_ids = sorted({card["interview_id"] for card in cards})

    first = cards[0]["naming"] if cards else {}
    persons = {card["card_id"].rsplit("-", 2)[-2] for card in cards}
    run.state["naming"] = {
        "client": first.get("client", ""),
        "dept": run.args.get("dept") or first.get("dept", ""),
        "person": persons.pop() if len(persons) == 1 else "공통",
    }
    run.header["인터뷰 식별자"] = ", ".join(interview_ids) or "[확인 필요: 인터뷰 식별자]"
    record = K.load_pre_research(run, run.args.get("pre_research"))
    systems = ""
    if record and "## 시스템·데이터 원천" in record.markdown:
        systems = record.markdown.split("## 시스템·데이터 원천", 1)[1].split("\n## ", 1)[0].strip()
    run.state.update(
        {
            "targets": targets,
            "standardization": standardization,
            "excluded": excluded,
            "multi": len(interview_ids) > 1,
            "systems": systems,
            "system_pool": [item for system in (record.data.get("systems", []) if record else []) for item in system.get("evidence", [])],
        }
    )
    if excluded:
        run.warn(f"채점하지 않는 카드(C형·다음 회차 확인): {', '.join(excluded)}")
    if not targets:
        run.state["skip_llm"] = True


def _prefix(run, card: dict[str, Any]) -> str:
    return card["interview_id"] if run.state["multi"] else ""


def materials(run, call: str) -> list[Material]:
    if call == "select":
        blocks = []
        for card in run.state["targets"]:
            rendered = card["rendered"]
            if run.state["multi"]:
                rendered = {key: _with_prefix(card, key) for key in rendered}
            blocks.append(
                "\n".join(
                    [
                        f"카드 번호: {card['card_id']} ({card['interview_id']})",
                        f"업무명: {card['name']} / 확정 분류: {card['confirmed']}",
                        f"3 빈도·소요 시간: {rendered['3']}",
                        f"4 사용 시스템: {rendered['4']}",
                        f"5 절차 요약: {rendered['5']}",
                        f"6 예외 상황: {rendered['6']}",
                        f"7 실수 영향: {rendered['7']}",
                        f"9 근거 원문: {rendered['9']}",
                    ]
                )
            )
        materials = [Material("채점 대상 카드", "\n\n".join(blocks))]
        if run.state["systems"]:
            materials.append(Material("0단계 시스템·데이터 원천 정리", run.state["systems"]))
        return materials
    rows = [_row_text(run, row) for row in run.state["summary_targets"]]
    return [Material("요약 대상 업무와 순위표 행", "\n\n".join(rows))]


def _with_prefix(card: dict[str, Any], key: str) -> str:
    return card["rendered"][key].replace("(발화 근거: ", f"(발화 근거: {card['interview_id']} ")


# ---------------------------------------------------------------------------
# 1차 호출(칸 선택) 검사
# ---------------------------------------------------------------------------
def _validate_select(run, data: dict[str, Any]) -> Check:
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise ValueError('{"cards": [...]} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    targets = {card["card_id"]: card for card in run.state["targets"]}
    result: dict[str, Any] = {}
    for raw in data["cards"]:
        raw = raw if isinstance(raw, dict) else {}
        card_id = str(raw.get("card_id") or "")
        if card_id not in targets:
            failures.append({"path": f"cards[{card_id}]", "message": f"채점 대상이 아닌 카드 번호다({', '.join(targets)} 가운데서만 쓴다)."})
            continue
        pool = K.card_evidence_pool(targets[card_id]) + list(run.state["system_pool"])
        criteria = raw.get("criteria") or {}
        selections = {}
        for key in SELECTED:
            path = f"cards[{card_id}].criteria.{key}"
            item = criteria.get(key) if isinstance(criteria.get(key), dict) else {}
            level = item.get("level", item.get("score"))
            if level is None:
                selections[key] = {"level": None, "reason": str(item.get("reason") or f"[확인 필요: {S.CRITERIA_LABELS[key]} 근거]"), "evidence": []}
                continue
            if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
                failures.append({"path": path, "message": "칸은 1~5 가운데 하나로 고른다."})
                selections[key] = {"level": None, "reason": "", "evidence": []}
                continue
            evidence = []
            raw_evidence = item.get("evidence") or []
            if not raw_evidence:
                failures.append({"path": path, "message": "고른 칸에 근거 인용이 없다."})
            for index, quote in enumerate(raw_evidence):
                matched = K.match_pool(quote, pool)
                if matched is None:
                    failures.append({"path": f"{path}.evidence[{index}]", "message": "카드나 0단계 정리에 붙은 인용이 아니다. 근거는 그대로 옮긴다."})
                else:
                    evidence.append(matched)
            selections[key] = {"level": level, "reason": "", "evidence": evidence}
        result[card_id] = selections
    for card_id in targets:
        if card_id not in result:
            failures.append({"path": f"cards[{card_id}]", "message": "이 카드의 칸 선택이 없다."})
    return Check({"cards": result}, failures, notes)


def _validate_summary(run, data: dict[str, Any]) -> Check:
    if not isinstance(data, dict) or not isinstance(data.get("summaries"), list):
        raise ValueError('{"summaries": [...]} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    targets = {row["card_id"]: row for row in run.state["summary_targets"]}
    result: dict[str, str] = {}
    for item in data["summaries"]:
        item = item if isinstance(item, dict) else {}
        card_id = str(item.get("card_id") or "")
        text = str(item.get("text") or "").strip()
        path = f"summaries[{card_id}]"
        if card_id not in targets:
            failures.append({"path": path, "message": "요약 대상이 아닌 카드 번호다."})
            continue
        if not text:
            failures.append({"path": path, "message": "요약이 비어 있다."})
        for error in V.style_errors(text, "polite"):
            failures.append({"path": path, "message": f"고객사에 건네는 요약은 존댓말로 쓴다. {error}"})
        allowed = set(re.findall(r"\d+(?:\.\d+)?", _row_text(run, targets[card_id])))
        foreign = sorted(set(re.findall(r"\d+(?:\.\d+)?", text)) - allowed)
        if foreign:
            failures.append({"path": path, "message": f"순위표 행에 없는 수치가 있다: {', '.join(foreign)}"})
        result[card_id] = text
    for card_id in targets:
        if card_id not in result:
            failures.append({"path": f"summaries[{card_id}]", "message": "이 업무의 요약이 없다."})
    return Check({"summaries": result}, failures, [])


def validate(run, call: str, data: Any) -> Check:
    return _validate_select(run, data) if call == "select" else _validate_summary(run, data)


def merge_retry(run, call: str, previous: dict[str, Any], new: Any, failures: list[dict[str, str]]) -> dict[str, Any]:
    failed = {match.group(1) for failure in failures if (match := re.match(r"(?:cards|summaries)\[([^\]]*)\]", failure["path"]))}
    if call == "select":
        merged = {card_id: selections for card_id, selections in previous["cards"].items() if card_id not in failed}
        new_cards = {str(card.get("card_id")): card for card in (new.get("cards") or [])} if isinstance(new, dict) else {}
        out = [{"card_id": card_id, "criteria": selections} for card_id, selections in merged.items()]
        out += [new_cards[card_id] for card_id in failed if card_id in new_cards]
        return {"cards": out}
    merged = {card_id: text for card_id, text in previous["summaries"].items() if card_id not in failed}
    new_items = {str(item.get("card_id")): item.get("text", "") for item in (new.get("summaries") or [])} if isinstance(new, dict) else {}
    merged.update({card_id: new_items[card_id] for card_id in failed if card_id in new_items})
    return {"summaries": [{"card_id": card_id, "text": text} for card_id, text in merged.items()]}


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    if call == "select":
        cards = data["cards"]
        for failure in failures:
            match = re.match(r"cards\[([^\]]*)\](?:\.criteria\.(\w+))?", failure["path"])
            if not match:
                continue
            card_id, key = match.group(1), match.group(2)
            if card_id not in {card["card_id"] for card in run.state["targets"]}:
                continue
            keys = [key] if key else list(SELECTED)
            for name in keys:
                cards.setdefault(card_id, {})[name] = {"level": None, "reason": f"[확인 필요: {S.CRITERIA_LABELS[name]} 근거 인용 대조 실패]", "evidence": []}
            run.warn(f"{card_id}: 다시 만든 뒤에도 근거 인용이 없거나 대조에 실패한 칸 선택을 받지 않았다({', '.join(S.CRITERIA_LABELS[name] for name in keys)}).")
        return {"cards": cards}
    summaries = data["summaries"]
    for failure in failures:
        match = re.match(r"summaries\[([^\]]*)\]", failure["path"])
        if match and match.group(1) in {row["card_id"] for row in run.state["summary_targets"]}:
            summaries[match.group(1)] = "[확인 필요: 추천 근거 요약 작성 실패]"
            run.warn(f"{match.group(1)}: 추천 근거 요약이 검사를 통과하지 못했다({failure['message']}).")
    return {"summaries": summaries}


def _compute(run) -> list[dict[str, Any]]:
    selections = run.state.get("calls", {}).get("select", {}).get("cards", {})
    rows = []
    for card in run.state["targets"]:
        card_selection = selections.get(card["card_id"], {})
        row = S.score_row(
            {"card_id": card["card_id"], "frequency_value": card["frequency_value"], "duration_value": card["fields"]["3"]["duration"]["value"]},
            card_selection,
        )
        row.update({"name": card["name"], "confirmed": card["confirmed"], "selections": card_selection, "card": card})
        rows.append(row)
    return S.sort_rows(rows)


def after_call(run, call: str, data: dict[str, Any]) -> None:
    if call != "select":
        return
    rows = _compute(run)
    run.state["rows"] = rows
    run.state["summary_targets"] = [row for row in rows if row["grade"] in ("즉시 착수", "검토 후 착수")][:3]
    if not run.state["summary_targets"]:
        run.state.setdefault("skip_calls", {})["summary"] = {"summaries": {}}


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------
def _number(value: float) -> str:
    return f"{value:g}" if value == int(value) else f"{value:.1f}"


def _reference(row: dict[str, Any]) -> str:
    frequency, duration, savings = row["annual_frequency"], row["duration_minutes"], row["annual_savings"]
    parts = [
        f"연간 {_number(frequency['low'])}~{_number(frequency['high'])}회" if frequency else "연간 횟수 [확인 필요]",
        f"1회 {_number(duration[0])}~{_number(duration[1])}분" if duration else "1회 소요 [확인 필요]",
        f"연간 절감 {_number(savings['low_hours'])}~{_number(savings['high_hours'])}시간" if savings else "연간 절감 [확인 필요]",
    ]
    return " / ".join(parts)


def _score_cell(value: int | None) -> str:
    return str(value) if value is not None else "[확인 필요]"


def _row_text(run, row: dict[str, Any]) -> str:
    scores = row["scores"]
    return (
        f"{row['card_id']} {row['name']} ({row['confirmed']}): 반복성 {_score_cell(scores['repeatability'])}, "
        f"절차 명확성 {_score_cell(scores['procedure_clarity'])}, 데이터 접근성 {_score_cell(scores['data_accessibility'])}, "
        f"오류 영향도 {_score_cell(scores['error_impact'])}, 총점 {row['total'] if row['total'] is not None else '-'}, "
        f"등급 제안 {row['grade']}, 참고 정보 {_reference(row)}. 근거: "
        + " ".join(K.render_evidence(row["selections"].get(key, {}).get("evidence", []), _prefix(run, row["card"])) for key in SELECTED)
    )


def _cell(text: Any) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def finish(run) -> Result:
    rows = run.state.get("rows") or _compute(run)
    summaries = run.state.get("calls", {}).get("summary", {}).get("summaries", {})
    table, evidence_blocks, followups = [], [], []
    for index, row in enumerate(rows, 1):
        scores = row["scores"]
        table.append(
            "| " + " | ".join(
                _cell(value)
                for value in (
                    index, row["card_id"], row["name"], row["confirmed"],
                    _score_cell(scores["repeatability"]), _score_cell(scores["procedure_clarity"]),
                    _score_cell(scores["data_accessibility"]), _score_cell(scores["error_impact"]),
                    row["total"] if row["total"] is not None else "-", row["grade"], "(비어 있음)",
                    _reference(row) + " (추정)", ", ".join(row["notes"]) or "-",
                )
            ) + " |"
        )
        card = row["card"]
        lines = [f"#### {row['card_id']} {row['name']}"]
        frequency_evidence = K.render_evidence(card["fields"]["3"]["frequency"]["evidence"], _prefix(run, card))
        lines.append(f"- 반복성 {_score_cell(scores['repeatability'])}: 빈도 '{card['frequency_value']}' {frequency_evidence}".rstrip())
        for key in SELECTED:
            selection = row["selections"].get(key, {})
            label = S.CRITERIA_LABELS[key]
            if selection.get("level") is None:
                lines.append(f"- {label}: {selection.get('reason') or f'[확인 필요: {label} 근거]'}")
            else:
                lines.append(f"- {label} {selection['level']}점: {K.render_evidence(selection['evidence'], _prefix(run, card))}")
        evidence_blocks.append("\n".join(lines))
        if row["grade"] == "평가 불가":
            followups.append(f"- {row['card_id']} {row['name']}: {', '.join(row['missing'])} 확인 필요")
    summary_lines = [f"#### {row['card_id']} {row['name']}\n{summaries.get(row['card_id'], '[확인 필요: 추천 근거 요약]')}" for row in run.state.get("summary_targets", [])]
    standardization = []
    for card in run.state["standardization"]:
        rec = card.get("recommendation")
        if rec:
            variable = K.with_evidence(rec["variable_part"]["text"], rec["variable_part"]["evidence"], _prefix(run, card))
            standardization.append(f"#### {card['card_id']} {card['name']}\n- 매번 달라지는 부분: {variable}\n- 표준화할 대상: {rec['target']}\n- 담당자에게 확인할 질문: {rec['question']}")
        else:
            standardization.append(f"#### {card['card_id']} {card['name']}\n- [확인 필요: 권고 작성]")
    markdown = K.fill_template(
        K.template_text(run, "scoring_table.md"),
        {
            "HEADER": "\n".join(K.header_lines(run, "업무별 '엔지니어 확정' 칸에 적는다")),
            "ROWS": "\n".join(table) or "| - | - | 채점 대상 없음 | - | - | - | - | - | - | - | - | - | - |",
            "EVIDENCE": "\n\n".join(evidence_blocks) or "- 없음",
            "SUMMARIES": "\n\n".join(summary_lines) or "- 즉시 착수나 검토 후 착수 업무가 없다.",
            "STANDARDIZATION": "\n\n".join(standardization) or "- 없음",
            "FOLLOWUP": "\n".join(followups) or "- 없음",
            "WARNINGS": K.warnings_block(run),
        },
    )
    data = {
        "interview_ids": run.header.get("인터뷰 식별자"),
        "rows": [
            {
                "card_id": row["card_id"], "name": row["name"], "confirmed": row["confirmed"], "scores": row["scores"],
                "total": row["total"], "grade": row["grade"], "notes": row["notes"], "selections": row["selections"],
                "annual_frequency": row["annual_frequency"], "duration_minutes": row["duration_minutes"],
                "annual_savings": row["annual_savings"], "card": row["card"], "summary": summaries.get(row["card_id"]),
            }
            for row in rows
        ],
        "standardization": [card["card_id"] for card in run.state["standardization"]],
        "excluded": run.state["excluded"],
    }
    return Result(markdown=markdown, data=data, stage=3)
