#!/usr/bin/env python3
"""AI 전환 업무 지원 파이프라인 런타임(기획서 5.8의 core/run.py).

채팅 에이전트는 skill을 이 명령으로만 실행한다. LLM 호출은 에이전트가 맡고,
그 앞뒤의 공통 동작(엔지니어 확정 검사, 가명 처리 검사, 인용 대조와 다시 만들기,
상단 정보 기록, 저장)은 이 코드가 강제한다.

  python3 core/run.py start <skill> [옵션]   입력을 검사하고 첫 LLM 호출 패키지를 만든다
  python3 core/run.py submit <run_id>        LLM 결과 JSON을 검사하고 다음 단계로 넘긴다
  python3 core/run.py status [run_id]        진행 상태를 보여 준다
  python3 core/run.py check <skill> <산출물.md> [--card 카드 번호]
                                             산출물이 그 skill의 입력으로 확정됐는지 본다

종료 코드: 0 진행·완료, 1 오류, 2 차단(엔지니어 확인 필요), 3 다시 만들기 요청
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
# 스크립트로 실행해도 skill 파이프라인의 `import run`이 같은 모듈(같은 예외 클래스)을 받게 한다.
sys.modules.setdefault("run", sys.modules[__name__])

import validate as V  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - 프로젝트 가상환경에는 PyYAML이 있다.
    yaml = None

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_RETRY = 0, 1, 2, 3
SKILLS = ("pre-research", "interview-sheet", "task-card", "fit-scoring", "design-doc", "report")
INTERVIEW_ID = re.compile(r"^(?P<client>[^-\s]+)-(?P<dept>[^-\s]+)-(?P<person>\d+|공통)-(?P<round>\d+)회차$")


class Blocked(Exception):
    """엔지니어 확인이 필요해 단계를 멈춘다(확정 누락, 가명 처리 검사 실패 등)."""


class RunError(Exception):
    """입력이나 사용법이 잘못됐다."""


def workspace_root() -> Path:
    override = os.getenv("AGENT_WORKSPACE")
    return Path(override).resolve() if override else CORE_DIR.parent


# ---------------------------------------------------------------------------
# 머리말과 버전
# ---------------------------------------------------------------------------
def read_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    _, head, body = text.split("---", 2)
    if yaml is None:
        raise RunError("PyYAML이 없어 머리말을 읽을 수 없다. 프로젝트 가상환경의 python3로 실행한다.")
    meta = yaml.safe_load(head) or {}
    return (meta if isinstance(meta, dict) else {}), body.lstrip("\n")


def markdown_section(body: str, heading: str) -> str:
    """'# 작업 지침' 같은 1단계 제목 아래 본문을 돌려준다."""
    match = re.search(rf"(?m)^#\s+{re.escape(heading)}\s*$", body)
    if not match:
        return ""
    rest = body[match.end():]
    end = re.search(r"(?m)^#\s+\S", rest)
    return (rest[: end.start()] if end else rest).strip()


def code_commit(root: Path) -> str:
    env_commit = os.getenv("AGENT_CODE_COMMIT")
    if env_commit:
        return env_commit
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "[확인 필요: 코드 커밋]"


def model_description() -> tuple[str, str]:
    return (
        os.getenv("AGENT_MODEL_ID", "[확인 필요: 모델 ID]"),
        os.getenv("AGENT_MODEL_SETTINGS", "[확인 필요: 모델 설정]"),
    )


# ---------------------------------------------------------------------------
# 실행 상태
# ---------------------------------------------------------------------------
@dataclass
class Material:
    """LLM 호출 패키지에 들어가는 입력 자료 하나."""

    title: str
    text: str
    virtual: bool = False


@dataclass
class Check:
    """LLM 출력 검사 결과. failures는 다시 만들기 대상, notes는 경고로만 남긴다."""

    data: Any
    failures: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Result:
    markdown: str
    data: dict[str, Any]
    stage: int
    card_id: str = ""


@dataclass
class OutputRecord:
    path: Path
    markdown: str
    sidecar: dict[str, Any]

    @property
    def data(self) -> dict[str, Any]:
        return self.sidecar.get("data", {})

    @property
    def virtual(self) -> bool:
        return bool(self.sidecar.get("virtual"))


class Run:
    def __init__(self, root: Path, run_id: str, manifest: dict[str, Any]):
        self.root = root
        self.run_id = run_id
        self.manifest = manifest

    # --- 경로 ---
    @property
    def dir(self) -> Path:
        return self.root / "work" / "runs" / self.run_id

    @property
    def skill(self) -> str:
        return self.manifest["skill"]

    @property
    def skill_dir(self) -> Path:
        return self.root / "skills" / self.skill

    @property
    def args(self) -> dict[str, Any]:
        return self.manifest["args"]

    @property
    def state(self) -> dict[str, Any]:
        return self.manifest["state"]

    @property
    def header(self) -> dict[str, str]:
        return self.manifest["header"]

    @property
    def virtual(self) -> bool:
        return bool(self.manifest.get("virtual", True))

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def resolve(self, value: str) -> Path:
        """에이전트가 넘긴 경로('/inputs/a.txt', 'inputs/a.txt')를 작업 공간 안의 실제 경로로 바꾼다."""
        path = (self.root / str(value).lstrip("/")).resolve()
        try:
            parts = path.relative_to(self.root).parts
        except ValueError as error:
            raise RunError(f"작업 공간 밖의 경로는 쓸 수 없다: {value}") from error
        if parts and parts[0] == "private":
            raise Blocked(f"private/ 아래 파일은 LLM 입력으로 쓸 수 없다: {value}. 가명 처리본(inputs/)을 지정한다.")
        if not path.exists():
            raise RunError(f"파일이 없다: {value}")
        return path

    def is_virtual_path(self, path: Path) -> bool:
        parts = path.resolve().relative_to(self.root).parts
        return bool(parts) and parts[0] == "tests"

    def read_input(self, value: str) -> tuple[Path, str]:
        """LLM에 들어갈 원본 입력 파일을 읽고, 가상 입력 여부를 실행 상태에 반영한다."""
        path = self.resolve(value)
        if not self.is_virtual_path(path):
            self.manifest["virtual"] = False
        return path, read_text_any(path)

    def note_sources(self, *paths: Path) -> None:
        sources = self.manifest.setdefault("sources", [])
        for path in paths:
            rel = self.rel(path)
            if rel not in sources:
                sources.append(rel)

    def warn(self, message: str) -> None:
        if message not in self.manifest["warnings"]:
            self.manifest["warnings"].append(message)

    def count(self, key: str, amount: int = 1) -> None:
        counters = self.manifest.setdefault("counters", {})
        counters[key] = counters.get(key, 0) + amount

    # --- 앞 단계 산출물 ---
    def load_output(self, value: str, expected_skill: str) -> OutputRecord:
        """앞 단계 산출물과 기계 판독용 데이터를 읽고 원본 연결 정보를 확인한다."""
        path = self.resolve(value)
        parts = path.relative_to(self.root).parts
        if not parts or parts[0] != "outputs" or path.suffix != ".md":
            raise Blocked(f"앞 단계 입력은 run.py가 outputs/에 저장한 .md 산출물이어야 한다: {value}")
        sidecar_path = path.with_suffix(".data.json")
        if not sidecar_path.exists():
            raise Blocked(f"{self.rel(path)}: run.py가 만든 산출물이 아니다(기계 판독용 데이터가 없다).")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if sidecar.get("skill") != expected_skill:
            raise Blocked(f"{self.rel(path)}: {expected_skill} 산출물이 아니다({sidecar.get('skill')}).")
        markdown = path.read_text(encoding="utf-8")
        problems = V.provenance_errors(markdown)
        if problems:
            raise Blocked(f"{self.rel(path)}: " + " ".join(problems))
        if _strip_confirmation(markdown) != _strip_confirmation(sidecar.get("markdown", "")):
            self.warn(f"{self.rel(path)}: 확정란 밖의 수정이 있다. 수정 내용은 반영되지 않으므로 필요하면 그 단계를 다시 실행한다.")
        if not sidecar.get("virtual"):
            self.manifest["virtual"] = False
        self.note_sources(path)
        return OutputRecord(path, markdown, sidecar)

    def require_header_confirmation(self, record: OutputRecord, expected: str) -> None:
        value, _ = V.split_confirmation(V.header_values(record.markdown).get("엔지니어 확정", ""))
        if value != expected:
            raise Blocked(f"{self.rel(record.path)}: 엔지니어 확정란이 '{expected}'가 아니다(현재: {value or '비어 있음'}). 엔지니어가 확정한 뒤 다시 실행한다.")

    # --- 저장 ---
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "manifest.json").write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _strip_confirmation(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        if line.startswith(("엔지니어 확정:", "변경 사유:")) or line.lstrip().startswith("|"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def read_text_any(path: Path) -> str:
    """UTF-8을 먼저 읽고, 안 되면 CP949로 읽는다."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RunError(f"{path.name}: 텍스트 인코딩을 읽을 수 없다(UTF-8 또는 CP949로 저장한다).")


