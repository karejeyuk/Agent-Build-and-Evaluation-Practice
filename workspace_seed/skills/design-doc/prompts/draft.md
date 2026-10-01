# 설계서 JSON 작성

착수가 엔지니어에 의해 확정된 카드와 순위표만 사용한다. 아래 9목차 구조의 모든 필드를 채우고, 알 수 없는 값은 `[확인 필요: ...]` 또는 `[정보 부족: 2단계 보강 필요]`로 표시한다. 업무 사실은 카드의 근거 인용을 evidence에 연결한다. 그 밖의 사실이나 도구를 만들어내지 않는다.

워크플로 각 단계에는 `who`, `what`, `tool`을 둔다. `tool`은 확인된 도구가 없으면 `[확인 필요: 도구]`로 둔다. B1형 또는 오류 영향도 1~2점이면 워크플로에 `사람 승인 후 실행`을 포함한다. 공통 운영 규칙 5개는 템플릿에 고정되므로 생성하지 않는다. 현업용 1쪽 요약은 고객사 전달용 존댓말로 쓴다.

출력 JSON:
`{"sections":{"1":{"agent_name":"","purpose":"","user":"","run_time":""},"2":{"must_do":"","must_not_do":""},"3":{"workflow":[{"who":"","what":"","tool":""}],"human_approval":""},"4":{"tools":[],"integration":""},"5":{"common_rules":"템플릿 고정","additional_rules":[]},"6":{"input_format":"","output_format":"","example":""},"7":{"success_definition":"","measurement":""},"8":{"risks":[],"responses":[]},"9":{"unknowns":[],"special_notes":[]}},"field_evidence":{},"business_summary":""}`

JSON 외의 설명은 쓰지 않는다.
