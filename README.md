# Agent Build and Evaluation Practice

## Codespaces에서 실행하기

> **전체 흐름** Codespace 만들기 → `.env` 설정 → 설치·실행 → `2024` 포트 공개 → LangSmith Studio 연결 → 채팅
>
> 아래 이미지는 이해를 돕기 위한 예시 화면입니다. 실제 화면은 서비스 업데이트에 따라 조금 다를 수 있습니다.

1. GitHub 저장소 우측 상단의 초록색 **Code** 버튼을 누르고 **Codespaces** 탭에서 **Create codespace on main**을 눌러 Codespace를 만듭니다.

   <img src="docs/images/codespaces/step01-create-codespace.png" alt="Code 버튼 → Codespaces 탭 → Create codespace on main" width="760">

2. 생성된 Codespace를 열고 VS Code 화면이 나타날 때까지 기다립니다. 왼쪽 아래에 **Codespaces: …** 표시가 보이면 준비가 끝난 것입니다.

   <img src="docs/images/codespaces/step02-codespace-ready.png" alt="Codespace의 VS Code 화면: 탐색기, 터미널, Codespaces 연결 표시" width="760">

3. 왼쪽 탐색기에서 `.env.example`을 복제해 `.env`로 이름을 바꿉니다. `.env`를 열고 `OPENAI_API_KEY`에 OpenRouter API 키를 입력한 뒤 저장합니다.

   <img src="docs/images/codespaces/step03-env-file.png" alt=".env 파일을 만들고 OPENAI_API_KEY에 OpenRouter API 키 입력" width="760">

   `.env`는 로컬 비밀 설정 파일이므로 커밋하거나 다른 사람과 공유하지 마세요.

4. 하단 패널에서 **Terminal**을 열고 의존성을 설치합니다.

   ```bash
   uv sync
   ```

   <img src="docs/images/codespaces/step04-uv-sync.png" alt="터미널에서 uv sync 실행 후 설치 완료" width="760">

5. 설치가 끝나면 메인 스크립트를 실행합니다.

   ```bash
   uv run python langchain-deepagents.py
   ```

   터미널에 LangGraph 서버가 시작되었다는 로그가 나오는지 확인합니다. 서버를 종료하려면 `Ctrl+C`를 누릅니다.

   <img src="docs/images/codespaces/step05-run-server.png" alt="메인 스크립트 실행 후 LangGraph 서버 시작 배너" width="760">

6. VS Code 우측 하단에 포트 포워딩 요청이 뜨면 허용합니다. 하단 **PORTS** 탭에서 `2024` 포트가 보이는지 확인하고, 포트 공개 범위를 **Public**으로 설정합니다(`2024` 행 우클릭 → **Port Visibility** → **Public**). 포트의 **Forwarded Address**를 복사합니다.

   <img src="docs/images/codespaces/step06-port-public.png" alt="PORTS 탭에서 2024 포트를 Public으로 바꾸고 Forwarded Address 복사" width="760">

