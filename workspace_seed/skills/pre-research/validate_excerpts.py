#!/usr/bin/env python3
"""사전 조사 요약서의 문서 인용이 지정된 원문 줄 범위에 있는지 확인한다."""

import argparse
import json
import re
import sys
from pathlib import Path

CITATION = re.compile(
    r"\(문서 근거:\s*([^,()]+),\s*L(\d+)~L(\d+)\s+'([^']*)'\)"
)


def validate_excerpts(documents: dict, report: str) -> list[str]:
    by_name = {
        document["filename"]: document
        for document in documents.get("documents", [])
        if document.get("status") == "ready"
    }
    errors: list[str] = []
    citations = list(CITATION.finditer(report))
    if "(문서 근거:" in report and not citations:
        errors.append("문서 근거 인용 형식을 읽을 수 없습니다.")

    for citation in citations:
        filename, start_text, end_text, quote = citation.groups()
        document = by_name.get(filename.strip())
        if document is None:
            errors.append(f"{filename}: 번호가 붙은 입력 문서에 없습니다.")
            continue
        lines = document.get("numbered_text", "").splitlines()
        start, end = int(start_text), int(end_text)
        if start < 1 or end < start or end > len(lines):
            errors.append(f"{filename}: L{start}~L{end} 범위가 올바르지 않습니다.")
            continue
        selected = "\n".join(line.split(":", 1)[1].lstrip() for line in lines[start - 1 : end])
        if not quote or quote not in selected:
            errors.append(f"{filename}: 인용이 L{start}~L{end}의 원문과 일치하지 않습니다.")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="사전 조사 인용을 입력 원문과 대조합니다.")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    errors = validate_excerpts(
        json.loads(args.documents.read_text(encoding="utf-8")),
        args.report.read_text(encoding="utf-8"),
    )
    if errors:
        for error in errors:
            print(f"오류: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("문서 근거 대조 통과")


if __name__ == "__main__":
    main()
