---
name: design-doc/draft
version: 2
templates: [templates/design_doc.md]
---
입력: [착수가 확정된 카드] [종합 순위표의 해당 업무 행] [design_doc.md 템플릿(상단 고정 문구와 5번 목차의 공통 운영 규칙 5개가 고정 문구로 들어 있다)]

지시: 설계서 9목차 템플릿에 맞춰 초안을 써라. 워크플로의 각 단계는 누가, 무엇을, 어떤 도구로 하는지 세 요소로 나눠 써라. 업무 사실(절차, 시스템, 예외, 실수 영향)을 쓸 때는 카드의 근거를 인용하라. B1형이거나 오류 영향도가 1~2점이면 사람 승인 지점을 반드시 넣어라. 5번 목차의 공통 운영 규칙 5개는 템플릿에 고정되어 있으니 쓰지 말고 업무별 추가 규칙만 써라. 모르는 내용은 [확인 필요: 내용]으로, 쓸 정보가 없는 목차는 [정보 부족: 2단계 보강 필요]로 두어라. 워크플로를 [정보 부족]으로 두더라도 필수 승인 지점은 approval_points에 '사람 승인 후 실행' 한 줄로 적어라. 마지막에 현업용 1쪽 요약을 전문 용어 없이 쉬운 존댓말로 써라. 설계서 본문은 한다체로 쓴다.

작성 규칙
- basis는 항목이 현재 업무의 사실이면 "fact", 이번 설계에서 새로 제안하는 내용이면 "proposal"로 적는다. "fact" 항목에는 카드에 붙은 근거를 그대로 옮긴 근거 객체가 반드시 있어야 한다.
- 워크플로 단계의 who는 "사람" 또는 "에이전트"다. 사람 승인 단계는 who "사람", approval true로 적는다.
- 도구의 integration은 "API", "파일", "수동 복사" 가운데 하나이고, 모르면 "[확인 필요: 연동 방식]"으로 둔다.
- 쓸 정보가 없는 목차는 그 목차 전체를 문자열 "[정보 부족: 2단계 보강 필요]"로 둔다(3번 목차는 workflow만 그렇게 두고 approval_points는 쓴다).

근거 객체: 카드에 붙은 인용을 그대로 옮긴다. 발화 {"source_type":"utterance","q":"Q2","quote":"…"}, 메모 {"source_type":"memo","m":"M1","quote":"…"}, 문서 {"source_type":"document","filename":"…","start_line":1,"end_line":2,"quote":"…"}

출력은 아래 JSON 하나다. 설명과 코드 펜스는 쓰지 않는다.
{"sections":{"1":{"agent_name":"","purpose":"","user":"","run_time":""},"2":{"must_do":[""],"must_not_do":[""]},"3":{"workflow":[{"who":"에이전트","what":"","tool":"","approval":false,"basis":"fact","evidence":[근거 객체]}],"approval_points":[""]},"4":{"tools":[{"name":"","integration":"파일","basis":"fact","evidence":[근거 객체]}]},"5":{"additional_rules":[""]},"6":{"input_format":"","output_format":"","example":""},"7":{"success_definition":"","measurement":""},"8":{"risks":[{"situation":"","response":"","basis":"fact","evidence":[근거 객체]}]},"9":{"unknowns":[""],"special_notes":[""]}},"business_summary":""}
