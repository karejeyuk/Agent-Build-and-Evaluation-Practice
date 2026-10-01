#!/usr/bin/env python3
"""가명 매핑표로 원본을 치환해 inputs/에 가명 처리본을 만들고, 남은 식별 패턴을 검사한다(규칙 3).

  python3 core/pseudonymize.py apply --input private/원본.txt --mapping private/mapping.json --output inputs/가명본.txt
  python3 core/pseudonymize.py check --input inputs/가명본.txt

매핑표 형식: {"replacements": {"원래 이름": "가명"}}. 매핑표와 원본은 private/에만 둔다.
오탐을 허용할 표현은 private/pii_allowlist.txt에 한 줄에 하나씩 적는다.
결과 메시지에는 식별 정보 원문을 넣지 않고 줄 번호와 종류만 적는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from validate import find_pii


def load_mapping(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("replacements", data) if isinstance(data, dict) else None
    if not isinstance(mapping, dict):
        raise ValueError("가명 매핑은 {\"replacements\": {\"원문\": \"가명\"}} 형식이어야 한다.")
    normalized = {}
    for original, pseudonym in mapping.items():
        original, pseudonym = str(original), str(pseudonym)
        if not original or not pseudonym or original == pseudonym:
            raise ValueError("가명 매핑의 원문과 치환값은 서로 다른, 비어 있지 않은 문자열이어야 한다.")
        normalized[original] = pseudonym
    return normalized


def load_allowlist(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}


def replace_identifiers(text: str, mapping: dict[str, str]) -> str:
    for original in sorted(mapping, key=len, reverse=True):
        text = text.replace(original, mapping[original])
    return text


def sanitize_text(text: str, mapping: dict[str, str] | None = None, allowlist: set[str] | None = None) -> tuple[str, list[str]]:
    """매핑을 적용하고 남은 식별 패턴을 '줄 번호: 종류' 목록으로 돌려준다."""
    mapping = mapping or {}
    sanitized = replace_identifiers(text, mapping)
    return sanitized, [finding.describe() for finding in find_pii(sanitized, allowlist, mapping)]


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
    parser = argparse.ArgumentParser(description="입력을 가명 처리하고 남은 식별 패턴을 검사한다.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply_parser = subparsers.add_parser("apply", help="매핑을 적용한 가명 처리본을 만든다")
    apply_parser.add_argument("--input", type=Path, required=True)
    apply_parser.add_argument("--mapping", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path, required=True)
    apply_parser.add_argument("--allowlist", type=Path, default=Path("private/pii_allowlist.txt"))
    check_parser = subparsers.add_parser("check", help="이미 가명 처리된 파일에 남은 패턴만 검사한다")
    check_parser.add_argument("--input", type=Path, required=True)
    check_parser.add_argument("--mapping", type=Path)
    check_parser.add_argument("--allowlist", type=Path, default=Path("private/pii_allowlist.txt"))
    args = parser.parse_args()

    allowlist = load_allowlist(args.allowlist)
    if args.command == "apply":
        if args.output.resolve().parts[-2:-1] == ("private",) or "private" in args.output.parts:
            raise SystemExit("가명 처리본은 private/ 밖(inputs/)에 저장한다.")
        sanitized, errors = sanitize_text(args.input.read_text(encoding="utf-8"), load_mapping(args.mapping), allowlist)
        if errors:
            print("전송 차단: 가명 처리 뒤에도 식별 패턴이 남았다. 매핑표를 보강한다. " + ", ".join(errors), file=sys.stderr)
            raise SystemExit(2)
        _atomic_write(args.output, sanitized)
        print(f"가명 처리 완료: {args.output}")
        return

    mapping = load_mapping(args.mapping) if args.mapping else {}
    errors = [finding.describe() for finding in find_pii(args.input.read_text(encoding="utf-8"), allowlist, mapping)]
    if errors:
        print("전송 차단: " + ", ".join(errors), file=sys.stderr)
        raise SystemExit(2)
    print("남은 식별 패턴 없음")


if __name__ == "__main__":
    main()
