---
name: task-card
description: 인터뷰 원문을 업무 상세 카드와 판정 조건표로 정리한다. 형은 코드가 판정한다.
---

# 인터뷰 결과 정리

입력은 엔지니어가 질문 번호와 화자 표지를 붙인 원문 한 건과, 선택적인 검토 완료 사전 조사 요약서다. 2회차 통합 병합은 본 스킬 범위 밖으로 두며, 회차 식별 정보를 보존한다.

## 실행 순서

1. 입력 원문이 실제 고객사 자료라면 허용 및 가명 처리 여부를 확인한다. 원문은 Q번호별 `Q1 면담자:`와 `Q1 담당자:` 또는 M번호 문단이어야 한다.
2. 문서 근거에 쓸 사전 조사 요약서가 있다면 `status: reviewed`와 `엔지니어 확정: 검토 완료`를 확인한다.
3. `prompts/extract.md`로 카드 초안 JSON을 만들고 `prompts/conditions.md`로 조건표 JSON을 만든다. 모델은 형을 정하지 않는다.
4. 결과를 아래 JSON 계약으로 저장한다. 각 인용에는 `source_type`, `quote`, `q`/`m` 또는 `filename`, `start_line`, `end_line`을 넣는다.
5. `python3 skills/task-card/merge_results.py --extract <추출.json> --conditions <조건표.json> --output <combined.json>`으로 두 호출의 결과를 카드 번호 기준 병합한다. 카드 또는 조건표가 짝을 찾지 못하면 통과시키지 않는다.
6. `python3 skills/task-card/classify.py <combined.json> --output <classified.json>`로 형, 판단 개입 지점, C형 기록을 계산한다.
7. 작업 공간 루트에서 `python3 core/validate.py evidence --input <classified.json> --transcript <원문.txt> [--documents <번호붙인문서.json>]`를 실행해 모든 인용을 대조한다. 공통 검증기는 작업 공간 루트의 `core/`에 있다. 실패하면 오류가 난 항목만 한 번 다시 생성하고 재검증한다. 여전히 실패한 충족 조건은 `미충족(인용 대조 실패)`로 고치고 다시 분류한다. 최종 실패 필드는 `[확인 필요: ...]`로 둔다.
8. 카드 사실 필드에 근거가 있는지 확인한다. 근거가 없으면 3·4·6·7번 값과 5번 매뉴얼·양식 값을 `[확인 필요]`로 바꾼다. `python3 skills/task-card/render_card.py <classified.json> --source <입력파일> --interview-id <식별자> --execution-version <버전> --output <출력.md>`로 템플릿을 조립한다.
9. 인용 실패는 경고로 남기고 전체 산출을 끝낸다. 결과는 초안이며 다음 단계에는 엔지니어가 카드별로 확정한 산출물만 전달한다.

## 안전 규칙

- 카드 업무는 원문에서 확인된 업무만 만든다. 문서에만 있는 업무는 후보 목록으로 분리한다.
- 조건 1~6은 `rules.md`를 적용한다. 충족 조건마다 원문 근거가 있어야 한다.
- 카드 3·4·6·7번 필드 및 5번 매뉴얼·양식 값은 근거가 없으면 추정하지 않는다.
- 엔지니어 확정란은 비워 둔다. 형 제안은 코드 결과만 쓴다.
