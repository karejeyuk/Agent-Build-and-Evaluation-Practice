#!/usr/bin/env python3
"""기획서의 키워드 사전으로 직무 유형과 필수 확인 주제를 결정한다."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

JOB_TYPES_PATH = Path(__file__).with_name("job_types.yaml")
PROFILE_FIELDS = (
    "부서",
    "직무명",
    "직급",
    "담당 업무",
    "연차",
    "department",
    "team",
    "job_title",
    "title",
    "responsibilities",
)


def load_job_types(path: Path = JOB_TYPES_PATH) -> dict[str, dict[str, Any]]:
    """YAML 직무 유형 사전을 읽고 기본 구조를 확인한다."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "fallback" not in data:
        raise ValueError("직무 유형 사전에는 fallback 유형이 필요합니다.")
    for type_id, definition in data.items():
        if not isinstance(definition, dict) or not isinstance(definition.get("label"), str):
            raise ValueError(f"{type_id}: label이 필요합니다.")
        if not isinstance(definition.get("keywords"), list):
            raise ValueError(f"{type_id}: keywords는 목록이어야 합니다.")
        if not isinstance(definition.get("required_topics"), list) or len(definition["required_topics"]) != 5:
            raise ValueError(f"{type_id}: required_topics는 정확히 5개여야 합니다.")
    return data


def _profile_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in PROFILE_FIELDS:
        value = profile.get(field, "")
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts).casefold()


def detect_job_type(
    profile: dict[str, Any], job_types: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """유형이 정확히 하나만 일치할 때 선택하고, 아니면 폴백한다."""
    if not isinstance(profile, dict):
        raise TypeError("담당자 프로필은 객체여야 합니다.")

    types = job_types or load_job_types()
    profile_text = _profile_text(profile)
    matches = [
        type_id
        for type_id, definition in types.items()
        if type_id != "fallback"
        and any(str(keyword).casefold() in profile_text for keyword in definition["keywords"])
    ]
    selected_id = matches[0] if len(matches) == 1 else "fallback"
    selected = types[selected_id]

    responsibilities = profile.get(
        "담당 업무", profile.get("담당업무", profile.get("responsibilities", ""))
    )
    if isinstance(responsibilities, list):
        responsibility_text = "、".join(str(item) for item in responsibilities) or "[확인 필요: 담당 업무]"
    else:
        responsibility_text = str(responsibilities).strip() or "[확인 필요: 담당 업무]"

    topics = [str(topic).replace("[담당 업무]", responsibility_text) for topic in selected["required_topics"]]
    return {
        "type_id": selected_id,
        "type_label": selected["label"],
        "matched_types": matches,
        "required_topics": topics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="담당자 프로필의 직무 유형을 판별합니다.")
    parser.add_argument("--profile", type=Path, help="프로필 JSON 경로. 생략하면 표준 입력을 읽습니다.")
    parser.add_argument("--job-types", type=Path, default=JOB_TYPES_PATH)
    args = parser.parse_args()

    profile_text = args.profile.read_text(encoding="utf-8") if args.profile else sys.stdin.read()
    result = detect_job_type(json.loads(profile_text), load_job_types(args.job_types))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
