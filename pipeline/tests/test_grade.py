"""Tests for the iRule judge-output parsing + schema validation. These run the
gauntlet a real LLM judge response goes through before a training pair is kept,
so they pin the robustness that earned bugs in the past (fenced blocks, prose
around JSON, braces inside strings, loose verdict/score types)."""

import json

from irule.grade import parse_grade_output, validate_result

GOOD = {
    "scores": {"factual": 5, "linguistic": 4, "domain": 5, "overall": 4.5},
    "verdict": "PASS",
    "feedback": "solid",
    "tags": ["accurate"],
}


class TestParseGradeOutput:
    def test_fenced_json_block(self):
        text = "Here is my grade:\n```json\n" + json.dumps(GOOD) + "\n```\nDone."
        assert parse_grade_output(text) == GOOD

    def test_fenced_block_without_json_tag(self):
        text = "```\n" + json.dumps(GOOD) + "\n```"
        assert parse_grade_output(text) == GOOD

    def test_raw_json_no_fence(self):
        assert parse_grade_output(json.dumps(GOOD)) == GOOD

    def test_prose_around_object_balanced_brace(self):
        text = f"Sure! My assessment is {json.dumps(GOOD)} — hope that helps."
        assert parse_grade_output(text) == GOOD

    def test_braces_inside_strings_do_not_break_extraction(self):
        obj = {
            "scores": {"factual": 5, "linguistic": 5, "domain": 5, "overall": 5},
            "verdict": "pass",
            "feedback": "use the syntax { ... } carefully",
        }
        text = f"blah {json.dumps(obj)} blah"
        assert parse_grade_output(text) == obj

    def test_first_object_with_scores_wins(self):
        decoy = '{"note": "no scores here"}'
        text = decoy + "\n" + json.dumps(GOOD)
        assert parse_grade_output(text) == GOOD

    def test_returns_none_when_no_scores_object(self):
        assert parse_grade_output("I cannot grade this. Sorry!") is None
        assert parse_grade_output('{"verdict": "pass"}') is None  # has no "scores"


class TestValidateResult:
    def test_valid_result_is_normalized(self):
        out = validate_result(GOOD)
        assert out is not None
        # scores coerced to float
        assert out["scores"] == {"factual": 5.0, "linguistic": 4.0, "domain": 5.0, "overall": 4.5}
        assert out["verdict"] == "pass"  # lowercased
        assert out["tags"] == ["accurate"]
        assert out["missing_facts"] == []  # defaulted
        assert set(out) == {"scores", "verdict", "feedback", "notes", "tags", "missing_facts"}

    def test_missing_score_key_rejected(self):
        bad = {**GOOD, "scores": {"factual": 5, "linguistic": 4, "domain": 5}}  # no 'overall'
        assert validate_result(bad) is None

    def test_non_numeric_score_rejected(self):
        bad = {**GOOD, "scores": {"factual": "high", "linguistic": 4, "domain": 5, "overall": 4}}
        assert validate_result(bad) is None

    def test_bad_verdict_rejected(self):
        assert validate_result({**GOOD, "verdict": "maybe"}) is None

    def test_scores_not_a_dict_rejected(self):
        assert validate_result({**GOOD, "scores": [1, 2, 3]}) is None

    def test_string_tags_coerced_to_list(self):
        out = validate_result({**GOOD, "tags": "single"})
        assert out is not None and out["tags"] == ["single"]

    def test_non_list_missing_facts_defaulted(self):
        out = validate_result({**GOOD, "missing_facts": "oops"})
        assert out is not None and out["missing_facts"] == []

    def test_non_dict_input_rejected(self):
        assert validate_result("not a dict") is None
        assert validate_result(None) is None
