import sys
import tempfile
import unittest
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "interview-sheet"
PRE_RESEARCH_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "pre-research"
TASK_CARD_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "task-card"
FIT_SCORING_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "fit-scoring"
DESIGN_DOC_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "design-doc"
REPORT_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "workspace_seed" / "skills" / "report" / "scripts"
CORE_DIR = Path(__file__).resolve().parents[1] / "core"
sys.path[:0] = [str(SKILL_DIR), str(PRE_RESEARCH_DIR), str(TASK_CARD_DIR), str(FIT_SCORING_DIR), str(DESIGN_DOC_DIR), str(REPORT_SCRIPT_DIR), str(CORE_DIR)]

from build_sheet import allocate_materials, normalize_followup, render_sheet
from job_type import detect_job_type, load_job_types
from prepare_documents import number_document, prepare_documents
from validate_excerpts import validate_excerpts
from classify import classify_conditions
from scoring import annual_frequency, score_cards
from validate import confirmation_error, fill_unconfirmed_fields, validate_evidence, validate_pii
from pseudonymize import sanitize_text
from design_doc import build_design_document
from savings import estimate_savings
from render_report import render_report
from merge_results import merge_results
from render_card import render as render_task_card
from render_scoring import render as render_scoring_table


class JobTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job_types = load_job_types()

    def test_selects_single_matching_type(self):
        result = detect_job_type(
            {"부서": "영업부", "담당 업무": "국내 영업 관리"},
            self.job_types,
        )
        self.assertEqual(result["type_id"], "sales")
        self.assertEqual(len(result["required_topics"]), 5)

    def test_uses_fallback_for_unmatched_and_ambiguous_profiles(self):
        unmatched = detect_job_type(
            {"부서": "IT운영팀", "담당 업무": "계정 관리"}, self.job_types
        )
        ambiguous = detect_job_type(
            {"부서": "영업 전략기획팀", "담당 업무": "영업 실적 분석"}, self.job_types
        )
        self.assertEqual(unmatched["type_id"], "fallback")
        self.assertEqual(ambiguous["type_id"], "fallback")
        self.assertIn("계정 관리", unmatched["required_topics"][0])

    def test_allocates_exact_question_counts_and_prioritizes_missing_details(self):
        profile = {"부서": "영업부", "담당 업무": "국내 영업 관리"}
        research = [
            {"kind": "사전조사확인", "content": "주기 확인", "missing_fields": ["빈도"]},
            {"kind": "사전조사확인", "content": "담당자 확인", "missing_fields": ["담당자"]},
            {"kind": "사전조사확인", "content": "절차 확인", "missing_fields": ["절차"]},
        ]
        short = allocate_materials(profile, "interview", 30, 1, research)
        long = allocate_materials(profile, "interview", 60, 1, research)
        mail = allocate_materials(profile, "mail", None, 1, research)

        self.assertEqual(short["question_count"], 5)
        self.assertEqual([item["content"] for item in short["materials"][-2:]], ["절차 확인", "담당자 확인"])
        self.assertEqual([item["content"] for item in short["carryover"]], ["주기 확인"])
        self.assertEqual(long["question_count"], 10)
        self.assertEqual(mail["question_count"], 2)
        self.assertEqual(mail["materials"][0]["content"], "절차 확인")

    def test_followup_priority_dedup_and_mail_appendix(self):
        profile = {"부서": "IT운영팀", "담당 업무": "계정 관리"}
        followups = normalize_followup(
            [
                {"kind": "정보부족", "content": "기한 확인", "f_numbers": ["F7"]},
                {"kind": "다음회차확인", "content": "업무 확인", "f_numbers": ["F5"]},
                {"kind": "이월", "content": "이전 이월", "f_numbers": ["F4"]},
                {"kind": "확인필요", "content": "빈도 확인", "f_numbers": ["F3"]},
                {"kind": "문서후보", "content": "문서 후보", "f_numbers": ["F2"]},
                {"kind": "이월", "content": "같은 이월", "f_numbers": ["F1"]},
                {"kind": "확인필요", "content": "빈도 확인", "f_numbers": ["F8"]},
            ]
        )
        self.assertEqual([item["kind"] for item in followups[:5]], ["이월", "이월", "다음회차확인", "문서후보", "확인필요"])
        merged = next(item for item in followups if item["content"] == "빈도 확인")
        self.assertEqual(merged["f_numbers"], ["F3", "F8"])

        mail_plan = allocate_materials(profile, "mail", None, 2, followup_items=followups)
        mail = render_sheet(
            mail_plan,
            ["이전 회차 항목을 확인해 주시겠어요?", "이 항목은 어떻게 처리하시나요?"],
            "profile.json, prior.md",
            "A사-IT-01-2회차",
            "agent v1, interview-sheet v1",
        )
        self.assertIn("맞춤 문항 1: F1", mail)
        self.assertIn("맞춤 문항 2: F4", mail)
        self.assertIn("### 다음 회차 이월", mail)
        self.assertNotIn("F1", mail.split("## 직무 맞춤 질문", 1)[1].split("회신 기한", 1)[0])

    def test_interview_template_keeps_question_count_and_frozen_questions(self):
        profile = {"부서": "품질관리팀", "담당 업무": "라인 점검"}
        plan = allocate_materials(
            profile, "interview", 30, 1
        )
        report = render_sheet(
            plan,
            [f"맞춤 질문 {index}을 설명해 주시겠어요?" for index in range(1, 6)],
            "profile.json",
            "A사-품질-01-1회차",
            "agent v1, interview-sheet v1",
        )
        self.assertEqual(len(re.findall(r"(?m)^\d+\. 맞춤 질문 \d", report)), 5)
        self.assertIn("오늘 하루 업무를 시간 순서대로 말씀해 주시겠어요?", report)
        self.assertIn("엔지니어 확정: (비어 있음)", report)

        long_plan = allocate_materials(profile, "interview", 60, 1)
        long_report = render_sheet(
            long_plan,
            [f"맞춤 질문 {index}을 설명해 주시겠어요?" for index in range(1, 11)],
            "profile.json",
            "A사-품질-01-1회차",
            "agent v1, interview-sheet v1",
        )
        self.assertEqual(len(re.findall(r"(?m)^\d+\.", long_report)), 18)

        mail_plan = allocate_materials(profile, "mail", None, 1)
        mail_report = render_sheet(
            mail_plan,
            [f"맞춤 질문 {index}에 대해 적어 주세요." for index in range(1, 3)],
            "profile.json",
            "A사-품질-01-1회차",
            "agent v1, interview-sheet v1",
        )
        self.assertEqual(len(re.findall(r"(?m)^\d+\.", mail_report)), 10)


