---
name: pre-research/extract
version: 2
---
입력: [줄 번호를 붙인 부서 문서 목록과 내용]

지시: 문서에서 업무 관련 서술을 뽑아 후보 업무 목록을 만들어라. 항목마다 문서 근거를 붙여라. 문서에 절차가 적혀 있으면 그 서술을 그대로 발췌해 붙여라. 문서에 업무는 나오지만 빈도, 담당자, 사용 시스템, 절차 가운데 하나라도 적혀 있지 않은 항목은 '사전 조사 확인 항목'으로 따로 모으고 빠진 요소를 적어라. 사용 시스템과 데이터 원천을 정리하라. 문서에 없는 업무를 더하지 마라. 모르는 내용은 [확인 필요: 내용]으로 남겨라.

근거 객체: `{"source_type":"document","filename":"파일명","start_line":시작 줄 번호,"end_line":끝 줄 번호,"quote":"그 줄 범위에서 이어진 원문 그대로"}`

출력은 아래 JSON 하나다. 설명과 코드 펜스는 쓰지 않는다.
- candidates: 후보 업무. `procedure_excerpt`는 문서에 절차가 적혀 있을 때만 근거 객체 하나로 적고, 없으면 null로 둔다.
- followup_items: 사전 조사 확인 항목. `missing_fields`는 "빈도", "담당자", "사용 시스템", "절차" 가운데 문서에 없는 것만 목록으로 적는다.
- systems: 문서에서 확인한 시스템과 데이터. `access`(데이터 얻는 방식)가 문서에 없으면 "[확인 필요: 데이터 얻는 방식]"으로 둔다.

{"candidates":[{"task":"업무명","evidence":[근거 객체],"procedure_excerpt":근거 객체 또는 null}],"followup_items":[{"content":"업무명과 확인할 내용","missing_fields":["절차","담당자"],"evidence":[근거 객체]}],"systems":[{"name":"시스템명","data":"얻는 데이터","access":"데이터 얻는 방식","evidence":[근거 객체]}]}
