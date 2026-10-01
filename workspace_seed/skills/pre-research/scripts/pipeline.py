"""pre-research(0단계) 파이프라인: 줄 번호 부여, 일반형 플래그, 발췌 대조, 요약서 조립."""

from __future__ import annotations

import json
from typing import Any

import skillkit as K
from run import Check, Material, Result, RunError

SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".log"}
MISSING_FIELDS = ("빈도", "담당자", "사용 시스템", "절차")
UNREADABLE = "[확인 필요: 텍스트 추출 실패, 원본 수동 확인]"
CONFIRMATION_HINT = "상단 '엔지니어 확정' 줄에 `검토 완료`를 적는다."


def add_arguments(parser) -> None:
    parser.add_argument("--interview-id", required=True, help="예: A사-영업-01-1회차")
    parser.add_argument("--document", action="append", default=[], help="부서 문서(텍스트). 여러 번 줄 수 있다")
    parser.add_argument("--profile", help="담당자 프로필 JSON(선택)")
    parser.add_argument("--dept", help="파일명에 쓸 부서명(예: 영업부)")


def start(run) -> None:
    profile = None
    if run.args.get("profile"):
        path, text = run.read_input(run.args["profile"])
        profile = json.loads(text)
        run.note_sources(path)
    K.naming_from_interview(run, run.args["interview_id"], run.args.get("dept"), profile)

    documents: list[dict[str, Any]] = []
    names = set()
    for value in run.args.get("document") or []:
        path = run.resolve(value)
        if path.name in names:
            raise RunError(f"파일명이 같은 문서가 둘 있다: {path.name}. 인용 출처를 가리기 위해 파일명을 다르게 한다.")
        names.add(path.name)
        run.note_sources(path)
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            documents.append({"filename": path.name, "status": "unsupported", "warning": UNREADABLE})
            continue
        try:
            _, text = run.read_input(value)
        except RunError:
            documents.append({"filename": path.name, "status": "unsupported", "warning": UNREADABLE})
            continue
        documents.append(K.number_document(path, text))
    run.state["documents"] = documents
    if not any(document["status"] == "ready" for document in documents):
        # 경로 B: LLM을 부르지 않고 코드가 일반형 플래그를 만든다.
        run.state["skip_llm"] = True
        if not run.manifest["sources"]:
            run.header["출처"] = "사전 자료 없음(입력 문서 없음)"


def materials(run, call: str) -> list[Material]:
    return [
        Material(f"{document['filename']} (줄 번호 부여)", document["numbered_text"])
        for document in run.state["documents"]
        if document["status"] == "ready"
    ]


def _missing_fields(value: Any, path: str, failures: list[dict[str, str]]) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.replace("/", ",").replace("·", ",").split(",")]
    if not isinstance(value, list):
        failures.append({"path": path, "message": "missing_fields는 목록이어야 한다."})
        return []
    fields = []
    for item in value:
        text = str(item).strip()
        text = "사용 시스템" if text.replace(" ", "") == "사용시스템" else text
        if not text:
            continue
        if text not in MISSING_FIELDS:
            failures.append({"path": path, "message": f"빠진 요소는 {', '.join(MISSING_FIELDS)} 가운데서만 고른다: {text}"})
            continue
        if text not in fields:
            fields.append(text)
    if not fields:
        failures.append({"path": path, "message": "빠진 요소가 없으면 사전 조사 확인 항목이 아니다."})
    return fields


def validate(run, call: str, data: Any) -> Check:
    if not isinstance(data, dict):
        raise ValueError("최상위 값은 객체여야 한다.")
    documents = {"documents": run.state["documents"]}
    failures: list[dict[str, str]] = []
    notes: list[str] = []
    result: dict[str, list[dict[str, Any]]] = {"candidates": [], "followup_items": [], "systems": []}
    for section in result:
        items = data.get(section, [])
        if not isinstance(items, list):
            failures.append({"path": section, "message": "목록이어야 한다."})
            continue
        for index, raw in enumerate(items):
            path = f"{section}[{index}]"
            if not isinstance(raw, dict):
                failures.append({"path": path, "message": "객체여야 한다."})
                result[section].append({})
                continue
            item = dict(raw)
            item["evidence"] = K.check_evidence_list(
                run, raw.get("evidence"), f"{path}.evidence", failures, notes, documents=documents, allowed_types=("document",)
            )
            if section == "candidates":
                if not str(raw.get("task", "")).strip():
                    failures.append({"path": f"{path}.task", "message": "업무명이 비어 있다."})
                excerpt = raw.get("procedure_excerpt")
                if excerpt:
                    checked = K.check_evidence_list(
                        run, [excerpt], f"{path}.procedure_excerpt", failures, notes, documents=documents, allowed_types=("document",)
                    )
                    item["procedure_excerpt"] = checked[0] if checked else None
            elif section == "followup_items":
                if not str(raw.get("content", "")).strip():
                    failures.append({"path": f"{path}.content", "message": "내용이 비어 있다."})
                item["missing_fields"] = _missing_fields(raw.get("missing_fields"), f"{path}.missing_fields", failures)
            else:
                if not str(raw.get("name", "")).strip():
                    failures.append({"path": f"{path}.name", "message": "시스템명이 비어 있다."})
                item["access"] = str(raw.get("access") or "[확인 필요: 데이터 얻는 방식]")
            result[section].append(item)
    return Check(result, failures, notes)