class PreResearchTests(unittest.TestCase):
    def test_no_document_flag_and_line_numbering(self):
        self.assertEqual(prepare_documents([])["status"], "no_pre_research")
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "guide.txt"
            document.write_text("업무 절차\n자료를 확인한다.\n", encoding="utf-8")
            result = number_document(document)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["numbered_text"], "L1: 업무 절차\nL2: 자료를 확인한다.")

    def test_rejects_unsupported_document_and_validates_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "scan.pdf"
            document.write_bytes(b"not parsed")
            unsupported = number_document(document)
            text_document = Path(directory) / "guide.txt"
            text_document.write_text("담당자가 자료를 검토한다.\n순서대로 결과를 기록한다.", encoding="utf-8")
            numbered = prepare_documents([text_document])

        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["warning"], "[확인 필요: 텍스트 추출 실패, 원본 수동 확인]")
        valid_report = "(문서 근거: guide.txt, L1~L2 '자료를 검토한다.')"
        invalid_report = "(문서 근거: guide.txt, L1~L1 '없는 발췌')"
        self.assertEqual(validate_excerpts(numbered, valid_report), [])
        self.assertTrue(validate_excerpts(numbered, invalid_report))


class ClassificationTests(unittest.TestCase):
    def test_c_type_precedes_other_conditions(self):
        conditions = {
            "1": {"status": "충족", "citation_valid": True},
            "2": {"status": "충족", "citation_valid": True},
            "3": {"status": "충족", "citation_valid": True},
            "4": {"status": "충족", "citation_valid": True, "judgment_point": "대상 판정"},
            "5": {"status": "충족", "citation_valid": True},
            "6": {"status": "충족", "citation_valid": True},
        }
        self.assertEqual(classify_conditions(conditions)["classification_proposal"], "C형")

    def test_overlapping_rules_and_failed_citations_are_on_hold(self):
        overlap = {
            "1": {"status": "미충족"},
            "2": {"status": "충족", "citation_valid": True},
            "3": {"status": "충족", "citation_valid": True},
            "4": {"status": "충족", "citation_valid": True},
            "5": {"status": "충족", "citation_valid": True},
            "6": {"status": "충족", "citation_valid": True},
        }
        failed = {**overlap, "1": {"status": "충족", "citation_valid": False}}
        self.assertEqual(classify_conditions(overlap)["classification_proposal"], "판정 보류")
        self.assertIn("인용 대조 실패", classify_conditions(failed)["classification_reason"])

    def test_a_b1_b2_and_judgment_none(self):
        base = {str(number): {"status": "미충족"} for number in range(1, 7)}
        a = {**base, "3": {"status": "충족"}, "4": {"status": "미충족"}, "6": {"status": "충족"}}
        b1 = {**base, "3": {"status": "충족"}, "4": {"status": "충족", "judgment_point": "승인 대상"}, "5": {"status": "충족"}}
        b2 = {**base, "2": {"status": "충족"}}
        none = {
            **a,
            "4": {"status": "미충족", "reason": "판단 없음 확인", "citation_valid": True},
        }
        self.assertEqual(classify_conditions(a)["classification_proposal"], "A형")
        self.assertEqual(classify_conditions(b1)["classification_proposal"], "B1형")
        self.assertEqual(classify_conditions(b2)["classification_proposal"], "B2형")
        self.assertEqual(classify_conditions(none)["judgment_intervention_point"], "없음")


