"""report(5단계) 파이프라인: 설계서 승인 검사, 절감 시간 계산, 리포트 초안 검사(수치 표기·실측 대조·피드백 인용·문체), 조립."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
from typing import Any

import savings as SV
import skillkit as K
import validate as V
from run import Check, Material, Result, RunError

CONFIRMATION_HINT = "수치를 확인한 뒤 상단 '엔지니어 확정' 줄에 `수치 확정`을 적는다."
CAUSES = ("프롬프트 문제", "데이터 문제", "절차 표준화 부족(B2형으로 되돌림)", "측정 방식 문제")
FEEDBACK = ("긍정", "개선", "추가 기대")
RESULTS = ("달성", "미달", "판단 불가")
PRIORITIES = ("높음", "중간", "낮음")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:\s*~\s*\d+(?:[.,]\d+)*)?")
TAG_AFTER = re.compile(r"^\s*[%가-힣/]{0,4}\s*\((?:실측|추정|목표)\)")
NOT_A_FIGURE = re.compile(r"^\s*(?:년|월|일|단계|회차|차|쪽|분기|순위|위)")
QUOTED = re.compile(r"'[^'\n]*'|‘[^’\n]*’|\"[^\"\n]*\"|“[^”\n]*”")


def add_arguments(parser) -> None:
    parser.add_argument("--design", required=True, help="엔지니어가 '승인'한 4단계 설계서(outputs/…_4단계_….md)")
    parser.add_argument("--measurements", required=True, help="PoC 실측 데이터(JSON {항목: 값} 또는 CSV 항목,값)")
    parser.add_argument("--feedback", help="가명 처리된 현업 피드백 원문(텍스트)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명")


def _parse_measurements(text: str, suffix: str) -> dict[str, str]:
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            data = data["metrics"]
        if isinstance(data, list):
            return {str(item.get("name")): str(item.get("value")) for item in data if isinstance(item, dict) and item.get("name")}
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items()}
        raise RunError("실측 JSON은 {항목: 값} 또는 {\"metrics\": [{\"name\", \"value\"}]} 형식이어야 한다.")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    header = [cell.strip() for cell in rows[0]]
    name_index = next((i for i, cell in enumerate(header) if cell in ("항목", "지표", "이름", "name")), 0)
    value_index = next((i for i, cell in enumerate(header) if cell in ("값", "실측", "실측값", "value")), 1)
    return {row[name_index].strip(): row[value_index].strip() for row in rows[1:] if len(row) > max(name_index, value_index) and row[name_index].strip()}


def check_input(run, target: str, card: str | None) -> None:
    record = run.load_output(target, "design-doc")
    run.require_header_confirmation(record, "승인")


def start(run) -> None:
    record = run.load_output(run.args["design"], "design-doc")
    run.require_header_confirmation(record, "승인")
    design = record.data
    if design.get("approval_missing"):
        run.warn("설계서에 필수 사람 승인 지점이 빠졌다는 경고가 남아 있다.")
    naming = dict(record.sidecar.get("naming", {}))
    if run.args.get("dept"):
        naming["dept"] = run.args["dept"]
    run.state["naming"] = naming
    run.header["인터뷰 식별자"] = design["card"]["interview_id"]

    path, text = run.read_input(run.args["measurements"])
    run.note_sources(path)
    try:
        measurements = _parse_measurements(text, path.suffix.lower())
    except (json.JSONDecodeError, csv.Error) as error:
        raise RunError(f"실측 데이터를 읽을 수 없다: {error}") from error
    feedback = ""
    if run.args.get("feedback"):
        feedback_path, feedback = run.read_input(run.args["feedback"])
        run.note_sources(feedback_path)
    rate_key = next((key for key in measurements if "개입률" in key or "intervention" in key.lower()), None)
    estimate = SV.estimate_savings(design.get("annual_frequency"), design.get("duration_minutes"), measurements.get(rate_key) if rate_key else None)
    if estimate.get("note"):
        run.warn(estimate["note"])
    section7 = design["sections"].get("7")
    success = section7 if isinstance(section7, str) else f"성공 정의: {section7.get('success_definition', '')}\n측정 방법: {section7.get('measurement', '')}"
    run.state.update(
        {
            "card_id": design["card_id"],
            "success": success,
            "measurements_text": text,
            "measurements": measurements,
            "feedback": feedback,
            "savings": estimate,
        }
    )


def _savings_text(estimate: dict[str, Any]) -> str:
    if estimate["status"] != "estimated":
        return f"[측정 불가] (없는 값: {', '.join(estimate.get('missing', []))})"
    rate = estimate["human_intervention_rate"]
    return f"연간 {estimate['low_hours']:.1f}~{estimate['high_hours']:.1f}시간 (추정, 사람 개입률 {rate:g} 반영)"


def materials(run, call: str) -> list[Material]:
    return [
        Material("설계서의 성공 정의와 측정 방법", run.state["success"]),
        Material("PoC 실측 데이터", run.state["measurements_text"]),
        Material("현업 피드백 원문(가명 처리본)", run.state["feedback"] or "(피드백 원문 없음)"),
        Material("savings.py가 계산한 절감 시간 추정치", _savings_text(run.state["savings"])),
    ]


# ---------------------------------------------------------------------------
# 검사 (5.4 5단계 수치 표기와 피드백 인용)
# ---------------------------------------------------------------------------
def _numbers(text: str) -> set[str]:
    return {number.replace(",", "") for number in re.findall(r"\d+(?:[.,]\d+)*", str(text))}


def untagged_numbers(text: str) -> list[str]:
    """피드백 인용 밖의 수치 가운데 (실측)·(추정)·(목표)가 바로 뒤에 붙지 않은 것을 찾는다."""
    cleaned = QUOTED.sub("", str(text))
    missing = []
    for match in NUMBER.finditer(cleaned):
        rest = cleaned[match.end():match.end() + 12]
        if NOT_A_FIGURE.match(rest) or TAG_AFTER.match(rest):
            continue
        missing.append(match.group(0))
    return missing


def validate(run, call: str, data: Any) -> Check:
    if not isinstance(data, dict):
        raise ValueError("최상위 값은 객체여야 한다.")
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    success_numbers = _numbers(run.state["success"])
    measurement_numbers = _numbers(run.state["measurements_text"])
    estimate = run.state["savings"]
    savings_numbers = _numbers(_savings_text(estimate)) if estimate["status"] == "estimated" else set()

    metrics = []
    for index, raw in enumerate(data.get("metrics") or []):
        raw = raw if isinstance(raw, dict) else {}
        path = f"metrics[{index}]"
        metric = {key: raw.get(key) for key in ("name", "target", "actual", "result", "source")}
        if not str(metric["name"] or "").strip():
            failures.append({"path": f"{path}.name", "message": "성공 기준 이름이 비어 있다."})
        target = str(metric["target"] or "").strip()
        if target and not V.only_unknown(target) and not _numbers(target) <= success_numbers:
            failures.append({"path": f"{path}.target", "message": "목표치가 설계서의 성공 정의에 없다."})
        actual = metric["actual"]
        if actual in ("", None) or V.only_unknown(actual):
            metric["actual"] = None
            if metric["result"] != "판단 불가":
                notes.append(f"{path}: 실측이 없어 결과를 '판단 불가'로 바꿨다.")
                metric["result"] = "판단 불가"
        elif not _numbers(actual) or not _numbers(actual) <= measurement_numbers:
            failures.append({"path": f"{path}.actual", "message": "실측치가 PoC 실측 데이터에 없다. 없는 값은 null로 둔다."})
        if metric["result"] not in RESULTS:
            failures.append({"path": f"{path}.result", "message": f"결과는 {', '.join(RESULTS)} 가운데 하나다."})
        metrics.append(metric)

    misses = []
    for index, raw in enumerate(data.get("misses") or []):
        raw = raw if isinstance(raw, dict) else {}
        item = {key: str(raw.get(key) or "").strip() for key in ("metric", "cause", "basis")}
        if item["cause"] not in CAUSES:
            failures.append({"path": f"misses[{index}].cause", "message": f"미달 원인은 {', '.join(CAUSES)} 가운데 하나다."})
        misses.append(item)

    feedback = []
    source = V.normalize_quote(run.state["feedback"])
    for index, raw in enumerate(data.get("feedback") or []):
        raw = raw if isinstance(raw, dict) else {}
        item = {"category": str(raw.get("category") or ""), "quote": str(raw.get("quote") or "").strip()}
        if item["category"] not in FEEDBACK:
            failures.append({"path": f"feedback[{index}].category", "message": f"피드백 분류는 {', '.join(FEEDBACK)} 가운데 하나다."})
        if not item["quote"] or V.normalize_quote(item["quote"]) not in source:
            failures.append({"path": f"feedback[{index}].quote", "message": "피드백 인용이 가명 처리된 피드백 원문에 없다."})
        feedback.append(item)

    recommendations = []
    for index, raw in enumerate(data.get("recommendations") or []):
        raw = raw if isinstance(raw, dict) else {}
        item = {key: str(raw.get(key) or "").strip() for key in ("priority", "action", "basis")}
        if item["priority"] not in PRIORITIES:
            failures.append({"path": f"recommendations[{index}].priority", "message": "우선순위는 높음, 중간, 낮음 가운데 하나다."})
        recommendations.append(item)

    summary = str(data.get("executive_summary") or "").strip()
    if not summary:
        failures.append({"path": "executive_summary", "message": "임원용 요약이 비어 있다."})
    for error in V.style_errors(summary, "polite"):
        failures.append({"path": "executive_summary", "message": f"임원용 요약은 존댓말로 쓴다. {error}"})
    untagged = untagged_numbers(summary)
    if untagged:
        failures.append({"path": "executive_summary", "message": f"표기 없는 수치가 있다: {', '.join(untagged[:5])}. 수치마다 (실측), (추정), (목표) 가운데 하나를 붙인다."})
    foreign = _numbers(QUOTED.sub("", summary)) - success_numbers - measurement_numbers - savings_numbers
    foreign = {number for number in foreign if not NOT_A_FIGURE.match(summary.split(number, 1)[-1][:4])}
    if foreign:
        failures.append({"path": "executive_summary", "message": f"입력에 없는 수치가 있다: {', '.join(sorted(foreign))}."})
    body = "\n".join([item["basis"] for item in misses] + [f"{item['action']} {item['basis']}" for item in recommendations])
    for error in V.style_errors(body, "plain"):
        failures.append({"path": "body", "message": f"리포트 본문은 한다체로 쓴다. {error}"})
    result = {"metrics": metrics, "misses": misses, "feedback": feedback, "recommendations": recommendations, "executive_summary": summary}
    return Check(result, failures, notes)


def merge_retry(run, call: str, previous: dict[str, Any], new: Any, failures: list[dict[str, str]]) -> dict[str, Any]:
    merged = copy.deepcopy(previous)
    if not isinstance(new, dict):
        return merged
    for failure in failures:
        match = re.match(r"(metrics|misses|feedback|recommendations)\[(\d+)\]", failure["path"])
        if match:
            key, index = match.group(1), int(match.group(2))
            items = new.get(key) or []
            if index < len(merged[key]) and index < len(items):
                merged[key][index] = items[index]
        elif failure["path"] == "executive_summary":
            merged["executive_summary"] = new.get("executive_summary", merged["executive_summary"])
        elif failure["path"] == "body":
            merged["misses"] = new.get("misses", merged["misses"])
            merged["recommendations"] = new.get("recommendations", merged["recommendations"])
    return merged


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    drop_feedback = set()
    for failure in failures:
        run.warn(f"다시 만든 뒤에도 통과하지 못했다: {failure['path']}: {failure['message']}")
        match = re.match(r"(metrics|misses|feedback|recommendations)\[(\d+)\]\.(\w+)", failure["path"])
        if not match:
            continue
        key, index, field = match.group(1), int(match.group(2)), match.group(3)
        if key == "metrics" and field == "actual":
            result["metrics"][index].update({"actual": None, "result": "판단 불가"})
        elif key == "metrics" and field == "target":
            result["metrics"][index]["target"] = "[확인 필요: 목표치]"
        elif key == "metrics" and field == "result":
            result["metrics"][index]["result"] = "판단 불가"
        elif key == "misses":
            result["misses"][index]["cause"] = "[확인 필요: 미달 원인]"
        elif key == "feedback":
            drop_feedback.add(index)
        elif key == "recommendations":
            result["recommendations"][index]["priority"] = "[확인 필요: 우선순위]"
    result["feedback"] = [item for index, item in enumerate(result["feedback"]) if index not in drop_feedback]
    return result


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------
def _tagged(value: Any, tag: str) -> str:
    text = str(value or "").strip()
    if not text or V.only_unknown(text):
        return text or "[확인 필요: 목표치]"
    return f"{text} {tag}"


def finish(run) -> Result:
    draft = run.state["calls"]["draft"]
    metrics = [
        f"| {item['name']} | {_tagged(item['target'], '(목표)')} | {_tagged(item['actual'], '(실측)') if item['actual'] is not None else '[측정 불가]'} | {item['result']} | {item.get('source') or '-'} |"
        for item in draft["metrics"]
    ]
    grouped = {category: [] for category in FEEDBACK}
    for item in draft["feedback"]:
        if item["category"] in grouped:
            grouped[item["category"]].append(f"- '{item['quote']}'")
    values = {
        "HEADER": "\n".join(K.header_lines(run)),
        "METRICS": "\n".join(metrics) or "| [확인 필요: 성공 기준] | [확인 필요: 목표치] | [측정 불가] | 판단 불가 | - |",
        "MISSES": "\n".join(f"- {item['metric']}: {item['cause']} (근거: {item['basis'] or '[확인 필요: 근거]'})" for item in draft["misses"]) or "- 미달 항목 없음",
        "POSITIVE": "\n".join(grouped["긍정"]) or "- 없음",
        "IMPROVEMENT": "\n".join(grouped["개선"]) or "- 없음",
        "EXPECTATION": "\n".join(grouped["추가 기대"]) or "- 없음",
        "RECOMMENDATIONS": "\n".join(f"- [{item['priority']}] {item['action']} (근거: {item['basis'] or '[확인 필요: 근거]'})" for item in draft["recommendations"]) or "- [확인 필요: 개선 권고안]",
        "SAVINGS": _savings_text(run.state["savings"]),
        "EXECUTIVE_SUMMARY": draft["executive_summary"] or "[확인 필요: 임원용 요약]",
        "WARNINGS": K.warnings_block(run),
    }
    markdown = K.fill_template(K.template_text(run, "report.md"), values)
    data = {"card_id": run.state["card_id"], "savings": run.state["savings"], **draft}
    return Result(markdown=markdown, data=data, stage=5, card_id=run.state["card_id"])
