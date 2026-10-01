"""AI 전환 파이프라인용 런타임 가드(도구 호출 미들웨어).

- private/(가명 매핑표와 원본)는 파일 도구로 읽거나 쓸 수 없고, glob·grep 결과에서도 뺀다.
- outputs/(확정 대상 산출물)와 core/(검증 계층)는 파일 도구로 쓸 수 없다. 산출물은 core/run.py만 저장한다.
- inputs/의 내용을 읽을 때 가명 처리 패턴이 남아 있으면 내용 대신 차단 메시지를 돌려준다.
- execute로 위 폴더를 건드리는 명령은 막는다. core/run.py, core/pseudonymize.py, core/validate.py 실행만 허용한다.

deepagents의 FilesystemPermission은 execute가 있는 백엔드에서 쓸 수 없어 이 미들웨어로 대신한다.
execute 검사는 실수를 막는 장치이지 보안 경계가 아니다. 셸은 작업 공간 밖도 볼 수 있다.
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

CORE_DIR = Path(__file__).resolve().parent / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import validate as V  # noqa: E402

READ_TOOLS = {"read_file", "ls", "glob", "grep"}
WRITE_TOOLS = {"write_file", "edit_file"}
NO_READ = ("private",)
NO_WRITE = ("private", "outputs", "core")

PROTECTED = re.compile(r"(?<![\w.-])/?(?:private|outputs|core)(?:/|\b)")
ALLOWED_COMMAND = re.compile(r"^\s*(?:python3?|uv run python3?)\s+core/(?:run|pseudonymize|validate)\.py(?:\s|$)")
SHELL_META = re.compile(r"[;&|`$<>\n]")


def _top_folder(value: Any) -> str:
    """'/private/a.json', 'private/a.json', '/./private' 모두 'private'로 본다."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = posixpath.normpath("/" + text.lstrip("/")).lstrip("/").split("/")
    return parts[0] if parts else ""


def command_block_reason(command: str) -> str | None:
    """보호 폴더를 건드리는 셸 명령이면 차단 사유를 돌려준다."""
    if not PROTECTED.search(command):
        return None
    if ALLOWED_COMMAND.match(command) and not SHELL_META.search(command):
        return None
    return (
        "private/, outputs/, core/를 건드리는 셸 명령은 실행할 수 없다. "
        "파이프라인 산출물은 `python3 core/run.py …`로만 만들고, 산출물을 읽을 때는 read_file을 쓴다."
    )


def call_block_reason(name: str, args: dict[str, Any]) -> str | None:
    """도구 호출 전에 막아야 하는지 판단한다."""
    if name == "execute":
        return command_block_reason(str(args.get("command", "")))
    target = _top_folder(args.get("file_path") or args.get("path"))
    if name in READ_TOOLS and target in NO_READ:
        return "private/는 가명 매핑표와 원본 보관 폴더라 열 수 없다. 가명 처리본은 inputs/에 있다."
    if name in WRITE_TOOLS and target in NO_WRITE:
        return f"{target}/에는 파일 도구로 쓸 수 없다. 파이프라인 산출물은 `python3 core/run.py`가 저장하고, 확정란은 엔지니어가 직접 적는다."
    return None


def _blocked(request, message: str) -> ToolMessage:
    return ToolMessage(content=f"[차단] {message}", tool_call_id=request.tool_call["id"], name=request.tool_call["name"], status="error")


def _filter_result(request, result: Any) -> Any:
    """목록 결과에서 private/를 빼고, inputs/ 내용에 식별 정보 패턴이 있으면 숨긴다(규칙 3)."""
    name = request.tool_call["name"]
    if name not in READ_TOOLS or not isinstance(result, ToolMessage) or not isinstance(result.content, str):
        return result
    content = result.content
    if name in ("glob", "grep", "ls"):
        kept = [line for line in content.splitlines() if "/private/" not in line and not line.rstrip("/").endswith("/private")]
        if len(kept) != len(content.splitlines()):
            content = "\n".join(kept)
            result = ToolMessage(content=content, tool_call_id=result.tool_call_id, name=result.name, status=result.status)
    reads_inputs = _top_folder((request.tool_call.get("args") or {}).get("file_path") or (request.tool_call.get("args") or {}).get("path")) == "inputs"
    if (name == "read_file" and reads_inputs) or (name == "grep" and ("/inputs/" in content or reads_inputs)):
        kinds = sorted({finding.kind for finding in V.find_pii(content)})
        if kinds:
            return _blocked(
                request,
                f"가명 처리 검사에 걸려 내용을 보여 주지 않는다({', '.join(kinds)}). "
                "엔지니어가 가명 처리를 확인하거나 private/pii_allowlist.txt를 보강한 뒤 다시 시도한다.",
            )
    return result


class PipelineGuard(AgentMiddleware):
    """보호 폴더 접근을 막고 inputs/ 읽기 결과를 가명 처리 패턴으로 검사한다."""

    def wrap_tool_call(self, request, handler: Callable[[Any], Any]) -> Any:
        reason = call_block_reason(request.tool_call["name"], request.tool_call.get("args") or {})
        if reason:
            return _blocked(request, reason)
        return _filter_result(request, handler(request))

    async def awrap_tool_call(self, request, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        reason = call_block_reason(request.tool_call["name"], request.tool_call.get("args") or {})
        if reason:
            return _blocked(request, reason)
        return _filter_result(request, await handler(request))
