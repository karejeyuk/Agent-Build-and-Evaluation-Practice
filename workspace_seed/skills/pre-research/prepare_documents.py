#!/usr/bin/env python3
"""텍스트 입력에 줄 번호를 붙이거나 사전 자료 없음 플래그를 만든다."""

import argparse
import json
import sys
from pathlib import Path

SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".log"}


def number_document(path: Path) -> dict:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return {
            "filename": path.name,
            "status": "unsupported",
            "warning": "[확인 필요: 텍스트 추출 실패, 원본 수동 확인]",
        }
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    numbered = "\n".join(f"L{index}: {line}" for index, line in enumerate(lines, 1))
    return {"filename": path.name, "status": "ready", "numbered_text": numbered}


def prepare_documents(paths: list[Path]) -> dict:
    if not paths:
        return {
            "status": "no_pre_research",
            "message": "사전 자료 없음, 사전 조사 기반 질문 제외",
            "documents": [],
        }
    documents = [number_document(path) for path in paths]
    return {
        "status": "ready",
        "documents": documents,
        "warnings": [doc["warning"] for doc in documents if doc["status"] == "unsupported"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="텍스트 문서에 L번호를 붙입니다.")
    parser.add_argument("--document", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = prepare_documents(args.document)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"사전 조사 입력 준비 완료: {args.output}")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
