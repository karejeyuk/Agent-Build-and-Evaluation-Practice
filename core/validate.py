#!/usr/bin/env python3
"""파이프라인 산출물의 근거, 확정, 기밀, 형식을 검사한다."""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

Q_LINE = re.compile(r"^\s*(Q\d+)\s+(면담자|담당자)\s*:\s*(.*)$")
M_LINE = re.compile(r"^\s*(M\d+)\s*:\s*(.*)$")
DOC_LINE = re.compile(r"^L(\d+):\s?(.*)$")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?82[- .]?)?0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)")
MONEY_PATTERN = re.compile(r"(?<![\w[])\d{1,3}(?:,\d{3})+(?:원|만원|억원)?")
PERSON_TITLE_PATTERN = re.compile(r"[가-힣]{2,4}\s*(?:차장|부장|과장|대리|주임|사원|팀장|전무|이사)(?:님)?")
COMPANY_PATTERN = re.compile(r"(?:㈜|주식회사\s*)[가-힣A-Za-z0-9·_-]{2,}|[가-힣A-Za-z0-9_-]{2,}사")
SAFE_COMPANY_WORDS = {"고객사", "회사", "경쟁사", "시장조사", "검사", "보고서"}


def normalize_quote(value: str) -> str:
    """공백·문장부호를 제거해 원문 인용과 비교할 문자열을 만든다."""
    return "".join(
        character
        for character in value
        if not character.isspace() and not unicodedata.category(character).startswith(("P", "Z"))
    )


def parse_transcript(text: str) -> dict[str, Any]:
    """Q번호 화자 줄과 M번호 메모 문단을 읽는다."""
    utterances: dict[str, dict[str, str]] = {}
    memos: dict[str, str] = {}
    current_q: tuple[str, str] | None = None
    current_m: str | None = None
    for line in text.splitlines():
        q_match = Q_LINE.match(line)
        if q_match:
            q_number, speaker, body = q_match.groups()
            utterances.setdefault(q_number, {"면담자": "", "담당자": ""})[speaker] += body
            current_q = (q_number, speaker)
            current_m = None
            continue
        m_match = M_LINE.match(line)
        if m_match:
            memo_number, body = m_match.groups()
            memos[memo_number] = body
            current_m = memo_number
            current_q = None
            continue
        if current_q:
            utterances[current_q[0]][current_q[1]] += "\n" + line
        elif current_m:
            memos[current_m] += "\n" + line
    return {"utterances": utterances, "memos": memos}


def _document_lines(documents: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, dict[int, str]]:
    if isinstance(documents, dict):
        documents = documents.get("documents", [])
    result: dict[str, dict[int, str]] = {}
    for document in documents or []:
        if document.get("status") != "ready":
            continue
        filename = str(document.get("filename", ""))
        numbered_text = str(document.get("numbered_text", ""))
        lines: dict[int, str] = {}
        for line in numbered_text.splitlines():
            match = DOC_LINE.match(line)
            if match:
                lines[int(match.group(1))] = match.group(2)
        result[filename] = lines
    return result


