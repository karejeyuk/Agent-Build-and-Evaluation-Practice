"""task-card(2단계) 파이프라인: 카드 추출 검사, 카드 번호, 조건표 검사, 형 판정, 10번 필드, 카드 조립."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import classify as C
import skillkit as K
import validate as V
from run import Check, Material, Result, RunError

CONFIRMATION_HINT = (
    "카드마다 '엔지니어 확정:' 줄에 A형, B1형, B2형, C형, 다음 회차 확인 가운데 하나를 적고, "
    "제안과 다르면 바로 아래에 '변경 사유:' 줄을 더한다."
)
FREQUENCY_FORMAT = re.compile(
    r"^(?:매일|격주|(?:주|월|분기|반기|연) \d+(?:~\d+)?회|사건 기반 월평균 (?:\d+(?:~\d+)?|\[확인 필요[^\]]*\])건)$"
)
DURATION_FORMAT = re.compile(r"^\d+(?:~\d+)?분$")
MULTI_FIELDS = {"4": "사용 시스템", "6": "예외 상황", "7": "실수 영향"}
FIELD_LABELS = {
    "1": "업무명", "2": "분류(제안)", "3": "빈도·소요 시간", "4": "사용 시스템", "5": "절차 요약",
    "6": "예외 상황", "7": "실수 영향", "8": "판단 개입 지점", "9": "근거 원문", "10": "미확인 항목",
}
TARGETS = ("절차", "양식", "판단 기준")
STATUS_TEXT = re.compile(r"^\s*(충족|미충족)\s*(?:\((.*)\))?\s*$")
REASON_FORMATS = (re.compile(r"^해당 발화 없음$"), re.compile(r"^관련 발화: [QM]\d+"), re.compile(r"^판단 없음 확인: [QM]\d+"), re.compile(r"^계기 없음: [QM]\d+"), re.compile(r"^인용 대조 실패$"))


def add_arguments(parser) -> None:
    parser.add_argument("--interview-id", required=True, help="예: A사-영업-01-1회차")
    parser.add_argument("--transcript", required=True, help="Q번호와 면담자·담당자 표지를 붙인 원문(또는 M번호 메모)")
    parser.add_argument("--pre-research", help="검토 완료된 0단계 산출물(outputs/…md)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명(예: 영업부)")
    parser.add_argument("--stt-review", action="store_true", help="STT 품질이 나빠 원문 대조가 필요할 때 카드에 'STT 검수 필요' 경고를 단다")


# ---------------------------------------------------------------------------
# 시작
# ---------------------------------------------------------------------------
def _existing_max_serial(run, prefix: str) -> int:
    serials = [0]
    for sidecar in (run.root / "outputs").glob("*.data.json"):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("skill") != "task-card":
            continue
        for card in data.get("data", {}).get("cards", []):
            card_id = str(card.get("card_id", ""))
            if card_id.startswith(prefix) and card_id[len(prefix):].isdigit():
                serials.append(int(card_id[len(prefix):]))
    return max(serials)


def start(run) -> None:
    naming = K.naming_from_interview(run, run.args["interview_id"], run.args.get("dept"))
    path, text = run.read_input(run.args["transcript"])
    run.note_sources(path)
    transcript = V.parse_transcript(text)
    if transcript.empty:
        raise RunError("원문에 'Q1 면담자:', 'Q1 담당자:' 같은 질문 번호·화자 표지나 'M1:' 메모 번호가 없다. 엔지니어가 표지를 붙인 원문을 넣는다.")
    if transcript.stray_lines:
        run.warn(f"표지 없는 줄이 앞쪽에 있다(줄 {', '.join(map(str, transcript.stray_lines[:10]))}). 인용 대조에서 빠진다.")
    record = K.load_pre_research(run, run.args.get("pre_research"))
    run.state["transcript"] = text
    run.state["documents"] = record.data.get("documents", []) if record else []
    run.state["pre_research"] = record.markdown if record else ""
    prefix = f"{naming['client']}-{naming['dept_short']}-{naming['person']}-"
    run.state["card_prefix"] = prefix
    run.state["serial_base"] = _existing_max_serial(run, prefix)
    if int(naming["round"]) >= 2:
        run.warn("2회차 이상 원문이다. v3 전까지는 같은 담당자의 기존 최대 일련번호 다음 번호부터 매기므로, 같은 업무면 엔지니어가 확정할 때 '직전 카드: 번호'를 적는다.")


def _transcript(run) -> V.Transcript:
    return V.parse_transcript(run.state["transcript"])


def _documents(run) -> dict[str, Any]:
    return {"documents": run.state["documents"]}


def _source_materials(run) -> list[Material]:
    materials = [Material("인터뷰 원문", run.state["transcript"])]
    if run.state["pre_research"]:
        materials.append(Material("0단계 사전 조사 요약서(검토 완료)", run.state["pre_research"]))
    return materials


def materials(run, call: str) -> list[Material]:
    if call == "extract":
        return _source_materials(run)
    cards = []
    for card_id, card in zip(run.state["card_ids"], run.state["calls"]["extract"]["cards"]):
        cards.append({"card_id": card_id, "extraction_basis": card["extraction_basis"], "fields": _render_fields_text(card)})
    return [Material("1차 호출이 만든 카드 목록", K.dumps(cards)), *_source_materials(run)]


# ---------------------------------------------------------------------------
# 1차 호출(카드 추출) 검사
# ---------------------------------------------------------------------------
def _value(run, raw: Any, path: str, label: str, failures, notes) -> dict[str, Any]:
    """값 객체 하나를 검사한다. 근거 없는 값은 [확인 필요]로 바꾸고(경고), 인용 실패는 다시 만들기로 보낸다."""
    if isinstance(raw, str):
        raw = {"value": raw, "evidence": []}
    if not isinstance(raw, dict):
        failures.append({"path": path, "message": '{"value": …, "evidence": […]} 객체여야 한다.'})
        return {"value": f"[확인 필요: {label}]", "evidence": []}
    value = str(raw.get("value") or "").strip() or f"[확인 필요: {label}]"
    if V.only_unknown(value):
        return {"value": value, "evidence": []}
    if not raw.get("evidence"):
        notes.append(f"{path}: 근거 없는 값을 [확인 필요]로 바꿨다(카드 필드 근거 검사).")
        run.count("근거 없는 필드 값")
        return {"value": f"[확인 필요: {label} 근거 없음]", "evidence": []}
    evidence = K.check_evidence_list(run, raw.get("evidence"), f"{path}.evidence", failures, notes, _transcript(run), _documents(run))
    return {"value": value, "evidence": evidence}


def _validate_card(run, card: Any, path: str, failures, notes) -> dict[str, Any]:
    if not isinstance(card, dict):
        failures.append({"path": path, "message": "카드는 객체여야 한다."})
        card = {}
    fields = card.get("fields") or {}
    basis = card.get("extraction_basis") or {}
    name = str(fields.get("1") or "").strip()
    if not name:
        failures.append({"path": f"{path}.fields.1", "message": "업무명이 비어 있다."})
        name = "[확인 필요: 업무명]"
    field3 = fields.get("3") if isinstance(fields.get("3"), dict) else {}
    frequency = _value(run, field3.get("frequency"), f"{path}.fields.3.frequency", "빈도", failures, notes)
    duration = _value(run, field3.get("duration"), f"{path}.fields.3.duration", "1회 소요 시간", failures, notes)
    if not V.only_unknown(frequency["value"]) and not FREQUENCY_FORMAT.match(frequency["value"]):
        notes.append(f"{path}.fields.3.frequency: 빈도 표기가 정해진 형식이 아니다('{frequency['value']}').")
    if not V.only_unknown(duration["value"]) and not DURATION_FORMAT.match(duration["value"]):
        notes.append(f"{path}.fields.3.duration: 소요 시간은 분 단위로 적는다('{duration['value']}').")
    result = {
        "extraction_basis": {key: str(basis.get(key) or "[확인 필요]") for key in ("trigger", "action", "object")},
        "fields": {"1": name, "3": {"frequency": frequency, "duration": duration}},
        "notes": str(card.get("notes") or "").strip(),
    }
    for key, label in MULTI_FIELDS.items():
        raw_values = fields.get(key)
        if raw_values in (None, "", []):
            raw_values = [f"[확인 필요: {label}]"]
        if not isinstance(raw_values, list):
            raw_values = [raw_values]
        result["fields"][key] = [_value(run, item, f"{path}.fields.{key}[{index}]", label, failures, notes) for index, item in enumerate(raw_values)]
    field5 = fields.get("5") if isinstance(fields.get("5"), dict) else {}
    procedure = str(field5.get("procedure") or "").strip() or "[확인 필요: 절차]"
    manual = _value(run, field5.get("manual"), f"{path}.fields.5.manual", "매뉴얼·양식 유무", failures, notes)
    result["fields"]["5"] = {"procedure": procedure, "manual": manual}
    result["fields"]["9"] = K.check_evidence_list(run, fields.get("9"), f"{path}.fields.9", failures, notes, _transcript(run), _documents(run))
    for text in [procedure, *[item["value"] for key in MULTI_FIELDS for item in result["fields"][key]]]:
        for error in V.style_errors(text, "plain"):
            notes.append(f"{path}: 문체 경고: {error}")
    return result


def _validate_extract(run, data: dict[str, Any]) -> Check:
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise ValueError('{"cards": [...]} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    cards = [_validate_card(run, card, f"cards[{index}]", failures, notes) for index, card in enumerate(data["cards"])]
    if not cards:
        notes.append("원문에서 카드를 하나도 뽑지 않았다.")
    candidates = []
    for index, candidate in enumerate(data.get("document_candidates") or []):
        path = f"document_candidates[{index}]"
        candidate = candidate if isinstance(candidate, dict) else {}
        evidence = K.check_evidence_list(run, candidate.get("evidence"), f"{path}.evidence", failures, notes, documents=_documents(run), allowed_types=("document",))
        candidates.append({"name": str(candidate.get("name") or "[확인 필요: 업무명]"), "evidence": evidence})
    return Check({"cards": cards, "document_candidates": candidates}, failures, notes)


# ---------------------------------------------------------------------------
# 2차 호출(판정 조건표) 검사
# ---------------------------------------------------------------------------
def _parse_condition(raw: Any) -> tuple[str | None, str, list[Any]]:
    if isinstance(raw, str):
        match = STATUS_TEXT.match(raw)
        return (match.group(1), (match.group(2) or "").strip(), []) if match else (None, "", [])
    if not isinstance(raw, dict):
        return None, "", []
    status = str(raw.get("status") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    match = STATUS_TEXT.match(status)
    if match:
        status = match.group(1)
        reason = reason or (match.group(2) or "").strip()
    return (status if status in (C.SATISFIED, C.UNSATISFIED) else None), reason, raw.get("evidence") or []


def _validate_conditions(run, data: dict[str, Any]) -> Check:
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise ValueError('{"cards": [...]} 형식이 아니다.')
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    known = run.state["card_ids"]
    result: dict[str, Any] = {}
    for raw_card in data["cards"]:
        raw_card = raw_card if isinstance(raw_card, dict) else {}
        card_id = str(raw_card.get("card_id") or "").strip()
        base = f"cards[{card_id}]"
        if card_id not in known:
            failures.append({"path": base, "message": f"입력에 없는 카드 번호다. 카드 번호는 {', '.join(known)} 가운데서만 쓴다."})
            continue
        if card_id in result:
            failures.append({"path": base, "message": "같은 카드 번호를 두 번 썼다."})
            continue
        conditions = {}
        raw_conditions = raw_card.get("conditions") or {}
        for number in range(1, 7):
            path = f"{base}.conditions.{number}"
            status, reason, evidence = _parse_condition(raw_conditions.get(str(number)))
            if status is None:
                failures.append({"path": path, "message": "조건은 '충족' 또는 '미충족'으로 적는다."})
                conditions[str(number)] = {"status": None, "reason": "", "evidence": []}
                continue
            if status == C.SATISFIED:
                checked = K.check_evidence_list(run, evidence, f"{path}.evidence", failures, notes, _transcript(run), _documents(run))
                conditions[str(number)] = {"status": status, "reason": "", "evidence": checked}
                continue
            if number == 4 and reason.startswith(C.NO_JUDGMENT):
                checked = K.check_evidence_list(run, evidence, f"{path}.evidence", failures, notes, _transcript(run), _documents(run))
            else:
                checked = []
            if not any(pattern.match(reason) for pattern in REASON_FORMATS):
                notes.append(f"{path}: 미충족 사유가 정해진 형식이 아니다('{reason or '비어 있음'}').")
                reason = reason or "해당 발화 없음"
            conditions[str(number)] = {"status": status, "reason": reason, "evidence": checked}

        judgment_point = str(raw_card.get("judgment_point") or "").strip()
        if conditions["4"]["status"] == C.SATISFIED and not judgment_point:
            failures.append({"path": f"{base}.judgment_point", "message": "조건 4를 충족했으면 판단 지점을 한 줄로 적는다."})
        recommendation = None
        if conditions["2"]["status"] == C.SATISFIED:
            raw_rec = raw_card.get("standardization_recommendation")
            raw_rec = raw_rec if isinstance(raw_rec, dict) else {}
            variable = raw_rec.get("variable_part") if isinstance(raw_rec.get("variable_part"), dict) else {"text": str(raw_rec.get("variable_part") or "")}
            rec_path = f"{base}.standardization_recommendation"
            recommendation = {
                "variable_part": {
                    "text": str(variable.get("text") or "").strip(),
                    "evidence": K.check_evidence_list(run, variable.get("evidence"), f"{rec_path}.variable_part.evidence", failures, notes, _transcript(run), _documents(run)),
                },
                "target": str(raw_rec.get("target") or "").strip(),
                "question": str(raw_rec.get("question") or "").strip(),
            }
            if not recommendation["variable_part"]["text"]:
                failures.append({"path": f"{rec_path}.variable_part", "message": "매번 달라지는 부분이 비어 있다."})
            if recommendation["target"] not in TARGETS:
                failures.append({"path": f"{rec_path}.target", "message": f"표준화할 대상은 {', '.join(TARGETS)} 가운데 하나다."})
            if not recommendation["question"]:
                failures.append({"path": f"{rec_path}.question", "message": "담당자에게 확인할 질문이 비어 있다."})
        result[card_id] = {"conditions": conditions, "judgment_point": judgment_point, "recommendation": recommendation}
    for card_id in known:
        if card_id not in result:
            failures.append({"path": f"cards[{card_id}]", "message": "이 카드의 조건표가 없다."})
    return Check({"cards": result}, failures, notes)


def validate(run, call: str, data: Any) -> Check:
    return _validate_extract(run, data) if call == "extract" else _validate_conditions(run, data)


# ---------------------------------------------------------------------------
# 다시 만들기 병합과 최종 실패 처리 (3.5의 6))
# ---------------------------------------------------------------------------
EXTRACT_PATH = re.compile(r"cards\[(\d+)\]\.fields\.(\d+)")
CONDITION_PATH = re.compile(r"cards\[([^\]]+)\]\.(conditions\.(\d)|judgment_point|standardization_recommendation)?")


def merge_retry(run, call: str, previous: dict[str, Any], new: Any, failures: list[dict[str, str]]) -> dict[str, Any]:
    merged = copy.deepcopy(previous)
    if not isinstance(new, dict):
        return merged
    if call == "extract":
        new_cards = new.get("cards") or []
        for failure in failures:
            match = EXTRACT_PATH.match(failure["path"])
            if match:
                index, key = int(match.group(1)), match.group(2)
                if index < len(merged["cards"]) and index < len(new_cards) and isinstance(new_cards[index], dict):
                    new_field = (new_cards[index].get("fields") or {}).get(key)
                    if new_field is not None:
                        merged["cards"][index]["fields"][key] = new_field
            elif failure["path"].startswith("document_candidates["):
                index = int(re.match(r"document_candidates\[(\d+)\]", failure["path"]).group(1))
                new_candidates = new.get("document_candidates") or []
                if index < len(merged["document_candidates"]) and index < len(new_candidates):
                    merged["document_candidates"][index] = new_candidates[index]
        return merged

    new_by_id = {str(card.get("card_id")): card for card in new.get("cards") or [] if isinstance(card, dict)}
    for failure in failures:
        match = CONDITION_PATH.match(failure["path"])
        if not match or match.group(1) not in new_by_id:
            continue
        card_id, part, number = match.group(1), match.group(2), match.group(3)
        new_card = new_by_id[card_id]
        if card_id not in merged["cards"]:
            merged["cards"][card_id] = new_card
            continue
        target = merged["cards"][card_id]
        if number:
            target["conditions"][number] = (new_card.get("conditions") or {}).get(number, target["conditions"][number])
        elif part == "judgment_point":
            target["judgment_point"] = new_card.get("judgment_point", "")
        elif part == "standardization_recommendation":
            target["recommendation"] = new_card.get("standardization_recommendation")
    # 병합 결과는 검사기가 다시 읽을 수 있는 출력 형식으로 돌려준다.
    return {
        "cards": [
            {
                "card_id": card_id,
                "conditions": card.get("conditions", {}),
                "judgment_point": card.get("judgment_point", ""),
                "standardization_recommendation": card.get("recommendation", card.get("standardization_recommendation")),
            }
            for card_id, card in merged["cards"].items()
        ]
    }


def _degrade_extract(run, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    for failure in failures:
        path = failure["path"]
        run.count("카드 필드 인용 최종 실패")
        if path.startswith("document_candidates["):
            index = int(re.match(r"document_candidates\[(\d+)\]", path).group(1))
            if index < len(result["document_candidates"]):
                run.warn(f"문서 근거 대조 실패로 문서에만 있는 후보에서 뺐다: {result['document_candidates'][index]['name']}")
                result["document_candidates"][index] = None
            continue
        match = re.match(r"cards\[(\d+)\]\.fields\.(\d+)(?:\.(frequency|duration|manual)|\[(\d+)\])?", path)
        if not match:
            continue
        card = result["cards"][int(match.group(1))]
        key, sub, index = match.group(2), match.group(3), match.group(4)
        label = FIELD_LABELS.get(key, "내용")
        run.warn(f"{path}: 다시 만든 뒤에도 인용 대조에 실패해 [확인 필요]로 바꿨다({failure['message']}).")
        if key == "3" and sub:
            card["fields"]["3"][sub] = {"value": f"[확인 필요: {'빈도' if sub == 'frequency' else '1회 소요 시간'} 인용 대조 실패]", "evidence": []}
        elif key == "5" and sub == "manual":
            card["fields"]["5"]["manual"] = {"value": "[확인 필요: 매뉴얼·양식 인용 대조 실패]", "evidence": []}
        elif key in MULTI_FIELDS and index is not None and int(index) < len(card["fields"][key]):
            card["fields"][key][int(index)] = {"value": f"[확인 필요: {label} 인용 대조 실패]", "evidence": []}
        elif key == "9":
            card["fields"]["9"] = [item for item in card["fields"]["9"] if item]
    result["document_candidates"] = [item for item in result["document_candidates"] if item]
    return result


def _degrade_conditions(run, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    cards = copy.deepcopy(data["cards"])
    for failure in failures:
        match = CONDITION_PATH.match(failure["path"])
        if not match:
            continue
        card_id, part, number = match.group(1), match.group(2), match.group(3)
        if card_id not in cards:
            if card_id in run.state["card_ids"]:
                run.warn(f"{card_id}: 조건표가 없어 판정 보류로 둔다.")
                cards[card_id] = {
                    "conditions": {str(n): {"status": C.UNSATISFIED, "reason": "해당 발화 없음", "evidence": []} for n in range(1, 7)},
                    "judgment_point": "",
                    "recommendation": None,
                    "missing_table": True,
                }
            continue
        card = cards[card_id]
        if number:
            condition = card["conditions"][number]
            if condition["status"] == C.SATISFIED:
                card["conditions"][number] = {"status": C.UNSATISFIED, "reason": "인용 대조 실패", "evidence": [], "citation_failed": True}
                run.warn(f"{card_id} 조건 {number}: 다시 만든 뒤에도 인용이 없거나 대조에 실패해 '미충족(인용 대조 실패)'으로 적었다(판정 절차 ⓪).")
                if number == "2":
                    card["recommendation"] = None
            elif condition["status"] == C.UNSATISFIED and str(condition.get("reason", "")).startswith(C.NO_JUDGMENT):
                card["conditions"][number] = {"status": C.UNSATISFIED, "reason": "해당 발화 없음", "evidence": []}
                run.warn(f"{card_id} 조건 4: '판단 없음 확인' 인용이 최종 실패해 '미충족(해당 발화 없음)'으로 바꿨다.")
            else:
                card["conditions"][number] = {"status": C.UNSATISFIED, "reason": "해당 발화 없음", "evidence": []}
                run.warn(f"{card_id} 조건 {number}: 충족·미충족을 읽을 수 없어 '미충족(해당 발화 없음)'으로 두었다.")
        elif part == "standardization_recommendation" and card.get("recommendation"):
            rec = card["recommendation"]
            if "variable_part" in failure["path"]:
                rec["variable_part"] = {"text": rec["variable_part"].get("text") or "[확인 필요: 매번 달라지는 부분]", "evidence": []}
                if failure["path"].endswith("]"):
                    rec["variable_part"]["text"] = "[확인 필요: 매번 달라지는 부분 인용 대조 실패]"
            if "target" in failure["path"]:
                rec["target"] = "[확인 필요: 표준화할 대상]"
            if "question" in failure["path"]:
                rec["question"] = "[확인 필요: 담당자에게 확인할 질문]"
            run.warn(f"{card_id}: 표준화 선행 권고 초안 일부를 [확인 필요]로 두었다.")
    return {"cards": cards}


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    return _degrade_extract(run, data, failures) if call == "extract" else _degrade_conditions(run, data, failures)


def after_call(run, call: str, data: dict[str, Any]) -> None:
    if call == "extract":
        # 5.8 공통 동작: 1차 호출이 카드를 내놓는 즉시 카드 번호를 매긴다.
        base = run.state["serial_base"]
        run.state["card_ids"] = [f"{run.state['card_prefix']}{base + index:02d}" for index in range(1, len(data["cards"]) + 1)]
        if not data["cards"]:
            run.state["calls"]["conditions"] = {"cards": {}}


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------
def _value_text(value: dict[str, Any], interview_id: str = "") -> str:
    return K.with_evidence(value["value"], value.get("evidence"), interview_id)


def _render_fields_text(card: dict[str, Any]) -> dict[str, str]:
    fields = card["fields"]
    return {
        "1": fields["1"],
        "3": f"빈도: {_value_text(fields['3']['frequency'])} | 1회 소요: {_value_text(fields['3']['duration'])}",
        "4": ", ".join(_value_text(item) for item in fields["4"]),
        "5": f"{fields['5']['procedure']} / 매뉴얼·양식: {_value_text(fields['5']['manual'])}",
        "6": ", ".join(_value_text(item) for item in fields["6"]),
        "7": ", ".join(_value_text(item) for item in fields["7"]),
        "9": K.render_evidence(fields["9"]) or "[확인 필요: 근거 원문]",
    }


def _build_card(run, card_id: str, card: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    decision = C.classify_conditions(table["conditions"], table.get("judgment_point", ""))
    if table.get("missing_table"):
        decision.update({"proposal": "판정 보류", "reason": "[판정 보류: 조건표 누락]"})
    fields = card["fields"]
    frequency = dict(fields["3"]["frequency"])
    if decision["trigger_missing"]:
        frequency["value"] = f"{frequency['value']} [확인 필요: 실행 계기]"
    rendered = _render_fields_text(card)
    rendered["3"] = f"빈도: {_value_text(frequency)} | 1회 소요: {_value_text(fields['3']['duration'])}"
    rendered["2"] = f"{decision['proposal'] if decision['proposal'] != '판정 보류' else decision['reason']} (판정 조건표 참조)"
    rendered["8"] = K.with_evidence(decision["decision_point"], decision["decision_evidence"])
    tags: list[str] = []
    for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        text = re.sub(r"\((?:발화|문서) 근거:[^)]*\)", "", rendered[key])
        for tag in V.unconfirmed_tags(text):
            if tag not in tags:
                tags.append(tag)
    rendered["10"] = " ".join(tags) or "없음"
    only_unknown = sum(
        [
            V.only_unknown(fields["1"]),
            V.only_unknown(fields["3"]["frequency"]["value"]) and V.only_unknown(fields["3"]["duration"]["value"]),
            all(V.only_unknown(item["value"]) for item in fields["4"]),
            V.only_unknown(fields["5"]["procedure"]) and V.only_unknown(fields["5"]["manual"]["value"]),
            all(V.only_unknown(item["value"]) for item in fields["6"]),
            all(V.only_unknown(item["value"]) for item in fields["7"]),
            V.only_unknown(decision["decision_point"]),
            not fields["9"],
        ]
    )
    return {
        "card_id": card_id,
        "interview_id": run.header.get("인터뷰 식별자"),
        "source": run.header.get("출처"),
        "name": fields["1"],
        "extraction_basis": card["extraction_basis"],
        "fields": fields,
        "frequency_value": frequency["value"],
        "rendered": rendered,
        "notes": card.get("notes", ""),
        "proposal": decision["proposal"],
        "proposal_reason": decision["reason"],
        "conditions": table["conditions"],
        "judgment_point": table.get("judgment_point", ""),
        "decision_point": decision["decision_point"],
        "recommendation": table.get("recommendation") if decision["proposal"] == "B2형" or table["conditions"]["2"]["status"] == C.SATISFIED else None,
        "next_round_recommended": only_unknown >= 3,
    }


def _card_markdown(run, card: dict[str, Any]) -> str:
    basis = card["extraction_basis"]
    lines = [
        f"### 카드 번호: {card['card_id']}",
        f"추출 근거: 주기·사건 {basis['trigger']} / 동작 {basis['action']} / 대상 {basis['object']}",
        *K.header_lines(run),
    ]
    if run.args.get("stt_review"):
        lines.append("경고: STT 검수 필요(고유명사 등 원문 품질 문제로 엔지니어가 원문과 직접 대조한다)")
    lines += [f"{key} {FIELD_LABELS[key]}: {card['rendered'][key]}" for key in FIELD_LABELS]
    if card["notes"]:
        lines.append(f"기타·특이사항: {card['notes']}")
    if card["next_round_recommended"]:
        lines.append("다음 회차 확인 권고: 기본 필드 1~9 가운데 3개 이상이 [확인 필요]뿐이다.")
    return "\n".join(lines)


def _condition_text(number: str, condition: dict[str, Any]) -> str:
    if condition["status"] == C.SATISFIED:
        return f"조건 {number} 충족 {K.render_evidence(condition['evidence'])}".rstrip()
    evidence = f" {K.render_evidence(condition['evidence'])}" if condition.get("evidence") else ""
    return f"조건 {number} 미충족({condition.get('reason') or '해당 발화 없음'}){evidence}"


def finish(run) -> Result:
    extract = run.state["calls"]["extract"]
    tables = run.state["calls"]["conditions"]["cards"]
    cards = [_build_card(run, card_id, card, tables[card_id]) for card_id, card in zip(run.state["card_ids"], extract["cards"])]

    conditions_md = []
    for card in cards:
        parts = [_condition_text(number, card["conditions"][number]) for number in map(str, range(1, 7))]
        conditions_md.append(f"#### {card['card_id']}\n" + " / ".join(parts))
    recommendations = []
    for card in cards:
        rec = card["recommendation"]
        if not rec:
            continue
        variable = K.with_evidence(rec["variable_part"]["text"], rec["variable_part"]["evidence"])
        recommendations.append(
            f"#### {card['card_id']} {card['name']}\n- 매번 달라지는 부분: {variable}\n- 표준화할 대상: {rec['target']}\n- 담당자에게 확인할 질문: {rec['question']}"
        )
    c_records = [
        f"- {card['card_id']} {card['name']}: {_condition_text('1', card['conditions']['1'])}" for card in cards if card["proposal"] == "C형"
    ]
    candidates = [f"- {K.with_evidence(item['name'], item['evidence'])}" for item in extract["document_candidates"]]
    markdown = K.fill_template(
        K.template_text(run, "task_card.md"),
        {
            "HEADER": "\n".join(K.header_lines(run, "카드별 '엔지니어 확정' 줄에 적는다")),
            "CARDS": "\n\n".join(_card_markdown(run, card) for card in cards) or "- 카드 없음",
            "CONDITIONS": "\n\n".join(conditions_md) or "- 없음",
            "DOCUMENT_CANDIDATES": "\n".join(candidates) or "- 없음",
            "RECOMMENDATIONS": "\n\n".join(recommendations) or "- 없음",
            "C_RECORDS": "\n".join(c_records) or "- 없음",
            "WARNINGS": K.warnings_block(run),
        },
    )
    data = {
        "interview_id": run.header.get("인터뷰 식별자"),
        "documents": run.state["documents"],
        "cards": cards,
        "document_candidates": extract["document_candidates"],
        "stt_review": bool(run.args.get("stt_review")),
    }
    return Result(markdown=markdown, data=data, stage=2)