class ScoringTests(unittest.TestCase):
    def test_frequency_conversion_and_range_lower_bound(self):
        self.assertEqual(annual_frequency("매일"), {"low": 260.0, "high": 260.0})
        self.assertEqual(annual_frequency("주 2~3회"), {"low": 104.0, "high": 156.0})
        self.assertEqual(annual_frequency("사건 기반 월평균 2건"), {"low": 24.0, "high": 24.0})

    def test_grade_ceiling_savings_and_confirmation_filter(self):
        evidence = [{"source_type": "utterance", "q": "Q1", "quote": "확인된 근거"}]
        card = {
            "card_id": "A사-영업-01-01",
            "task_name": "영업실적 집계",
            "classification": "A형",
            "engineer_confirmation": "A형",
            "frequency": "주 1회",
            "duration_minutes": 30,
            "criteria": {
                "procedure_clarity": {"score": 5, "evidence": evidence},
                "data_accessibility": {"score": 5, "evidence": evidence},
                "error_impact": {"score": 2, "evidence": evidence},
            },
        }
        result = score_cards({"cards": [card, {**card, "card_id": "unconfirmed", "engineer_confirmation": ""}]})
        row = result["ranking"][0]
        self.assertEqual(row["grade_proposal"], "검토 후 착수")
        self.assertEqual(row["total_score"], 16)
        self.assertAlmostEqual(row["annual_savings"]["low_hours"], 26)
        self.assertIn("사람 승인 필수", row["notes"][0])
        self.assertEqual(result["ranking"][1]["status"], "채점 제외: 엔지니어가 A형 또는 B1형으로 확정하지 않음")