7. 새 브라우저 탭에서 [LangSmith](https://smith.langchain.com/)에 접속해 로그인합니다.
8. 왼쪽 탐색 메뉴에서 **Studio**로 이동한 뒤 **Configure connection**을 누릅니다.

   <img src="docs/images/codespaces/step08-studio-menu.png" alt="LangSmith 왼쪽 메뉴의 Studio와 Configure connection 버튼" width="760">

9. **Base URL**에 PORTS 탭에서 복사한 `2024` 포트의 **Forwarded Address**를 붙여 넣습니다. 주소 끝의 `/`는 제거합니다.

   <img src="docs/images/codespaces/step09-base-url.png" alt="Base URL에 Forwarded Address 붙여넣기" width="760">

10. **Domain not allowed** 경고가 나타나면 **Add to allowed domains**를 눌러 허용합니다.

    <img src="docs/images/codespaces/step10-allow-domain.png" alt="Domain not allowed 경고에서 Add to allowed domains 클릭" width="760">

11. 연결되면 그래프 UI가 나타납니다. `deepagent` 그래프를 선택하고, 좌측 상단의 **Graph** 토글을 **Chat**으로 바꿔 메시지를 보내 응답을 확인합니다.

    <img src="docs/images/codespaces/step11-graph.png" alt="Studio 그래프 화면에서 deepagent 선택 후 Chat으로 전환" width="760">

    <img src="docs/images/codespaces/step11-chat.png" alt="Chat 모드에서 메시지를 보내고 에이전트 응답 확인" width="760">

## 필수 설정

`.env`의 `OPENAI_API_KEY`는 필수입니다. LangSmith 관측을 사용하려면 `LANGSMITH_TRACING=true`와 `LANGSMITH_API_KEY`를 설정하세요. 기본 프로젝트명은 `AI Agent Builder Assist`이며, 필요하면 `LANGSMITH_PROJECT`로 바꿀 수 있습니다. Tavily, Slack, Telegram, 이메일 연동은 해당 기능을 사용할 때만 각 키와 설정을 추가하면 됩니다. 자세한 환경변수 목록은 [`.env.example`](.env.example)을 참고하세요.

## AI 전환 업무 지원

에이전트 채팅에서 사전 조사(pre-research), 인터뷰 시트(interview-sheet), 업무 카드(task-card), 적합성 평가(fit-scoring), 설계서(design-doc), 성과 보고(report)를 요청할 수 있다. 여섯 skill은 모두 `core/run.py`로 실행된다. 에이전트는 run.py가 만든 호출 패키지를 읽고 JSON 초안만 쓰며, 엔지니어 확정 검사, 가명 처리 검사, 인용 대조, 다시 만들기(1회), 형·등급 계산, 상단 정보와 실행 버전 기록, 저장은 코드가 맡는다. 기준 문서는 [`docs/my_agent_plan.txt`](docs/my_agent_plan.txt)다.

작업 공간(`workspace/`) 폴더는 이렇게 쓴다.

| 폴더 | 용도 | 에이전트 접근 |
|---|---|---|
| `inputs/` | 실제 고객사 자료의 가명 처리본(프로필, 문서, 인터뷰 원문) | 읽기. 가명 처리 패턴이 남아 있으면 내용을 보여 주지 않는다 |
| `tests/` | 부록 A 같은 가상 시나리오 | 읽기. 가명 처리 검사는 경고만 남긴다 |
| `private/` | 가명 매핑표(`mapping.json`), 원본, 오탐 허용 목록(`pii_allowlist.txt`) | 열 수 없다 |
| `work/` | 실행 기록과 호출 패키지, LLM 초안 | 읽기·쓰기 |
| `outputs/` | 산출물(`.md`)과 기계 판독용 데이터(`.data.json`) | 읽기만. 저장은 run.py만 한다 |

실제 고객사 자료는 원본과 매핑표(`{"replacements": {"원래 이름": "가명"}}`)를 `private/`에 두고, 터미널에서 `python3 core/pseudonymize.py apply --input private/원본.txt --mapping private/mapping.json --output inputs/가명본.txt`로 가명 처리본을 만든 뒤 쓴다. 인터뷰 원문은 `Q1 면담자: ...`, `Q1 담당자: ...`처럼 질문 번호와 화자 표지를 붙이고, 질문 없는 메모는 문단마다 `M1: ...`을 붙인다.

모든 산출물은 초안이다. 엔지니어가 산출물 파일을 직접 열어 확정란을 채워야 다음 단계가 시작된다(에이전트는 확정란을 채울 수 없다).

| 단계 산출물 | 확정 위치 | 확정 값 |
|---|---|---|
| 0단계 사전 조사 요약서 | 상단 `엔지니어 확정:` | `검토 완료` |
| 1단계 인터뷰 시트·메일 질문지 | 상단 `엔지니어 확정:` | `사용 승인` |
| 2단계 업무 카드 | 카드마다 `엔지니어 확정:` | A형, B1형, B2형, C형, 다음 회차 확인(제안과 다르면 아래 줄에 `변경 사유: …`) |
| 3단계 종합 순위표 | 표의 `엔지니어 확정` 칸 | 즉시 착수, 검토 후 착수, 착수 보류, 다음 회차 확인(제안과 다르면 `(변경 사유: …)`) |
| 4단계 설계서 | 상단 `엔지니어 확정:` | `승인` |
| 5단계 성과 리포트 | 상단 `엔지니어 확정:` | `수치 확정` |

다음 단계 입력으로 쓸 수 있는지는 `python3 core/run.py check <skill> <산출물.md> [--card 카드 번호]`로 미리 확인할 수 있다.

스킬을 새로 받은 뒤에는 Studio에서 새 thread를 연다. deepagents는 thread마다 처음 한 번만 스킬 목록을 읽으므로, 이전 thread에서는 새 스킬이 보이지 않는다. 예전 버전 스킬 파일이 `workspace/skills/`에 남아 있으면 서버를 끈 상태에서 그 폴더를 지우고 다시 실행한다(서버가 켜진 채로 지우면 `workspace_seed/skills/`도 함께 지워진다).
