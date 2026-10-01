"""판정 조건표에 판정 절차를 적용해 형을 정하고 분류·판단 개입 지점·실행 계기 표시를 정한다(기획서 3.5의 5)).

LLM은 조건표만 쓰고 형은 이 코드가 정한다. 입력 조건은 검증 계층을 거친 뒤의 값이다.
  conditions[n] = {"status": "충족"|"미충족", "reason": str, "evidence": [...], "citation_failed": bool}
"""

from __future__ import annotations

from typing import Any

SATISFIED, UNSATISFIED = "충족", "미충족"
NO_JUDGMENT = "판단 없음 확인"
NO_TRIGGER = "계기 없음"
UNKNOWN_DECISION = "[확인 필요: 판단이 필요한 경우가 있는지]"


def _status(conditions: dict[str, Any], number: int) -> str | None:
    value = (conditions.get(str(number)) or {}).get("status")
    return value if value in (SATISFIED, UNSATISFIED) else None


def classify_conditions(conditions: dict[str, Any], judgment_point: str = "") -> dict[str, Any]:
    """판정 절차 ⓪~③으로 형을 정한다."""
    statuses = {number: _status(conditions, number) for number in range(1, 7)}
    satisfied = {number for number, status in statuses.items() if status == SATISFIED}

    if any((conditions.get(str(number)) or {}).get("citation_failed") for number in range(1, 7)):
        proposal, reason = "판정 보류", "[판정 보류: 인용 대조 실패]"  # ⓪
    elif 1 in satisfied:
        proposal, reason = "C형", "조건 1 충족"  # ①
    else:  # ②
        matches = []
        if 2 in satisfied:
            matches.append("B2형")
        if {3, 4, 5} <= satisfied:
            matches.append("B1형")
        if {3, 6} <= satisfied and statuses[4] == UNSATISFIED:
            matches.append("A형")
        if len(matches) == 1:
            proposal, reason = matches[0], f"{matches[0]} 조건 충족"
        elif matches:
            proposal, reason = "판정 보류", f"[판정 보류: {'·'.join(matches)} 조건에 모두 걸림]"
        else:
            missing = [f"조건 {number}" for number in (2, 3, 5, 6) if number not in satisfied]
            proposal, reason = "판정 보류", f"[판정 보류: 걸리는 형 없음(미충족: {', '.join(missing)})]"

    condition_four = conditions.get("4") or {}
    decision_evidence: list[dict[str, Any]] = []
    if statuses[4] == SATISFIED:
        decision_point = str(judgment_point or "").strip() or "[확인 필요: 판단 개입 지점]"
    elif str(condition_four.get("reason", "")).startswith(NO_JUDGMENT) and condition_four.get("evidence"):
        decision_point = "없음"
        decision_evidence = list(condition_four["evidence"])
    else:
        decision_point = UNKNOWN_DECISION

    condition_six = conditions.get("6") or {}
    return {
        "proposal": proposal,
        "reason": reason,
        "decision_point": decision_point,
        "decision_evidence": decision_evidence,
        "trigger_missing": statuses[6] == UNSATISFIED and str(condition_six.get("reason", "")).startswith(NO_TRIGGER),
    }
