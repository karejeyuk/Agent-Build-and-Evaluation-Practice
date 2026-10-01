#!/usr/bin/env python3
"""로컬 가명 매핑으로 LLM 입력을 치환하고, 잔여 식별 패턴이면 전송을 차단한다."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from validate import validate_pii


def load_mapping(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("replacements", data) if isinstance(data, dict) else None
    if not isinstance(mapping, dict):
        raise ValueError("가명 매핑은 {\"replacements\": {\"원문\": \"가명\"}} 형식이어야 합니다.")
    normalized = {}
    for original, pseudonym in mapping.items():
        original, pseudonym = str(original), str(pseudonym)
        if not original or not pseudonym or original == pseudonym:
            raise ValueError("가명 매핑의 원문과 치환값은 서로 다른 비어 있지 않은 문자열이어야 합니다.")
        normalized[original] = pseudonym
    return normalized


def replace_identifiers(text: str, mapping: dict[str, str]) -> str:
    for original in sorted(mapping, key=len, reverse=True):
        text = text.replace(original, mapping[original])
    return text


def sanitize_text(text: str, mapping: dict[str, str] | None = None) -> tuple[str, list[str]]:
    sanitized = replace_identifiers(text, mapping or {})
    return sanitized, validate_pii(sanitized)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pseudonymized-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="입력을 가명 처리하고 잔여 식별 패턴을 검사합니다.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply_parser = subparsers.add_parser("apply", help="매핑을 적용한 새 파일을 안전하게 만듭니다.")
    apply_parser.add_argument("--input", type=Path, required=True)
    apply_parser.add_argument("--mapping", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path, required=True)
    check_parser = subparsers.add_parser("check", help="이미 가명 처리된 파일의 잔여 패턴만 검사합니다.")
    check_parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "apply":
        sanitized, errors = sanitize_text(
            args.input.read_text(encoding="utf-8"), load_mapping(args.mapping)
        )
        if errors:
            print("전송 차단: " + " ".join(errors), file=sys.stderr)
            raise SystemExit(2)
        _atomic_write(args.output, sanitized)
        print(f"가명 처리 완료: {args.output}")
        return

    errors = validate_pii(args.input.read_text(encoding="utf-8"))
    if errors:
        print("전송 차단: " + " ".join(errors), file=sys.stderr)
        raise SystemExit(2)
    print("잔여 식별 패턴 없음")


if __name__ == "__main__":
    main()