def parse_interview_id(value: str) -> dict[str, str]:
    match = INTERVIEW_ID.match(str(value or "").strip())
    if not match:
        raise RunError(f"인터뷰 식별자는 '[고객사 코드]-[부서 약칭]-[담당자 번호]-[회차]회차' 형식이어야 한다(예: A사-영업-01-1회차): {value}")
    return match.groupdict()


# ---------------------------------------------------------------------------
# 가명 처리 검사 설정 (private/은 에이전트 파일 도구로 읽을 수 없다)
# ---------------------------------------------------------------------------
def pii_settings(root: Path) -> tuple[set[str], dict[str, str]]:
    allowlist: set[str] = set()
    allow_file = root / "private" / "pii_allowlist.txt"
    if allow_file.exists():
        allowlist = {line.strip() for line in allow_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    mapping: dict[str, str] = {}
    private = root / "private"
    if private.exists():
        for mapping_file in sorted(private.rglob("mapping*.json")):
            try:
                data = json.loads(mapping_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            entries = data.get("replacements", data) if isinstance(data, dict) else {}
            if isinstance(entries, dict):
                mapping.update({str(key): str(value) for key, value in entries.items()})
    return allowlist, mapping


def pii_gate(run: Run, label: str, text: str, virtual: bool) -> None:
    """LLM 입력이나 산출물에 식별 정보 패턴이 있으면 멈춘다. tests/의 가상 입력은 경고만 남긴다."""
    allowlist, mapping = pii_settings(run.root)
    findings = V.find_pii(text, allowlist, mapping)
    if not findings:
        return
    summary = ", ".join(finding.describe() for finding in findings[:12]) + (" 외" if len(findings) > 12 else "")
    if virtual:
        run.warn(f"가명 처리 검사 경고(가상 입력): {label} {summary}")
        return
    raise Blocked(
        f"가명 처리 검사에 걸려 LLM 전송을 멈췄다: {label} {summary}. "
        "엔지니어가 해당 줄을 가명 처리하거나(private/mapping.json 보강 뒤 core/pseudonymize.py apply), "
        "오탐이면 private/pii_allowlist.txt에 그 표현을 더한 뒤 다시 실행한다."
    )


# ---------------------------------------------------------------------------
# skill 파이프라인 모듈
# ---------------------------------------------------------------------------
def load_pipeline(root: Path, skill: str):
    scripts = root / "skills" / skill / "scripts"
    module_path = scripts / "pipeline.py"
    if not module_path.exists():
        raise RunError(f"{skill}: skills/{skill}/scripts/pipeline.py가 없다.")
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(f"pipeline_{skill.replace('-', '_')}", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def skill_calls(root: Path, skill: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta, body = read_front_matter(root / "skills" / skill / "SKILL.md")
    calls = []
    for prompt in meta.get("llm_calls") or []:
        path = root / "skills" / skill / str(prompt)
        prompt_meta, _ = read_front_matter(path)
        calls.append({"name": path.stem, "prompt": str(prompt), "version": prompt_meta.get("version", "?")})
    meta["_body"] = body
    return meta, calls


def execution_version(root: Path, skill: str, meta: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    agent_meta, _ = read_front_matter(root / "core" / "agent.md") if (root / "core" / "agent.md").exists() else ({}, "")
    parts = [f"{call['name']} v{call['version']}" for call in calls]
    rules = meta.get("rules")
    if rules:
        rules_meta, _ = read_front_matter(root / "skills" / skill / str(rules))
        parts.append(f"rules v{rules_meta.get('version', '?')}")
    model_id, settings = model_description()
    inner = f"({', '.join(parts)})" if parts else ""
    return (
        f"agent v{agent_meta.get('version', '?')}, {skill} v{meta.get('version', '?')}{inner}, "
        f"코드 {code_commit(root)}, 모델 {model_id}, 설정 {settings}"
    )


# ---------------------------------------------------------------------------
# LLM 호출 패키지
# ---------------------------------------------------------------------------
def build_package(run: Run, pipeline, index: int, retry: dict[str, Any] | None = None) -> Path:
    call = run.manifest["calls"][index]
    prompt_meta, prompt_body = read_front_matter(run.skill_dir / call["prompt"])
    skill_meta, skill_body = read_front_matter(run.skill_dir / "SKILL.md")
    materials: list[Material] = pipeline.materials(run, call["name"])
    for material in materials:
        pii_gate(run, f"[{material.title}]", material.text, material.virtual or run.virtual)

    output = run.dir / f"{call['name']}.json"
    sections = [
        f"# LLM 호출: {run.skill} / {call['name']} ({index + 1}/{len(run.manifest['calls'])})",
        "이 파일 전체가 이번 LLM 호출의 입력이다. 아래 작업 지침과 지시만 따르고, 지시가 정한 JSON만 출력한다.",
        f"## 작업 지침 (SKILL.md v{skill_meta.get('version', '?')})",
        markdown_section(skill_body, "작업 지침"),
        f"## 지시 ({call['prompt']} v{prompt_meta.get('version', '?')})",
        prompt_body.strip(),
    ]
    for rules_ref in prompt_meta.get("rules") or []:
        # 'rules.md#판정 조건'처럼 절 이름을 붙이면 그 절만 넣는다(프롬프트 파일이 지정한 규칙 사본).
        rules_name, _, section_name = str(rules_ref).partition("#")
        rules_meta, rules_body = read_front_matter(run.skill_dir / rules_name)
        if section_name:
            match = re.search(rf"(?ms)^##\s+{re.escape(section_name)}\s*$(.*?)(?=^##\s|\Z)", rules_body)
            if not match:
                raise RunError(f"{rules_name}에 '{section_name}' 절이 없다.")
            rules_body = f"## {section_name}\n{match.group(1)}"
        sections += [f"## 규칙 사본 ({rules_ref} v{rules_meta.get('version', '?')})", rules_body.strip()]
    for template_name in prompt_meta.get("templates") or []:
        sections += [f"## 템플릿 ({template_name})", (run.skill_dir / str(template_name)).read_text(encoding="utf-8").strip()]
    sections.append("## 입력 자료")
    for material in materials:
        sections += [f"### {material.title}", material.text.strip() or "(비어 있음)"]
    if retry:
        sections += [
            "## 다시 만들기",
            "직전 출력에서 아래 항목이 검사를 통과하지 못했다. 이 항목만 고친 전체 JSON을 다시 저장한다. "
            "나머지 항목은 직전 출력과 같게 둔다. 고칠 근거가 원문에 없으면 그 값을 [확인 필요: 내용]으로 바꾸거나 "
            "판정 조건을 '미충족'으로 바꾼다.",
            "\n".join(f"- {item['path']}: {item['message']}" for item in retry["failures"]),
            "### 직전 출력",
            "```json\n" + json.dumps(retry["previous"], ensure_ascii=False, indent=2) + "\n```",
        ]
    sections += [
        "## 저장",
        f"- 결과 JSON을 `{run.rel(output)}`에 저장한다(write_file). 설명이나 코드 펜스는 넣지 않는다.\n"
        f"- 저장한 뒤 `python3 core/run.py submit {run.run_id}`를 실행한다.",
    ]
    suffix = "_retry" if retry else ""
    package = run.dir / f"call_{index + 1}_{call['name']}{suffix}.md"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text("\n\n".join(section for section in sections if section) + "\n", encoding="utf-8")
    call["package"] = run.rel(package)
    call["output"] = run.rel(output)
    return package


def print_call_instruction(run: Run, index: int, retry: bool = False) -> None:
    call = run.manifest["calls"][index]
    label = "다시 만들기 (1회)" if retry else "LLM 작업"
    print(f"[run {run.run_id}] {run.skill} {index + 1}/{len(run.manifest['calls'])}단계: {label}")
    print(f"1) read_file로 `{call['package']}` 전체를 읽는다(limit 2000 이상).")
    print(f"2) 패키지의 지시대로 JSON만 만들어 `{call['output']}`에 write_file로 저장한다.")
    print(f"3) `python3 core/run.py submit {run.run_id}`를 실행한다.")


# ---------------------------------------------------------------------------
# 산출물 저장 (규칙 6)
# ---------------------------------------------------------------------------
def output_name(run: Run, result: Result) -> Path:
    naming = run.state.get("naming", {})
    client = naming.get("client") or "[고객사]"
    dept = naming.get("dept") or "[부서]"
    person = naming.get("person") or "공통"
    person_code = person if person == "공통" else f"담당자{person}"
    date = _dt.date.today().strftime("%Y%m%d")
    prefix = f"{client}_{dept}_{person_code}_{result.stage}단계_{date}_r"
    outputs = run.root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    used = [int(match.group(1)) for path in outputs.glob(prefix + "*.md") if (match := re.match(re.escape(prefix) + r"(\d+)", path.name))]
    number = max(used, default=0) + 1
    suffix = f"_{result.card_id}" if result.card_id else ""
    return outputs / f"{prefix}{number}{suffix}.md"


def finish_run(run: Run, pipeline) -> Path:
    result: Result = pipeline.finish(run)
    problems = V.provenance_errors(result.markdown)
    if problems:
        raise RunError("산출물 상단 정보가 빠졌다: " + " ".join(problems))
    pii_gate(run, "[산출물]", result.markdown, run.virtual)
    path = output_name(run, result)
    path.write_text(result.markdown, encoding="utf-8")
    sidecar = {
        "skill": run.skill,
        "run_id": run.run_id,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "virtual": run.virtual,
        "header": run.header,
        "naming": run.state.get("naming", {}),
        "warnings": run.manifest["warnings"],
        "counters": run.manifest.get("counters", {}),
        "data": result.data,
        "markdown": result.markdown,
    }
    path.with_suffix(".data.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run.manifest["status"] = "done"
    run.manifest["output"] = run.rel(path)
    run.save()
    print(f"[run {run.run_id}] {run.skill} 완료: {run.rel(path)}")
    for warning in run.manifest["warnings"]:
        print(f"- 경고: {warning}")
    confirmation = pipeline.CONFIRMATION_HINT if hasattr(pipeline, "CONFIRMATION_HINT") else ""
    if confirmation:
        print(f"엔지니어 확정 필요: {confirmation}")
    return path


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------
def new_run_id(root: Path, skill: str) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{skill}-{stamp}"
    run_id, number = base, 2
    while (root / "work" / "runs" / run_id).exists():
        run_id, number = f"{base}-{number}", number + 1
    return run_id


def command_start(root: Path, skill: str, argv: list[str]) -> int:
    if skill not in SKILLS:
        raise RunError(f"알 수 없는 skill이다: {skill}. ({', '.join(SKILLS)})")
    pipeline = load_pipeline(root, skill)
    parser = argparse.ArgumentParser(prog=f"run.py start {skill}")
    pipeline.add_arguments(parser)
    args = parser.parse_args(argv)
    meta, calls = skill_calls(root, skill)
    run_id = new_run_id(root, skill)
    manifest = {
        "skill": skill,
        "status": "running",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "args": {key: (str(value) if isinstance(value, Path) else value) for key, value in vars(args).items()},
        "virtual": True,
        "sources": [],
        "header": {},
        "calls": [{**call, "attempts": 0, "status": "pending"} for call in calls],
        "current": 0,
        "state": {},
        "warnings": [],
    }
    run = Run(root, run_id, manifest)
    pipeline.start(run)
    run.header.setdefault("출처", ", ".join(run.manifest["sources"]) or "[확인 필요: 입력 파일]")
    run.header["실행 버전"] = execution_version(root, skill, meta, calls)
    if run.state.get("skip_llm"):
        run.manifest["calls"] = []
        run.save()
        finish_run(run, pipeline)
        return EXIT_OK
    build_package(run, pipeline, 0)
    run.save()
    print_call_instruction(run, 0)
    return EXIT_OK


def command_submit(root: Path, run_id: str) -> int:
    manifest_path = root / "work" / "runs" / run_id / "manifest.json"
    if not manifest_path.exists():
        raise RunError(f"실행 기록이 없다: {run_id}")
    run = Run(root, run_id, json.loads(manifest_path.read_text(encoding="utf-8")))
    if run.manifest.get("status") != "running":
        raise RunError(f"{run_id}는 이미 끝난 실행이다({run.manifest.get('status')}).")
    pipeline = load_pipeline(root, run.skill)
    index = run.manifest["current"]
    call = run.manifest["calls"][index]
    output = root / call["output"]
    if not output.exists():
        raise RunError(f"LLM 결과 파일이 없다: {call['output']}")
    call["attempts"] += 1
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
        check: Check = pipeline.validate(run, call["name"], data)
    except (json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as error:
        check = Check(data=None, failures=[{"path": "(전체)", "message": f"지시한 JSON 형식이 아니다: {error}"}])
        data = None

    if call["attempts"] > 1 and data is not None and call.get("previous") is not None:
        merged = pipeline.merge_retry(run, call["name"], call["previous"], data, call.get("failures", []))
        check = pipeline.validate(run, call["name"], merged)
    for note in check.notes:
        run.warn(note)

    if check.failures and call["attempts"] == 1:
        call["previous"] = check.data if check.data is not None else data
        call["failures"] = check.failures
        call["status"] = "retrying"
        build_package(run, pipeline, index, retry={"previous": call["previous"], "failures": check.failures})
        run.save()
        print(f"[run {run_id}] 검사 실패 {len(check.failures)}건. 실패 항목만 한 번 다시 만든다.")
        for failure in check.failures[:30]:
            print(f"- {failure['path']}: {failure['message']}")
        print_call_instruction(run, index, retry=True)
        return EXIT_RETRY

    if check.data is None:
        run.manifest["status"] = "failed"
        run.save()
        raise RunError("다시 만든 뒤에도 JSON 형식이 맞지 않아 실행을 멈췄다. 같은 skill을 처음부터 다시 실행한다.")
    data = pipeline.degrade(run, call["name"], check.data, check.failures) if check.failures else check.data
    call["status"] = "done"
    call.pop("previous", None)
    run.state.setdefault("calls", {})[call["name"]] = data
    if hasattr(pipeline, "after_call"):
        pipeline.after_call(run, call["name"], data)

    next_index = index + 1
    skipped = run.state.get("skip_calls", {})
    while next_index < len(run.manifest["calls"]) and run.manifest["calls"][next_index]["name"] in skipped:
        # 파이프라인이 넘길 재료가 없다고 정한 호출(예: 요약 대상 업무가 없음)은 부르지 않는다.
        name = run.manifest["calls"][next_index]["name"]
        run.manifest["calls"][next_index]["status"] = "skipped"
        run.state["calls"][name] = skipped[name]
        next_index += 1
    if next_index < len(run.manifest["calls"]):
        run.manifest["current"] = next_index
        build_package(run, pipeline, next_index)
        run.save()
        print_call_instruction(run, next_index)
        return EXIT_OK
    finish_run(run, pipeline)
    return EXIT_OK


def command_status(root: Path, run_id: str | None) -> int:
    runs_dir = root / "work" / "runs"
    manifests = sorted(runs_dir.glob("*/manifest.json")) if run_id is None else [runs_dir / run_id / "manifest.json"]
    for path in manifests[-10:]:
        if not path.exists():
            raise RunError(f"실행 기록이 없다: {run_id}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        step = ""
        if manifest.get("status") == "running" and manifest.get("calls"):
            call = manifest["calls"][manifest["current"]]
            step = f" / 현재 {call['name']} ({call['status']}, 패키지 {call.get('package')})"
        print(f"{path.parent.name}: {manifest['skill']} {manifest.get('status')}{step} {manifest.get('output', '')}".rstrip())
    return EXIT_OK


def command_check(root: Path, skill: str, target: str, card: str | None) -> int:
    """산출물을 다음 단계 입력으로 쓸 수 있는지(확정 완료) 확인한다."""
    pipeline = load_pipeline(root, skill)
    run = Run(root, "check", {"skill": skill, "args": {}, "state": {}, "header": {}, "warnings": [], "virtual": True})
    if not hasattr(pipeline, "check_input"):
        raise RunError(f"{skill}에는 확정 검사 대상 입력이 없다.")
    pipeline.check_input(run, target, card)
    print(f"확정 검사 통과: {target} → {skill} 입력으로 쓸 수 있다.")
    for warning in run.manifest["warnings"]:
        print(f"- 경고: {warning}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = workspace_root()
    usage = "사용법: run.py start <skill> [옵션] | submit <run_id> | status [run_id] | check <skill> <산출물.md> [--card 번호]"
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return EXIT_OK
    command, rest = argv[0], argv[1:]
    try:
        if command == "start" and rest:
            return command_start(root, rest[0], rest[1:])
        if command == "submit" and len(rest) == 1:
            return command_submit(root, rest[0])
        if command == "status":
            return command_status(root, rest[0] if rest else None)
        if command == "check" and len(rest) >= 2:
            card = rest[rest.index("--card") + 1] if "--card" in rest else None
            return command_check(root, rest[0], rest[1], card)
        raise RunError(usage)
    except Blocked as error:
        print(f"[차단] {error}", file=sys.stderr)
        return EXIT_BLOCKED
    except RunError as error:
        print(f"[오류] {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