class SharedValidationTests(unittest.TestCase):
    def test_validates_utterance_memo_and_document_quotes(self):
        transcript = "Q1 면담자: 주기는 언제인가요?\nQ1 담당자: 매주 월요일에 합니다.\nM1: 메일로 결과를 공유한다."
        documents = {
            "documents": [
                {"filename": "guide.txt", "status": "ready", "numbered_text": "L1: 승인 후 처리한다.\nL2: 결과를 보관한다."}
            ]
        }
        self.assertEqual(
            validate_evidence(
                {"source_type": "utterance", "q": "Q1", "speaker": "담당자", "quote": "매주 월요일에 합니다"},
                transcript,
                documents,
            ),
            [],
        )
        self.assertEqual(
            validate_evidence({"source_type": "memo", "m": "M1", "quote": "메일로 결과를 공유한다"}, transcript),
            [],
        )
        self.assertEqual(
            validate_evidence(
                {"source_type": "document", "filename": "guide.txt", "start_line": 1, "end_line": 2, "quote": "결과를 보관한다"},
                documents=documents,
            ),
            [],
        )
        self.assertTrue(
            validate_evidence(
                {"source_type": "utterance", "q": "Q1", "speaker": "담당자", "quote": "매일 합니다"},
                transcript,
            )
        )

    def test_fills_unknown_field_and_detects_identifiers(self):
        card = {"fields": {"1": "업무", "3": "[확인 필요: 빈도]", "7": "[확인 필요: 영향]"}}
        self.assertEqual(fill_unconfirmed_fields(card)["field_10_unconfirmed"], ["[확인 필요: 빈도]", "[확인 필요: 영향]"])
        self.assertTrue(validate_pii("담당자 연락처 010-1234-5678"))
        self.assertEqual(validate_pii("A사 고객사 업무 보고"), [])

    def test_confirmation_gate_uses_plan_values(self):
        self.assertIsNotNone(confirmation_error("design-doc", {"engineer_confirmation": ""}))
        self.assertIsNone(confirmation_error("design-doc", {"engineer_confirmation": "승인"}))
        self.assertIsNotNone(confirmation_error("report", {"engineer_confirmation": ""}))
        self.assertIsNone(confirmation_error("report", {"engineer_confirmation": "수치 확정"}))

    def test_pseudonymization_replaces_mapped_values_and_blocks_residual_pii(self):
        clean, errors = sanitize_text(
            "김철수 차장이 010-1234-5678로 연락했고, 금액은 500,000원입니다.",
            {"김철수": "담당자01", "010-1234-5678": "[연락처1]", "500,000원": "[금액1]"},
        )
        self.assertIn("담당자01", clean)
        self.assertIn("[연락처1]", clean)
        self.assertFalse(errors)
        _, residual = sanitize_text("연락처는 010-9876-5432 입니다.")
        self.assertTrue(residual)


