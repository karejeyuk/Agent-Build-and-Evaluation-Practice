# 판정 조건표

입력: 카드 추출 JSON, 동일한 인터뷰 원문, 선택적 검토 완료 사전 조사 요약서, `rules.md`의 조건 1~6과 용어 정의.

각 카드마다 조건 1~6을 `충족` 또는 `미충족`으로 작성한다. 충족이면 해당 원문 줄에서 연속된 인용을 넣는다. 미충족이면 `reason`에 해당 발화 없음 또는 관련 발화가 조건에 맞지 않는 이유를 적는다. 판단이 없다고 담당자가 명시했다면 조건 4의 `reason`은 `판단 없음 확인`, `citation_valid`는 `true`로 둔다. 조건 6이 계기 없는 횟수 발화 때문에 미충족이면 `reason`은 `계기 없음`으로 둔다.

조건 4가 충족이면 `judgment_point`에 지점 하나를 적는다. 조건 2가 충족이면 카드별 `standardization_recommendation`에 `variable_part`, `target`(절차·양식·판단 기준), `question`을 적는다. 조건을 직접 채우지 않는 다른 카드의 근거를 잘못 공유하지 않는다. 형은 고르지 않는다.

출력은 JSON 객체만 반환한다.
`{"cards":[{"card_id":"카드 번호","conditions":{"1":{"status":"충족|미충족","reason":"","evidence":[]},"2":{"status":"미충족","reason":"해당 발화 없음","evidence":[]},"3":{},"4":{},"5":{},"6":{}},"judgment_point":"","standardization_recommendation":{"variable_part":"","target":"","question":""}}]}`
