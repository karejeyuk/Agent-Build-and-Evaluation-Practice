#!/usr/bin/env python3
"""통합 검증 계층: 인용 대조, 가명 처리 패턴, 문체, 상단 정보, 엔지니어 확정을 검사한다.

run.py와 각 skill의 pipeline.py가 이 모듈을 불러 쓴다. 검사 결과 메시지에는
식별 정보 원문을 넣지 않는다(메시지도 LLM이 읽기 때문이다).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 표기 약속
# ---------------------------------------------------------------------------
TAG_PATTERN = re.compile(r"\[(?:확인 필요|정보 부족|판정 보류)(?::[^\[\]]*)?\]|\[측정 불가\]")
UNCONFIRMED_TAG = re.compile(r"\[확인 필요(?::[^\[\]]*)?\]")


def normalize_quote(value: str) -> str:
    """공백과 문장부호(생략 부호 포함)를 지워 인용 대조용 문자열을 만든다."""
    return "".join(
        character
        for character in unicodedata.normalize("NFC", str(value))
        if not character.isspace() and not unicodedata.category(character).startswith(("P", "Z"))
    )


def only_unknown(value: Any) -> bool:
    """값이 비었거나 [확인 필요]·[정보 부족]·[측정 불가] 표기뿐인지 본다."""
    if value is None:
        return True
    if isinstance(value, (list, tuple)):
        return not value or all(only_unknown(item) for item in value)
    if isinstance(value, dict):
        if "value" in value:
            return only_unknown(value.get("value"))
        return not value or all(only_unknown(item) for item in value.values())
    return not normalize_quote(TAG_PATTERN.sub("", str(value)))


def unconfirmed_tags(value: str) -> list[str]:
    return UNCONFIRMED_TAG.findall(str(value))


# ---------------------------------------------------------------------------
# 원문 파싱
# ---------------------------------------------------------------------------
# 'Q7 담당자:'와 시트 번호를 괄호로 함께 적은 'Q7(시트 ③-2) 담당자:'를 모두 받는다.
Q_LINE = re.compile(r"^\s*(Q\d+)\s*(?:\([^)]*\))?\s*(면담자|담당자)\s*:\s?(.*)$")
M_LINE = re.compile(r"^\s*(M\d+)\s*:\s?(.*)$")
DOC_LINE = re.compile(r"^L(\d+):\s?(.*)$")
SPEAKERS = ("면담자", "담당자")


@dataclass
class Transcript:
    utterances: dict[str, dict[str, str]] = field(default_factory=dict)
    memos: dict[str, str] = field(default_factory=dict)
    stray_lines: list[int] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.utterances and not self.memos


def parse_transcript(text: str) -> Transcript:
    """Q번호 화자 줄과 M번호 메모 문단을 읽는다. 표지 없는 줄은 앞 줄에 잇는다."""
    result = Transcript()
    current: tuple[str, str, str] | None = None
    for number, line in enumerate(str(text).splitlines(), 1):
        q_match = Q_LINE.match(line)
        if q_match:
            q_number, speaker, body = q_match.groups()
            entry = result.utterances.setdefault(q_number, {"면담자": "", "담당자": ""})
            entry[speaker] = (entry[speaker] + "\n" + body).strip("\n") if entry[speaker] else body
            current = ("q", q_number, speaker)
            continue
        m_match = M_LINE.match(line)
        if m_match:
            m_number, body = m_match.groups()
            result.memos[m_number] = (result.memos.get(m_number, "") + "\n" + body).strip("\n")
            current = ("m", m_number, "")
            continue
        if not line.strip():
            continue
        if current is None:
            result.stray_lines.append(number)
        elif current[0] == "q":
            result.utterances[current[1]][current[2]] += "\n" + line
        else:
            result.memos[current[1]] += "\n" + line
    return result


def document_lines(documents: Any) -> dict[str, dict[int, str]]:
    """prepare 단계가 만든 번호 붙은 문서 목록을 {파일명: {줄 번호: 내용}}으로 바꾼다."""
    if isinstance(documents, dict):
        documents = documents.get("documents", [])
    result: dict[str, dict[int, str]] = {}
    for document in documents or []:
        if document.get("status") != "ready":
            continue
        lines: dict[int, str] = {}
        if isinstance(document.get("lines"), list):
            for index, text in enumerate(document["lines"], 1):
                lines[index] = str(text)
        else:
            for line in str(document.get("numbered_text", "")).splitlines():
                match = DOC_LINE.match(line)
                if match:
                    lines[int(match.group(1))] = match.group(2)
        result[str(document.get("filename", ""))] = lines
    return result


# ---------------------------------------------------------------------------
# 인용 대조 (3.5의 6))
# ---------------------------------------------------------------------------
@dataclass
class QuoteCheck:
    status: str  # ok | corrected | failed
    message: str = ""
    evidence: dict[str, Any] | None = None


def _spans_both(quote: str, interviewer: str, respondent: str) -> bool:
    combined = interviewer + respondent
    boundary = len(interviewer)
    start = combined.find(quote)
    while start != -1:
        if start < boundary < start + len(quote):
            return True
        start = combined.find(quote, start + 1)
    return False


def _locate_in_document(quote: str, lines: dict[int, str]) -> tuple[int, int] | None:
    """정규화한 인용이 들어 있는 가장 짧은 줄 범위를 찾는다."""
    numbers = sorted(lines)
    normalized = [normalize_quote(lines[number]) for number in numbers]
    for start_index in range(len(numbers)):
        joined = ""
        for end_index in range(start_index, len(numbers)):
            joined += normalized[end_index]
            if quote in joined:
                # 앞쪽 줄을 빼도 포함되면 더 짧은 범위가 뒤에서 잡힌다.
                if start_index + 1 <= end_index and quote in "".join(normalized[start_index + 1 : end_index + 1]):
                    break
                return numbers[start_index], numbers[end_index]
            if len(joined) > len(quote) + 2000:
                break
    return None


def check_quote(evidence: Any, transcript: Transcript | None = None, documents: Any = None) -> QuoteCheck:
    """인용 하나를 원문과 대조한다. 번호만 틀렸으면 고친 근거를 돌려준다."""
    if not isinstance(evidence, dict):
        return QuoteCheck("failed", "근거 항목은 객체여야 한다.")
    quote = normalize_quote(evidence.get("quote", ""))
    if not quote:
        return QuoteCheck("failed", "인용문이 비어 있다.")
    source_type = evidence.get("source_type")
    transcript = transcript or Transcript()

    if source_type == "utterance":
        q_number = str(evidence.get("q", ""))
        speaker = evidence.get("speaker", "담당자")
        if speaker not in ("담당자", "both"):
            return QuoteCheck("failed", f"{q_number}: 면담자 줄만 인용할 수 없다.")
        normalized = {
            number: {name: normalize_quote(entry.get(name, "")) for name in SPEAKERS}
            for number, entry in transcript.utterances.items()
        }

        def matches(number: str) -> str | None:
            entry = normalized.get(number)
            if not entry:
                return None
            if quote in entry["담당자"]:
                return "담당자"
            if _spans_both(quote, entry["면담자"], entry["담당자"]):
                return "both"
            return None

        found = matches(q_number)
        if found:
            fixed = {**evidence, "speaker": found}
            return QuoteCheck("ok", evidence=fixed)
        if any(quote in entry["면담자"] for entry in normalized.values()):
            return QuoteCheck("failed", f"{q_number}: 인용 전체가 면담자 줄 안에 있다.")
        for number in sorted(normalized, key=lambda value: int(value[1:])):
            found = matches(number)
            if found:
                fixed = {**evidence, "q": number, "speaker": found}
                return QuoteCheck("corrected", f"Q번호 자동 교정: {q_number or '(없음)'} → {number}", fixed)
        return QuoteCheck("failed", f"{q_number}: 인용이 원문에 없다.")

    if source_type == "memo":
        m_number = str(evidence.get("m", ""))
        normalized = {number: normalize_quote(text) for number, text in transcript.memos.items()}
        if quote in normalized.get(m_number, ""):
            return QuoteCheck("ok", evidence=dict(evidence))
        for number in sorted(normalized, key=lambda value: int(value[1:])):
            if quote in normalized[number]:
                fixed = {**evidence, "m": number}
                return QuoteCheck("corrected", f"M번호 자동 교정: {m_number or '(없음)'} → {number}", fixed)
        return QuoteCheck("failed", f"{m_number}: 인용이 메모 문단에 없다.")

    if source_type == "document":
        filename = str(evidence.get("filename", ""))
        lines = document_lines(documents).get(filename)
        if lines is None:
            return QuoteCheck("failed", f"{filename}: 번호가 붙은 입력 문서에 없다.")
        start, end = evidence.get("start_line"), evidence.get("end_line")
        if isinstance(start, int) and isinstance(end, int) and 1 <= start <= end:
            selected = "".join(normalize_quote(lines.get(number, "")) for number in range(start, end + 1))
            if quote in selected:
                return QuoteCheck("ok", evidence=dict(evidence))
        located = _locate_in_document(quote, lines)
        if located:
            fixed = {**evidence, "start_line": located[0], "end_line": located[1]}
            return QuoteCheck("corrected", f"{filename} 줄 범위 자동 교정: L{start}~L{end} → L{located[0]}~L{located[1]}", fixed)
        return QuoteCheck("failed", f"{filename}: 인용이 문서에 없다.")

    return QuoteCheck("failed", f"지원하지 않는 근거 종류다: {source_type}")


def format_evidence(evidence: dict[str, Any], interview_id: str = "") -> str:
    """표기 약속대로 근거를 적는다."""
    quote = str(evidence.get("quote", "")).strip()
    prefix = f"{interview_id} " if interview_id else ""
    if evidence.get("source_type") == "utterance":
        return f"(발화 근거: {prefix}{evidence.get('q', '')} '{quote}')"
    if evidence.get("source_type") == "memo":
        return f"(발화 근거: {prefix}{evidence.get('m', '')} '{quote}')"
    return f"(문서 근거: {evidence.get('filename', '')}, L{evidence.get('start_line')}~L{evidence.get('end_line')} '{quote}')"


# ---------------------------------------------------------------------------
# 가명 처리 검사 (규칙 3)
# ---------------------------------------------------------------------------
SURNAMES = (
    "황보 남궁 제갈 선우 독고 서문 사공 "
    "김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 류 유 전 홍 고 문 양 손 배 백 허 남 심 노 하 곽 성 "
    "차 주 우 구 민 진 나 지 엄 채 원 천 방 공 현 함 변 염 여 추 도 소 석 선 설 마 길 연 위 표 명 기 반 라 왕 "
    "금 옥 육 인 맹 모 탁 국 어 은 편 용 예 경 봉 부 복 태 목 형 계 피 두 감 빈 동 온 범 좌 팽 승 간 상 시 단 견 당"
).split()
TITLES = (
    "부회장 회장 부사장 사장 전무 상무 이사 본부장 센터장 지점장 공장장 파트장 실장 부장 차장 과장 대리 주임 "
    "사원 팀장 소장 반장 조장 선임 책임 수석 연구원 매니저 프로 위원 대표 원장 국장 계장 주무관 사무관"
).split()
_SURNAME_RE = "|".join(sorted(SURNAMES, key=len, reverse=True))
_TITLE_RE = "|".join(sorted(TITLES, key=len, reverse=True))
# 성씨와 이름 자리에 오지만 사람 이름이 아닌 흔한 낱말(오탐 방지).
NAME_STOPWORDS = {
    "우리", "저희", "이번", "지난", "한번", "현장", "전부", "모두", "해당", "담당", "직속", "소속", "신입", "정규",
    "경력", "계약", "기존", "신규", "전임", "후임", "선임", "수석", "책임", "상위", "하위", "동료", "조금", "진짜",
    "정말", "이제", "이미", "이건", "이거", "이게", "제가", "제일", "주로", "주간", "하루", "오늘", "오전", "오후",
    "지금", "정도", "구매", "인사", "총무", "영업", "경영", "기획", "품질", "생산", "설비", "물류", "자재", "회계",
    "재무", "채용", "고객", "공장", "본부", "지점", "부서", "상사", "사내", "전사", "안에", "위에", "남은", "나중",
    "가끔", "계속", "보고", "결재", "연구", "개발", "운영", "관리", "전체", "각자", "모든", "다른", "여러", "부서장",
    "팀원", "직원", "전원", "일반", "임시", "정식", "최종", "최근", "이전", "이후", "전에", "후에", "다시",
}
_NAME_FULL = re.compile(rf"(?<![가-힣])((?:{_SURNAME_RE})[가-힣]{{1,2}})\s?(?:{_TITLE_RE})(?:님)?")
_NAME_SHORT = re.compile(rf"(?<![가-힣])((?:{_SURNAME_RE}))(\s?)((?:{_TITLE_RE}))(님)?")
# 붙여 쓴 '성씨+직급' 가운데 사람이 아닌 낱말.
ATTACHED_STOPWORDS = {"부사장", "부회장", "조사원", "정사원", "주사원", "사대표", "공장장"}
# '제 팀장님', '현 팀장'처럼 소유·시점을 뜻하는 한 글자 낱말은 성씨로 보지 않는다.
SHORT_STOPWORDS = {"제", "내", "저", "현", "모"}

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_PATTERNS = (
    re.compile(r"(?<!\d)(?:\+?82[- .]?)?0\d{1,2}[- .)]?\d{3,4}[- .]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)1[5-9]\d{2}-\d{4}(?!\d)"),
)
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_KOREAN_NUMBER = r"[일이삼사오육칠팔구십백천]+"
MONEY_PATTERNS = (
    re.compile(rf"(?:{_NUMBER}|{_KOREAN_NUMBER})\s*(?:십|백|천)?\s*(?:만|억|조)\s*(?:{_NUMBER}\s*(?:천|백|만)?\s*)?원"),
    re.compile(rf"{_NUMBER}\s*원(?!인|문|본|칙|래|하|활|격|료|단|자재|청|형|점|리|소|산|룸|탁|두|목|만한|년)"),
    re.compile(rf"(?:₩|\$|USD|KRW)\s*{_NUMBER}|{_NUMBER}\s*(?:달러|USD|KRW|엔|유로)(?![가-힣])"),
    re.compile(r"(?:매출|금액|단가|예산|견적가|판매가|계약금|대금|매입|가격)\s*[^\n\d]{0,12}?(?:\d{1,3}(?:,\d{3})+|\d{4,})"),
)
COMPANY_MARKERS = re.compile(r"(?:㈜|\(주\)|주식회사)\s*[가-힣A-Za-z0-9&·]+|[가-힣A-Za-z0-9&·]{2,}\s*(?:㈜|\(주\))")
HANGUL_WORD = re.compile(r"[가-힣A-Za-z0-9&·]+")
PARTICLES = sorted(
    "에서는 으로는 에게서 에서 에게 으로 부터 까지 하고 이랑 처럼 보다 마다 은 는 이 가 을 를 의 에 와 과 도 만 로 랑 께".split(),
    key=len,
    reverse=True,
)
SAFE_COMPANY_WORDS = set(
    (
        "고객사 회사 경쟁사 협력사 본사 지사 자사 타사 당사 계열사 관계사 모회사 자회사 지주사 거래사 공급사 납품사 "
        "제조사 판매사 유통사 위탁사 수탁사 보험사 증권사 카드사 통신사 항공사 여행사 건설사 시공사 제약사 언론사 "
        "방송사 신문사 출판사 택배사 운송사 물류사 대행사 외주사 금융사 투자사 운용사 신탁사 개발사 소속사 벤더사 "
        "파트너사 컨설팅사 협력회사 시장조사"
    ).split()
)
SAFE_COMPANY_SUFFIXES = tuple(
    (
        "조사 검사 감사 심사 행사 복사 답사 실사 봉사 역사 묘사 반사 발사 투사 회사 박사 석사 학사 강사 교사 판사 "
        "형사 기사 의사 약사 간호사 변호사 회계사 세무사 노무사 설계사 요리사 미용사 사진사 장사 이사 인사 "
        "대사 신사 수사 매사 축사 기념사 연설사"
    ).split()
)
PSEUDONYM_COMPANY = re.compile(r"^[A-Z][0-9]*사$")


@dataclass
class PiiFinding:
    line: int
    kind: str

    def describe(self) -> str:
        return f"L{self.line}: {self.kind}"


def _strip_particle(word: str) -> str:
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) - len(particle) >= 2:
            return word[: -len(particle)]
    return word


def _company_hits(line: str) -> bool:
    if COMPANY_MARKERS.search(line):
        return True
    for word in HANGUL_WORD.findall(line):
        stem = _strip_particle(word)
        if not stem.endswith("사") or len(stem) < 3:
            continue
        if stem in SAFE_COMPANY_WORDS or PSEUDONYM_COMPANY.match(stem) or stem.endswith(SAFE_COMPANY_SUFFIXES):
            continue
        return True
    return False


def _name_hits(line: str) -> bool:
    for match in _NAME_FULL.finditer(line):
        if match.group(1) not in NAME_STOPWORDS:
            return True
    for match in _NAME_SHORT.finditer(line):
        surname, space, title, _ = match.groups()
        if surname in SHORT_STOPWORDS:
            continue
        if not space and (surname + title in ATTACHED_STOPWORDS or surname + title in TITLES):
            continue
        return True
    return False


def find_pii(text: str, allowlist: set[str] | None = None, mapping: dict[str, str] | None = None) -> list[PiiFinding]:
    """식별 정보 패턴이 남은 줄과 종류를 찾는다. 원문 조각은 돌려주지 않는다."""
    findings: list[PiiFinding] = []
    allowed = sorted({phrase for phrase in (allowlist or set()) if phrase}, key=len, reverse=True)
    originals = sorted({original for original in (mapping or {}) if original}, key=len, reverse=True)
    for number, raw in enumerate(str(text).splitlines(), 1):
        line = raw
        for phrase in allowed:
            line = line.replace(phrase, " " * len(phrase))
        kinds: list[str] = []
        if any(original in raw for original in originals):
            kinds.append("매핑표의 원래 이름")
        if EMAIL_PATTERN.search(line):
            kinds.append("메일 주소")
        if any(pattern.search(line) for pattern in PHONE_PATTERNS):
            kinds.append("전화번호")
        if any(pattern.search(line) for pattern in MONEY_PATTERNS):
            kinds.append("금액")
        if _name_hits(line):
            kinds.append("성씨와 직급·호칭")
        if _company_hits(line):
            kinds.append("회사명")
        findings.extend(PiiFinding(number, kind) for kind in kinds)
    return findings


# ---------------------------------------------------------------------------
# 문체 검사 (규칙 7)
# ---------------------------------------------------------------------------
_QUOTED = re.compile(r"'[^'\n]*'|‘[^’\n]*’|\"[^\"\n]*\"|“[^”\n]*”|\([^()\n]*\)")
_SENTENCE = re.compile(r"[^.?!\n]+[.?]")
POLITE_ENDING = re.compile(r"(?:요|니다|니까|세요|십시오|시오)$")


def style_errors(text: str, mode: str) -> list[str]:
    """마침표나 물음표로 끝나는 문장의 종결 어미가 지정 문체와 맞는지 본다.

    mode는 'polite'(존댓말) 또는 'plain'(한다체)이다. 인용과 괄호 안은 보지 않는다.
    """
    errors = []
    cleaned = _QUOTED.sub("", str(text))
    for sentence in _SENTENCE.findall(cleaned):
        body = re.sub(r"[\s\"'”’)\]]+$", "", sentence[:-1]).strip()
        if not body or not re.search(r"[가-힣]$", body):
            continue
        polite = bool(POLITE_ENDING.search(body))
        if mode == "polite" and not polite:
            errors.append(f"존댓말이 아닌 문장: …{body[-12:]}{sentence[-1]}")
        if mode == "plain" and polite:
            errors.append(f"한다체가 아닌 문장: …{body[-12:]}{sentence[-1]}")
    return errors


# ---------------------------------------------------------------------------
# 산출물 상단 정보와 엔지니어 확정 (규칙 2, 규칙 6)
# ---------------------------------------------------------------------------
HEADER_KEYS = ("출처", "인터뷰 식별자", "실행 버전", "엔지니어 확정")
EMPTY_CONFIRMATION = {"", "(비어 있음)", "비어 있음"}
CARD_TYPES = ("A형", "B1형", "B2형", "C형")
CARD_CONFIRMATIONS = (*CARD_TYPES, "다음 회차 확인")
GRADE_CONFIRMATIONS = ("즉시 착수", "검토 후 착수", "착수 보류", "다음 회차 확인")
START_GRADES = ("즉시 착수", "검토 후 착수")
REASON = re.compile(r"변경 사유\s*:\s*(.*\S)")


def header_values(markdown: str) -> dict[str, str]:
    """문서 맨 앞쪽의 네 줄 상단 정보를 읽는다(처음 나온 값만 쓴다)."""
    values: dict[str, str] = {}
    for line in markdown.splitlines()[:40]:
        for key in HEADER_KEYS:
            prefix = key + ":"
            if line.startswith(prefix) and key not in values:
                values[key] = line[len(prefix):].strip()
    return values


def provenance_errors(markdown: str) -> list[str]:
    """원본 연결 정보(출처, 인터뷰 식별자, 실행 버전)가 있는지 본다(규칙 6)."""
    values = header_values(markdown)
    errors = []
    for key in HEADER_KEYS[:3]:
        value = values.get(key, "")
        if not value or "출처 불명" in value:
            errors.append(f"출처 불명: 상단의 '{key}' 줄이 비어 있다.")
    return errors


def split_confirmation(value: str) -> tuple[str, str]:
    """'B1형 (변경 사유: …)' 같은 확정 칸을 값과 변경 사유로 나눈다."""
    text = str(value or "").strip()
    reason_match = REASON.search(text)
    reason = reason_match.group(1).strip() if reason_match else ""
    if reason_match:
        text = text[: reason_match.start()].strip()
    text = text.strip(" /,;()")
    if text in EMPTY_CONFIRMATION:
        text = ""
    return text, reason


def card_confirmation_error(card_id: str, proposal: str, value: str, reason: str) -> str | None:
    """2단계 카드 확정 값이 규칙 2에 맞는지 본다."""
    if not value:
        return f"{card_id}: 엔지니어 확정란이 비어 있다."
    if value not in CARD_CONFIRMATIONS:
        return f"{card_id}: 확정 값 '{value}'는 {', '.join(CARD_CONFIRMATIONS)} 가운데 하나여야 한다."
    same = value == proposal or (proposal == "판정 보류" and value == "다음 회차 확인")
    if not same and not reason:
        return f"{card_id}: 제안({proposal})과 다른 확정에는 '변경 사유:'가 필요하다."
    return None


def grade_confirmation_error(card_id: str, proposal: str, error_impact: int | None, value: str, reason: str) -> str | None:
    """3단계 등급 확정 값이 규칙 2와 상한 규칙에 맞는지 본다."""
    if not value:
        return f"{card_id}: 엔지니어 확정란이 비어 있다."
    if value not in GRADE_CONFIRMATIONS:
        return f"{card_id}: 확정 값 '{value}'는 {', '.join(GRADE_CONFIRMATIONS)} 가운데 하나여야 한다."
    if proposal == "평가 불가":
        if value not in ("다음 회차 확인", "착수 보류"):
            return f"{card_id}: 평가 불가 업무는 다음 회차 확인이나 착수 보류로만 확정할 수 있다."
        return None
    if value == "즉시 착수" and error_impact in (1, 2):
        return f"{card_id}: 오류 영향도 {error_impact}점 업무는 즉시 착수로 확정할 수 없다(상한 규칙)."
    if value != proposal and not reason:
        return f"{card_id}: 제안({proposal})과 다른 확정에는 '변경 사유:'가 필요하다."
    return None


def parse_card_confirmations(markdown: str) -> dict[str, tuple[str, str]]:
    """2단계 산출물에서 카드마다 엔지니어 확정 값과 변경 사유를 읽는다."""
    result: dict[str, tuple[str, str]] = {}
    blocks = re.split(r"(?m)^### 카드 번호:\s*", markdown)
    for block in blocks[1:]:
        lines = block.splitlines()
        card_id = lines[0].strip()
        value, reason = "", ""
        for line in lines[1:]:
            if line.startswith("### ") or line.startswith("## "):
                break
            if line.startswith("엔지니어 확정:"):
                value, inline_reason = split_confirmation(line.split(":", 1)[1])
                reason = reason or inline_reason
            elif line.startswith("변경 사유:"):
                reason = line.split(":", 1)[1].strip() or reason
        result[card_id] = (value, reason)
    return result


def parse_table(markdown: str, heading: str) -> list[dict[str, str]]:
    """지정 절의 첫 Markdown 표를 행 목록으로 읽는다."""
    section = markdown.split(heading, 1)
    if len(section) != 2:
        return []
    rows: list[list[str]] = []
    for line in section[1].splitlines():
        if line.startswith("## "):
            break
        if line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
                continue
            rows.append(cells)
        elif rows:
            break
    if not rows:
        return []
    head, *body = rows
    return [dict(zip(head, row)) for row in body]


# ---------------------------------------------------------------------------
# 1단계 산출물 검사 (5.4 템플릿 준수, 1단계 수량·반영)
# ---------------------------------------------------------------------------
def interview_output_errors(text: str, interview_format: str, expected_count: int, f_numbers: list[str], template_fixed: list[str]) -> list[str]:
    """맞춤 질문 수, 고정 문구, 확인 목록 F번호 반영을 검사한다."""
    errors: list[str] = []
    heading = "## 직무 맞춤 질문" if interview_format == "mail" else "## ③ 직무 맞춤 질문"
    if heading not in text:
        return ["직무 맞춤 질문 절이 없다."]
    body = text.split(heading, 1)[1]
    end_marker = "회신 기한" if interview_format == "mail" else "### 다음 회차 이월"
    custom = body.split(end_marker, 1)[0]
    count = len(re.findall(r"(?m)^\d+\.\s+\S", custom))
    if count != expected_count:
        errors.append(f"맞춤 질문 수가 다르다: 기대 {expected_count}, 실제 {count}.")
    for phrase in template_fixed:
        if phrase not in text:
            errors.append(f"고정 문구가 템플릿과 다르다: {phrase[:20]}…")
    for number in f_numbers:
        if not re.search(rf"\b{re.escape(number)}\b", text):
            errors.append(f"확인 목록 {number}이 질문, 이월 블록, 엔지니어용 부록 어디에도 없다.")
    if interview_format == "mail" and re.search(r"\bF\d+\b", text.split("## 엔지니어용 부록", 1)[0]):
        errors.append("메일 본문에 F번호가 들어 있다.")
    return errors


# ---------------------------------------------------------------------------
# 명령줄: 산출물·입력 파일을 사람이 직접 점검할 때 쓴다.
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="가명 처리 패턴과 원본 연결 정보를 점검한다.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pii_parser = subparsers.add_parser("pii", help="파일에 식별 정보 패턴이 남았는지 본다")
    pii_parser.add_argument("--input", type=Path, required=True)
    header_parser = subparsers.add_parser("header", help="산출물 상단의 원본 연결 정보를 본다")
    header_parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    errors = [finding.describe() for finding in find_pii(text)] if args.command == "pii" else provenance_errors(text)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(2)
    print("검사 통과")


if __name__ == "__main__":
    main()