class FinalStageTests(unittest.TestCase):
    def test_savings_formula_handles_ranges_and_missing_measurements(self):
        result = estimate_savings({"low": 10, "high": 20}, {"low": 30, "high": 60}, 0.5)
        self.assertEqual(result["low_hours"], 2.5)
        self.assertEqual(result["high_hours"], 10)
        self.assertEqual(estimate_savings(None, 30, 0.2)["status"], "unavailable")

    def test_design_doc_gate_and_required_approval(self):
        sections = {
            "1": {"agent_name": "업무 에이전트", "purpose": "초안", "user": "담당자", "run_time": "매주"},
            "2": {"must_do": "자료 정리", "must_not_do": "승인 없는 발송"},
            "3": {"workflow": [{"who": "에이전트", "what": "초안 작성", "tool": "파일"}], "human_approval": ""},
            "4": {"tools": ["파일"], "integration": "파일"},
            "5": {"common_rules": "", "additional_rules": []},
            "6": {"input_format": "CSV", "output_format": "Markdown", "example": "[확인 필요: 예시]"},
            "7": {"success_definition": "[확인 필요: 성공 정의]", "measurement": "[확인 필요: 측정 방법]"},
            "8": {"risks": ["오류"], "responses": ["사람 확인"]},
            "9": {"unknowns": [], "special_notes": []},
        }
        result = build_design_document({"sections": sections, "card": {"classification": "B1형", "error_impact_score": 3}})
        self.assertTrue(result["mandatory_approval"])
        self.assertIn("사람 승인 후 실행", result["markdown"])
        self.assertEqual(result["unknown_field_count"], 3)
        self.assertEqual(result["quality_gate"], "")

        sections["1"]["purpose"] = "[확인 필요: 목적]"
        sections["1"]["user"] = "[확인 필요: 사용자]"
        sections["2"]["must_do"] = "[확인 필요: 역할]"
        sections["2"]["must_not_do"] = "[확인 필요: 제외 업무]"
        sections["3"]["workflow"] = "[정보 부족: 2단계 보강 필요]"
        sections["4"]["integration"] = "[확인 필요: 연동 방식]"
        gated = build_design_document({"sections": sections, "card": {"classification": "B1형", "error_impact_score": 3}})
        self.assertGreaterEqual(gated["unknown_field_count"], 6)
        self.assertIn("2단계 인터뷰 보강 권고", gated["quality_gate"])

    def test_report_tags_values_and_checks_feedback_quote(self):
        savings = estimate_savings(52, 30, 0.25)
        result = render_report(
            {
                "source": "poc.csv",
                "feedback_source": "보고서가 빨라졌어요.",
                "metrics": [{"name": "성공률", "target": 0.9, "actual": 0.8, "source": "poc.csv"}],
                "feedback": [{"category": "긍정", "quote": "보고서가 빨라졌어요.", "source": "F1"}],
                "savings": savings,
                "executive_summary": "성과를 확인했습니다.",
            }
        )
        self.assertFalse(result["quote_errors"])
        self.assertIn("0.9 (목표)", result["markdown"])
        self.assertIn("0.8 (실측)", result["markdown"])
        self.assertIn("19.5~19.5시간 (추정)", result["markdown"])

    def test_task_card_json_merges_classifies_and_renders_evidence(self):
        evidence = [{"source_type": "utterance", "q": "Q1", "speaker": "담당자", "quote": "매주 월요일에 CRM에서 실적을 내려받아 엑셀에 입력해요"}]
        extracted = {
            "cards": [
                {
                    "card_id": "A사-영업-01-01",
                    "task_name": "영업 실적 리포트",
                    "extraction_basis": {"trigger": "매주", "action": "집계", "object": "영업 실적"},
                    "fields": {
                        "1": "영업 실적 리포트", "2": "", "3": "빈도: 주 1회", "4": "CRM",
                        "5": "CRM 조회 후 엑셀 작성 / 매뉴얼·양식: 있음", "6": "없음", "7": "[확인 필요: 실수 영향]",
                        "8": "", "9": "원문", "10": "",
                    },
                    "field_evidence": {"3": evidence, "4": evidence, "5": evidence, "6": evidence, "7": [], "9": evidence},
                }
            ],
            "document_candidates": [],
        }
        conditions = {
            "cards": [{
                "card_id": "A사-영업-01-01",
                "conditions": {
                    "1": {"status": "미충족"}, "2": {"status": "미충족"},
                    "3": {"status": "충족", "citation_valid": True},
                    "4": {"status": "미충족"}, "5": {"status": "미충족"},
                    "6": {"status": "충족", "citation_valid": True},
                },
            }]
        }
        combined = merge_results(extracted, conditions)
        markdown = render_task_card(combined, "interview.txt", "A사-영업-01-1회차", "v1")
        self.assertIn("A형 (판정 조건표 참조)", markdown)
        self.assertIn("발화 근거: Q1 '매주 월요일에 CRM에서 실적을 내려받아 엑셀에 입력해요'", markdown)
        self.assertIn("[확인 필요: 실수 영향]", markdown)

    def test_scoring_table_renders_deterministic_rows(self):
        document = score_cards({"source": "cards.md", "interview_id": "I1", "execution_version": "v3", "cards": [{
            "card_id": "A사-영업-01-01", "task_name": "주간 영업 보고", "classification": "A형",
            "engineer_confirmation": "A형", "frequency": "매일", "duration_minutes": 30,
            "criteria": {
                "procedure_clarity": {"score": 5, "evidence": [{"quote": "절차 근거"}]},
                "data_accessibility": {"score": 5, "evidence": [{"quote": "파일 근거"}]},
                "error_impact": {"score": 5, "evidence": [{"quote": "오류 영향 근거"}]},
            },
        }]})
        markdown = render_scoring_table(document)
        self.assertIn("주간 영업 보고", markdown)
        self.assertIn("즉시 착수", markdown)
        self.assertIn("130.0시간/년 (추정)", markdown)


if __name__ == "__main__":
    unittest.main()
