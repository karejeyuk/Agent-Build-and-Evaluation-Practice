---
name: interview-sheet
description: 담당자 프로필과 인터뷰 조건으로 인터뷰 시트나 메일 질문지를 만든다(1단계). "인터뷰 시트", "인터뷰 질문지", "메일 질문지" 요청에 쓴다.
version: 2
stage: 1
available_from: v1
inputs: [담당자 프로필, 인터뷰 조건(시간, 형태, 회차), 0단계 산출물, 직전 회차 확인 목록(2회차 이상)]
outputs: [templates/interview_sheet.md, templates/mail_questionnaire.md]
llm_calls: [prompts/custom_questions.md]
scripts: [scripts/job_type.py(호출 전), scripts/build_sheet.py 배분(호출 전), scripts/build_sheet.py 조립(호출 뒤), core/validate.py 템플릿·수량·F번호 검사(조립 뒤)]
data: [job_types.yaml]
confirmation: 사용 승인
---
# 목적
15분 안에 담당자에게 맞는 인터뷰 시트를 완성한다(기획서 3.4).

# 작업 지침
- 코드가 넘긴 재료 항목마다 질문을 하나씩 만든다. 재료에 없는 주제는 묻지 않는다.
- 담당자에게 건네는 질문이므로 존댓말로 쓴다.
- 고정 문구(인트로 멘트, 공통 질문, 심화 후속 질문, 마무리 체크리스트)와 이월 블록, 엔지니어용 부록은 쓰지 않는다.

# 실행 순서
작업 공간 루트에서 실행한다.
1) `python3 core/run.py start interview-sheet --interview-id <A사-영업-01-1회차> --profile <프로필.json> --format <대면|온라인|메일> [--duration <30|60>] [--pre-research <outputs/…_0단계_….md>] [--followup <확인목록.txt>] [--dept <영업부>]`
   - 프로필 JSON 키: 부서, 직무명, 직급, 담당 업무, 연차. 회차는 인터뷰 식별자에서 읽는다.
   - 0단계 산출물은 엔지니어가 `검토 완료`로 확정한 것만 받는다. 확정되지 않았으면 차단된다.
   - 2회차 이상은 엔지니어가 정리한 확인 목록을 넣는다. 한 줄에 `[F번호][종류] 내용`(종류: 이월, 다음회차확인, 문서후보, 확인필요, 정보부족)으로 쓴다.
2) 안내된 호출 패키지를 읽고 `{"questions": [...]}` JSON을 저장한 뒤 `python3 core/run.py submit <run_id>`를 실행한다.
3) 다시 만들기 요청(종료 코드 3)이면 실패 항목만 고쳐 한 번 더 submit한다. 차단(종료 코드 2)이면 메시지를 엔지니어에게 전하고 멈춘다.
4) 완료되면 산출물 경로와 경고를 알린다. 메일 질문지는 '엔지니어용 부록'을 뺀 본문만 담당자에게 보낸다고 알린다.

# 품질 기준과 실패 처리
기획서 3.4의 품질 기준과 예외 상황, 5.4의 검증 항목(템플릿 준수, 1단계 수량·반영, 문체)을 따른다. 맞춤 질문 수, 고정 문구, 확인 목록 F번호 반영은 코드가 검사하고, 통과하지 못하면 한 번 다시 만든다. 그래도 실패하면 경고를 단 채 산출하고 엔지니어가 확정할 때 판단한다.

# 엔지니어 확정
상단 '엔지니어 확정' 줄에 `사용 승인`을 적은 시트로 인터뷰를 진행한다.