def validate_evidence(
    evidence: dict[str, Any],
    transcript: dict[str, Any] | str | None = None,
    documents: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> list[str]:
    """발화·메모·문서 근거가 지정 원문에 실제로 포함되는지 확인한다."""
    if not isinstance(evidence, dict):
        return ["근거 항목은 객체여야 합니다."]
    source_type = evidence.get("source_type")
    quote = str(evidence.get("quote", ""))
    if not quote:
        return ["인용문이 비어 있습니다."]
    if isinstance(transcript, str):
        transcript = parse_transcript(transcript)
    transcript = transcript or {"utterances": {}, "memos": {}}
    errors: list[str] = []

    if source_type == "utterance":
        q_number = str(evidence.get("q", evidence.get("source_id", "")))
        utterance = transcript.get("utterances", {}).get(q_number)
        if not utterance:
            return [f"{q_number}: 원문에 Q번호가 없습니다."]
        speaker = evidence.get("speaker", "담당자")
        if speaker == "both":
            normalized_quote = normalize_quote(quote)
            interviewer = normalize_quote(utterance.get("면담자", ""))
            respondent = normalize_quote(utterance.get("담당자", ""))
            combined = normalize_quote(utterance.get("면담자", "") + utterance.get("담당자", ""))
            if normalized_quote not in combined or not any(part in normalized_quote for part in [interviewer, respondent] if part):
                errors.append(f"{q_number}: 질문과 답을 함께 인용했으나 원문과 일치하지 않습니다.")
            if interviewer and interviewer in normalized_quote:
                if not respondent or respondent not in normalized_quote:
                    errors.append(f"{q_number}: 긍정 답 인용은 담당자 답변에도 걸쳐야 합니다.")
            elif respondent and respondent in normalized_quote and interviewer not in normalized_quote:
                errors.append(f"{q_number}: 긍정 답 인용은 면담자 질문에도 걸쳐야 합니다.")
            return errors
        source_text = utterance.get(str(speaker), "")
        if normalize_quote(quote) not in normalize_quote(source_text):
            errors.append(f"{q_number} {speaker}: 인용이 원문에 없습니다.")
        return errors

    if source_type == "memo":
        memo_number = str(evidence.get("m", evidence.get("source_id", "")))
        memo = transcript.get("memos", {}).get(memo_number)
        if memo is None:
            return [f"{memo_number}: 원문에 메모 문단이 없습니다."]
        if normalize_quote(quote) not in normalize_quote(memo):
            errors.append(f"{memo_number}: 인용이 메모 문단에 없습니다.")
        return errors

    if source_type == "document":
        filename = str(evidence.get("filename", ""))
        start = evidence.get("start_line")
        end = evidence.get("end_line")
        lines = _document_lines(documents).get(filename)
        if lines is None:
            return [f"{filename}: 번호가 붙은 문서 입력이 없습니다."]
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            return [f"{filename}: 문서 줄 범위가 올바르지 않습니다."]
        if any(number not in lines for number in range(start, end + 1)):
            return [f"{filename}: L{start}~L{end} 범위가 입력 문서에 없습니다."]
        original = "\n".join(lines[number] for number in range(start, end + 1))
        if normalize_quote(quote) not in normalize_quote(original):
            errors.append(f"{filename} L{start}~L{end}: 인용이 지정 줄 범위에 없습니다.")
        return errors

    return [f"지원하지 않는 근거 종류입니다: {source_type}"]


def validate_pii(text: str, allowed_company_words: set[str] | None = None) -> list[str]:
    """외부 LLM 입력에서 흔한 식별정보 패턴을 찾아낸다. 자동 가림은 하지 않는다."""
    errors: list[str] = []
    if EMAIL_PATTERN.search(text):
        errors.append("메일 주소 패턴이 남아 있습니다.")
    if PHONE_PATTERN.search(text):
        errors.append("전화번호 패턴이 남아 있습니다.")
    if MONEY_PATTERN.search(text):
        errors.append("금액 패턴이 남아 있습니다.")
    if PERSON_TITLE_PATTERN.search(text):
        errors.append("실명과 직급·호칭 패턴이 남아 있습니다.")
    safe_words = SAFE_COMPANY_WORDS | (allowed_company_words or set())
    for match in COMPANY_PATTERN.finditer(text):
        if match.group(0) not in safe_words and not re.fullmatch(r"[A-Z]\d*사", match.group(0)):
            errors.append("회사명 패턴이 남아 있을 수 있습니다.")
            break
    return errors


def fill_unconfirmed_fields(card: dict[str, Any]) -> dict[str, Any]:
    """1~9번 필드에서 확인 필요를 모아 10번 항목을 작성한다."""
    result = dict(card)
    fields = result.get("fields", {})
    unconfirmed: list[str] = []
    for number in range(1, 10):
        value = fields.get(str(number), fields.get(number, ""))
        if isinstance(value, str) and "[확인 필요" in value:
            unconfirmed.append(value)
    result["field_10_unconfirmed"] = unconfirmed
    return result


def confirmation_error(stage: str, document: dict[str, Any]) -> str | None:
    """후속 단계 입력의 사람 확정란을 확인한다."""
    expected = {
        "task-card": ("reviewed", "검토 완료"),
        "fit-scoring": ("reviewed", "검토 완료"),
        "design-doc": ("confirmed", "승인"),
        "report": ("approved", "수치 확정"),
    }
    if stage not in expected:
        return None
    required_state, required_text = expected[stage]
    status = document.get("status")
    confirmation = document.get("engineer_confirmation", document.get("confirmation", ""))
    if status == required_state or confirmation == required_text:
        return None
    if stage == "fit-scoring":
        cards = document.get("cards", [])
        if cards and all(card.get("engineer_confirmation") in {"A형", "B1형", "B2형", "C형", "다음 회차 확인"} for card in cards):
            return None
    return f"{stage} 입력의 엔지니어 확정이 필요합니다."


def validate_interview_output(text: str, interview_format: str, duration: int | None = None) -> list[str]:
    """맞춤 질문 수와 필수 고정 문구를 빠르게 확인한다."""
    expected = 2 if interview_format == "mail" else (5 if duration == 30 else 10)
    section = text.split("## 직무 맞춤 질문", 1)
    if len(section) != 2:
        return ["직무 맞춤 질문 섹션이 없습니다."]
    body = section[1].split("회신 기한", 1)[0] if interview_format == "mail" else section[1].split("## ④ 심화 후속 질문", 1)[0]
    count = len(re.findall(r"(?m)^\d+\.\s+", body))
    errors = []
    if count != expected:
        errors.append(f"맞춤 질문 수가 달라야 합니다: 기대 {expected}, 실제 {count}.")
    common_questions = (
        "오늘 하루 업무를 시간 순서대로 말씀해 주시겠어요?",
        "지난 한 달 동안 정기적으로 반복한 작업이 있나요?",
        "그중 시간이 가장 많이 드는 작업 하나를 골라 절차를 설명해 주시겠어요?",
        "그 작업에 필요한 자료는 어떤 시스템이나 파일에서, 어떤 방식으로 가져오시나요?",
        "절차와 다르게 처리해야 할 때는 언제이고, 그때는 어떻게 처리하시나요?",
        "그 작업에서 실수가 생기면 어떤 일이 벌어지나요?",
        "이 작업을 신입에게 처음 가르칠 때 어떻게 알려 주시나요?",
        "'이건 꼭 사람이 해야 한다'고 느끼는 작업이 있나요?",
    )
    if interview_format == "interview" and any(question not in text for question in common_questions):
        errors.append("공통 질문 고정 문구가 템플릿과 다릅니다.")
    return errors


def validate_json_evidence(
    document: dict[str, Any],
    transcript_text: str | None = None,
    source_documents: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """중첩된 JSON 객체의 evidence 배열을 순회해 실패 경로를 반환한다."""
    failures: list[dict[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            evidence_items = value.get("evidence")
            if evidence_items is not None:
                if not isinstance(evidence_items, list):
                    failures.append({"path": path + ".evidence", "errors": ["evidence는 목록이어야 합니다."]})
                else:
                    for index, evidence in enumerate(evidence_items):
                        errors = validate_evidence(evidence, transcript_text, source_documents)
                        if errors:
                            failures.append({"path": f"{path}.evidence[{index}]", "errors": errors})
            for key, item in value.items():
                visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(document, "root")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="원문 인용·개인정보·인터뷰 산출물을 검증합니다.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("--input", type=Path, required=True)
    evidence_parser.add_argument("--transcript", type=Path)
    evidence_parser.add_argument("--documents", type=Path)
    pii_parser = subparsers.add_parser("pii")
    pii_parser.add_argument("--input", type=Path, required=True)
    pii_parser.add_argument("--allow-company-word", action="append", default=[])
    args = parser.parse_args()

    if args.command == "pii":
        errors = validate_pii(args.input.read_text(encoding="utf-8"), set(args.allow_company_word))
        if errors:
            print("\n".join(errors), file=sys.stderr)
            raise SystemExit(1)
        print("가명 처리 패턴 검사 통과")
        return

    report = json.loads(args.input.read_text(encoding="utf-8"))
    transcript = args.transcript.read_text(encoding="utf-8") if args.transcript else None
    documents = json.loads(args.documents.read_text(encoding="utf-8")) if args.documents else None
    failures = validate_json_evidence(report, transcript, documents)
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
    print("근거 인용 대조 통과")


if __name__ == "__main__":
    main()