def merge_retry(run, call: str, previous: dict[str, Any], new: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    merged = {}
    for section in ("candidates", "followup_items", "systems"):
        indexes = {int(key[0]) for key in K.failed_keys(failures, rf"{section}\[(\d+)\]")}
        merged[section] = K.merge_list_items(previous.get(section, []), new.get(section, []) if isinstance(new, dict) else [], indexes)
    return merged


def degrade(run, call: str, data: dict[str, Any], failures: list[dict[str, str]]) -> dict[str, Any]:
    """다시 만든 뒤에도 근거 대조에 실패한 항목은 빼고 경고를 남긴다."""
    result = {}
    for section in ("candidates", "followup_items", "systems"):
        indexes = {int(key[0]) for key in K.failed_keys(failures, rf"{section}\[(\d+)\]")}
        kept = []
        for index, item in enumerate(data.get(section, [])):
            if index in indexes:
                label = item.get("task") or item.get("content") or item.get("name") or f"{section}[{index}]"
                run.warn(f"근거 대조 실패로 뺀 항목: {label}")
                continue
            kept.append(item)
        result[section] = kept
    return result


def _render(run, calls: dict[str, Any]) -> str:
    template = K.template_text(run, "pre_research.md")
    main, path_b = template.split("<!-- 경로 B: 사전 자료 없음 -->")
    header = "\n".join(K.header_lines(run))
    unreadable = [f"- {document['filename']}: {document['warning']}" for document in run.state["documents"] if document["status"] != "ready"]
    for document in run.state["documents"]:
        if document["status"] != "ready":
            run.warn(f"{document['filename']}: 텍스트로 읽을 수 없어 처리하지 않았다.")
    if run.state.get("skip_llm"):
        return K.fill_template(path_b.strip() + "\n", {"HEADER": header, "WARNINGS": K.warnings_block(run)})

    data = calls["extract"]
    candidates = []
    for item in data["candidates"]:
        line = f"- {K.with_evidence(item['task'], item['evidence'])}"
        excerpt = item.get("procedure_excerpt")
        line += f"\n  - 절차 발췌: {K.render_evidence([excerpt])}" if excerpt else "\n  - 절차: [확인 필요: 문서에 절차 서술 없음]"
        candidates.append(line)
    followups = [
        f"- {item['content']} (빠진 요소: {', '.join(item['missing_fields'])}) {K.render_evidence(item['evidence'])}".rstrip()
        for item in data["followup_items"]
    ]
    systems = [
        f"- {item['name']}: {item.get('data') or '[확인 필요: 데이터]'}, 데이터 얻는 방식: {item['access']} {K.render_evidence(item['evidence'])}".rstrip()
        for item in data["systems"]
    ]
    return K.fill_template(
        main.strip() + "\n",
        {
            "HEADER": header,
            "CANDIDATES": "\n".join(candidates) or "- 없음",
            "FOLLOWUP_ITEMS": "\n".join(followups) or "- 없음",
            "SYSTEMS": "\n".join(systems) or "- 없음",
            "UNREADABLE": "\n".join(unreadable) or "- 없음",
            "WARNINGS": K.warnings_block(run),
        },
    )


def finish(run) -> Result:
    calls = run.state.get("calls", {})
    markdown = _render(run, calls)
    extract = calls.get("extract", {"candidates": [], "followup_items": [], "systems": []})
    data = {
        "interview_id": run.header.get("인터뷰 식별자"),
        "no_documents": bool(run.state.get("skip_llm")),
        "documents": run.state["documents"],
        **extract,
    }
    return Result(markdown=markdown, data=data, stage=0)
