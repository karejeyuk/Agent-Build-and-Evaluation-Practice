# 평가 기준 근거 선택

확정 A형·B1형 카드와 선택적 사전 조사 시스템·데이터 원천을 입력으로 사용한다. 반복성은 다루지 않는다.

각 카드마다 `procedure_clarity`, `data_accessibility`, `error_impact`의 구간을 하나씩 선택한다. 모든 선택에는 입력 근거의 인용 객체가 있어야 한다. 여러 구간에 걸치면 낮은 점수를 선택하고, 근거가 부족하면 `score: null` 및 `needs_confirmation: true`로 둔다. 등급, 총점, 절감 시간은 계산하지 않는다.

JSON만 출력한다:
`{"cards":[{"card_id":"...","criteria":{"procedure_clarity":{"score":1,"evidence":[{"source_type":"utterance","q":"Q1","speaker":"담당자","quote":"..."}]},"data_accessibility":{"score":null,"evidence":[],"needs_confirmation":true},"error_impact":{"score":1,"evidence":[]}}]}`
