#!/usr/bin/env python3
"""기획서의 키워드 사전으로 직무 유형과 필수 확인 주제를 정한다(3.4의 2)).

담당자 프로필의 부서, 직무명, 담당 업무만 키워드와 대조한다. 정확히 한 유형에만 걸리면
그 유형을, 걸리지 않거나 두 유형 이상에 걸리면 폴백을 쓴다. 같은 입력에는 늘 같은 결과가 나온다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

JOB_TYPES_PATH = Path(__file__).resolve().parents[1] / "job_types.yaml"
PROFILE_FIELDS = ("부서", "직무명", "담당 업무", "담당업무", "department", "team", "job_title", "responsibilities")
# 키워드를 품고 있지만 다른 뜻인 낱말. 대조 전에 지운다.
NON_KEYWORD_WORDS = ("인사이트", "인사이드", "인사말", "인사드", "영업일", "영업시간", "영업외")


def load_job_types(path: Path = JOB_TYPES_PATH) -> dict[str, dict[str, Any]]:
    """YAML 직무 유형 사전을 읽고 기본 구조를 확인한다."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "fallback" not in data:
        raise ValueError("직무 유형 사전에는 fallback 유형이 필요하다.")
    for type_id, definition in data.items():
        if not isinstance(definition, dict) or not isinstance(definition.get("label"), str):
            raise ValueError(f"{type_id}: label이 필요하다.")
        if not isinstance(definition.get("keywords"), list):
            raise ValueError(f"{type_id}: keywords는 목록이어야 한다.")
        if not isinstance(definition.get("required_topics"), list) or len(definition["required_topics"]) != 5:
            raise ValueError(f"{type_id}: required_topics는 정확히 5개여야 한다.")
    return data


def _profile_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    for name in PROFILE_FIELDS:
        value = profile.get(name, "")
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    text = " ".join(parts)
    for word in NON_KEYWORD_WORDS:
        text = text.replace(word, " ")
    return text


def keyword_matches(keyword: str, text: str) -> bool:
    """한글 키워드는 낱말 안에서 찾고, 영문 키워드(CS 등)는 낱말 첫머리에서만 찾는다."""
    if re.fullmatch(r"[A-Za-z0-9]+", keyword):
        return any(
            token.casefold().startswith(keyword.casefold()) and not re.match(r"[A-Za-z]", token[len(keyword):])
            for token in re.findall(r"[A-Za-z0-9가-힣]+", text)
        )
    return keyword in text


def detect_job_type(profile: dict[str, Any], job_types: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """유형이 정확히 하나만 걸릴 때 고르고, 아니면 폴백한다."""
    if not isinstance(profile, dict):
        raise TypeError("담당자 프로필은 객체여야 한다.")
    types = job_types or load_job_types()
    text = _profile_text(profile)
    matches = [
        type_id
        for type_id, definition in types.items()
        if type_id != "fallback" and any(keyword_matches(str(keyword), text) for keyword in definition["keywords"])
    ]
    selected_id = matches[0] if len(matches) == 1 else "fallback"
    selected = types[selected_id]

    responsibilities = profile.get("담당 업무", profile.get("담당업무", profile.get("responsibilities", "")))
    if isinstance(responsibilities, list):
        responsibility_text = "와 ".join(str(item) for item in responsibilities if str(item).strip())
    else:
        responsibility_text = str(responsibilities).strip()
    responsibility_text = responsibility_text or "[확인 필요: 담당 업무]"
    topics = [str(topic).replace("[담당 업무]", responsibility_text) for topic in selected["required_topics"]]
    return {
        "type_id": selected_id,
        "type_label": selected["label"],
        "matched_types": matches,
        "required_topics": topics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="담당자 프로필의 직무 유형을 판별한다.")
    parser.add_argument("--profile", type=Path, help="프로필 JSON 경로. 생략하면 표준 입력을 읽는다.")
    parser.add_argument("--job-types", type=Path, default=JOB_TYPES_PATH)
    args = parser.parse_args()
    profile_text = args.profile.read_text(encoding="utf-8") if args.profile else sys.stdin.read()
    result = detect_job_type(json.loads(profile_text), load_job_types(args.job_types))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
