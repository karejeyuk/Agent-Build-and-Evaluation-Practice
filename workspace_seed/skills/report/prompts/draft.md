# 성과 리포트 초안

성공 정의, PoC 실측, 가명 처리된 피드백, 절감 시간 계산 JSON만 사용한다. 근거가 없는 수치나 피드백을 만들지 않는다.

반환 JSON:
`{"metrics":[{"name":"","target":"","actual":"","source":""}],"misses":[{"metric":"","cause":"프롬프트 문제|데이터 문제|절차 표준화 부족(B2형으로 되돌림)|측정 방식 문제","evidence":[]}],"feedback":[{"category":"긍정|개선|추가 기대","quote":"원문 그대로","source":"피드백 원문 위치"}],"recommendations":[{"priority":"높음|중간|낮음","action":"","basis":""}],"savings":{},"executive_summary":"존댓말 요약"}`

- 실측이 없으면 `actual`은 null이다. 미측정은 `[측정 불가]`로 표시한다.
- 피드백 인용은 받은 원문 그대로 쓴다. 원문 인용 안의 숫자에는 태그를 추가하지 않는다.
- 근거가 없는 원인이나 개선안은 `[확인 필요: 근거]`로 남긴다.
- 출력은 JSON만 반환한다.
