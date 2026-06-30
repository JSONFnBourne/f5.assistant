"""Tests for the iRule rulebook token extraction — the tokens these functions
mine from chunks become the allow-list that grading enforces, so their precision
is what keeps invalid commands/events out of the training data."""

import json

from irule.rulebook import (
    DEFAULT_OPERATORS,
    Rulebook,
    discover_events,
    extract_command_tokens,
    extract_events_from_text,
    extract_operator_tokens,
    load_rulebook,
    write_rulebook,
)


class TestExtractCommandTokens:
    def test_finds_namespace_commands(self):
        text = "Use HTTP::redirect and TCP::collect, then SSL::cipher."
        assert extract_command_tokens(text) == {"HTTP::redirect", "TCP::collect", "SSL::cipher"}

    def test_requires_uppercase_namespace(self):
        # the namespace before :: must start uppercase — lowercase is not a command
        assert extract_command_tokens("http::redirect lowercased") == set()

    def test_allows_hyphen_and_underscore_in_command(self):
        assert "X509::verify_cert_error_string" in extract_command_tokens(
            "[X509::verify_cert_error_string]"
        )

    def test_no_commands_in_plain_text(self):
        assert extract_command_tokens("This is prose about load balancing.") == set()


class TestDiscoverEvents:
    def test_finds_when_declarations(self):
        text = "when CLIENT_ACCEPTED {\n  log local0. hi\n}\nwhen HTTP_REQUEST {}"
        assert discover_events(text) == {"CLIENT_ACCEPTED", "HTTP_REQUEST"}

    def test_requires_uppercase_event(self):
        assert discover_events("when foo { }") == set()


class TestExtractEventsFromText:
    def test_substring_match_against_known(self):
        known = {"HTTP_REQUEST", "CLIENT_ACCEPTED", "LB_SELECTED"}
        text = "This fires during the HTTP_REQUEST event after CLIENT_ACCEPTED."
        assert extract_events_from_text(text, known) == {"HTTP_REQUEST", "CLIENT_ACCEPTED"}

    def test_unknown_events_ignored(self):
        assert extract_events_from_text("HTTP_REQUEST happens", {"LB_SELECTED"}) == set()


class TestExtractOperatorTokens:
    def test_word_and_symbol_operators(self):
        text = "if { $x contains foo and $y == $z }"
        toks = extract_operator_tokens(text, DEFAULT_OPERATORS)
        assert {"contains", "and", "=="} <= toks

    def test_word_boundary_prevents_substring_false_positive(self):
        # "and" inside "android" must NOT register as the operator
        assert "and" not in extract_operator_tokens("the android device", DEFAULT_OPERATORS)

    def test_only_known_operators_returned(self):
        # restrict the known set — "contains" present in text but not allowed
        toks = extract_operator_tokens("$a contains $b", {"and", "or"})
        assert "contains" not in toks


class TestLoadWriteRulebook:
    def test_round_trip(self, tmp_path):
        rb = Rulebook(
            commands={"HTTP::redirect", "TCP::collect"},
            events={"HTTP_REQUEST"},
            operators={"contains", "=="},
            event_order={"HTTP_REQUEST": {"order": 1}},
        )
        path = tmp_path / "rb.json"
        write_rulebook(rb, path)
        loaded = load_rulebook(path)
        assert loaded.commands == rb.commands
        assert loaded.events == rb.events
        assert loaded.operators == rb.operators
        assert loaded.event_order == rb.event_order

    def test_missing_file_yields_default_operators(self, tmp_path):
        rb = load_rulebook(tmp_path / "does_not_exist.json")
        assert rb.commands == set()
        assert rb.operators == set(DEFAULT_OPERATORS)

    def test_legacy_list_format(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(["HTTP::redirect", "TCP::collect"]))
        rb = load_rulebook(path)
        assert rb.commands == {"HTTP::redirect", "TCP::collect"}
        assert rb.operators == set(DEFAULT_OPERATORS)  # defaults filled in

    def test_empty_operators_fall_back_to_defaults(self, tmp_path):
        path = tmp_path / "no_ops.json"
        path.write_text(json.dumps({"commands": ["HTTP::redirect"], "operators": []}))
        rb = load_rulebook(path)
        assert rb.operators == set(DEFAULT_OPERATORS)
