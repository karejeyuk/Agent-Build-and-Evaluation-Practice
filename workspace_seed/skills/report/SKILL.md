---
name: report
description: PoC 실측치와 현업 피드백으로 성과 리포트 및 임원용 요약 초안을 만든다.
---

# 검증·성과 보고

1. 엔지니어가 확정한 설계서의 성공 정의, 실측 CSV/JSON, 가명 처리된 현업 피드백, 3단계 순위표 참고 정보를 읽는다.
2. `python3 skills/report/scripts/savings.py <savings-input.json> --output <savings.json>`으로 연간 횟수 × 1회 소요 시간 × (1 - 사람 개입률)을 계산한다. 입력 범위가 있으면 하한·상한을 계산한다. 실측값이 없으면 계산하지 않는다.
3. `prompts/draft.md`의 스키마로 목표, 실측, 미달 원인, 인용된 피드백, 개선안을 JSON 초안으로 작성한다. 모든 피드백 인용에는 원문 인덱스 또는 근거를 기록한다.
4. `python3 skills/report/scripts/render_report.py <report.json> --output <보고서.md>`로 Markdown을 조립한다. 실측 `(실측)`, 목표 `(목표)`, 추정 `(추정)` 표기는 코드가 붙인다. 비어 있는 실측값은 `[측정 불가]`로 남긴다. 피드백 인용은 `feedback_source` 원문에 실제로 포함되어야 한다.
5. 인용과 수치 표기를 확인한다. 입력에 없는 피드백 문구는 쓰지 않는다. 수치와 고객사 제출용 결론은 엔지니어가 검토한다.

산출물은 초안이며 엔지니어 확정란은 `수치 확정` 전까지 비워 둔다.
