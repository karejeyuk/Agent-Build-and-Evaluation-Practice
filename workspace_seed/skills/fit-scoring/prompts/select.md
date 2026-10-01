---
name: fit-scoring/select
version: 2
rules: ["rules.md#평가 기준표", "rules.md#채점 원칙"]
---
입력: [scoring.py가 고른 채점 대상 카드] [0단계 시스템·데이터 원천 정리(있을 때)] [규칙 사본의 평가 기준표와 채점 원칙]

지시: 절차 명확성, 데이터 접근성, 오류 영향도 기준마다 카드(필드 값과 그 뒤에 붙은 근거)와 0단계 시스템·데이터 원천 정리에서 근거를 찾아 인용하고, 평가 기준표에서 조건에 가장 맞는 칸을 골라라. 근거가 여러 칸에 걸치면 그 가운데 가장 낮은 칸을 골라라. 어느 칸을 지지하는 근거도 없으면 level을 null로, reason을 '[확인 필요: 내용]'으로 두고 칸을 고르지 마라. 근거는 카드에 붙은 인용(또는 0단계 정리의 문서 근거)을 근거 객체로 그대로 옮겨라. 새 인용을 만들지 마라. 반복성, 총점, 등급, 절감 시간은 코드가 계산하므로 다루지 마라.

근거 객체: 발화 {"source_type":"utterance","q":"Q2","quote":"카드에 붙은 인용 그대로"}, 메모 {"source_type":"memo","m":"M1","quote":"…"}, 문서 {"source_type":"document","filename":"…","start_line":1,"end_line":2,"quote":"…"}

출력은 아래 JSON 하나다. 입력의 모든 카드 번호를 빠짐없이 한 번씩 쓴다. 설명과 코드 펜스는 쓰지 않는다.
{"cards":[{"card_id":"카드 번호","criteria":{"procedure_clarity":{"level":5,"evidence":[근거 객체]},"data_accessibility":{"level":null,"reason":"[확인 필요: 데이터 얻는 방식]","evidence":[]},"error_impact":{"level":3,"evidence":[근거 객체]}}}]}
