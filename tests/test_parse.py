"""Tests for transcript parsing and format auto-detection."""
from pathlib import Path

import pytest

from ctxwaste.parse import est_tokens, normalize, parse_file, parse_records

FIX = Path(__file__).parent / "fixtures"


def test_claude_code_format_detection():
    tr = parse_file(FIX / "claude_code.jsonl")
    assert tr.format == "claude-code"
    assert len(tr.messages) == 29
    assert tr.skipped_lines == 0
    assert all(m.fmt == "claude-code" for m in tr.messages)


def test_claude_code_tool_extraction():
    tr = parse_file(FIX / "claude_code.jsonl")
    uses = [u for m in tr.messages for u in m.tool_uses]
    on_path = [u for u in uses if u.path == "/app/checkout.py"]
    reads = [u for u in on_path if u.name == "Read"]
    assert len(reads) == 3
    assert [u.name for u in on_path if u.name != "Read"] == ["Edit"]  # same path counts too
    assert any(u.name == "Grep" for u in uses)
    results = [r for m in tr.messages for r in m.tool_results]
    assert any(est_tokens(r.text) > 2000 for r in results)
    # ids survive for use/result pairing
    assert {r.tool_use_id for r in results} >= {"tu01", "tu02", "tu10"}


def test_generic_format():
    tr = parse_file(FIX / "generic.jsonl")
    assert tr.format == "generic"
    assert len(tr.messages) == 13
    assert tr.skipped_lines == 0
    roles = [m.role for m in tr.messages]
    assert roles[0] == "system"
    # plain-string content on a "tool" role message becomes a tool result
    tool_msgs = [m for m in tr.messages if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].tool_results
    assert tool_msgs[0].text == ""
    # assistant string content stays text
    assert any(m.role == "assistant" and m.text and not m.tool_results
               for m in tr.messages)


def test_generic_json_array_file():
    tr = parse_file(FIX / "generic_array.json")
    assert tr.format == "generic"
    assert len(tr.messages) == 5
    assert tr.skipped_lines == 0


def test_malformed_mixed_is_counted_not_fatal():
    tr = parse_file(FIX / "malformed_mixed.jsonl")
    assert tr.skipped_lines == 6
    assert len(tr.messages) == 3
    assert tr.format == "mixed"


def test_parse_records_handles_scalars_and_nulls():
    tr = parse_records([
        (1, 42), (2, None), (3, [1, 2]), (4, "str"),
        (5, {"role": "user", "content": "real"}),
    ])
    assert tr.skipped_lines == 4
    assert len(tr.messages) == 1
    assert tr.messages[0].text == "real"


def test_empty_file():
    tr = parse_records([])
    assert tr.messages == [] and tr.skipped_lines == 0
    assert tr.format == "unknown"


def test_normalize_and_est_tokens():
    assert normalize("  a \n b\t c  ") == "a b c"
    assert est_tokens("x" * 400) == 100
    assert est_tokens("") == 0


def test_claude_non_turn_types_skipped():
    tr = parse_records([
        (1, {"type": "summary", "summary": "note"}),
        (2, {"type": "user", "message": {"role": "user", "content": "hi"}}),
        (3, {"message": "not a dict"}),
    ])
    assert tr.skipped_lines == 2
    assert len(tr.messages) == 1


def test_tool_result_block_content_variants():
    # string content, list-of-blocks content, missing content
    tr = parse_records([
        (1, {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "plain"},
            {"type": "tool_result", "tool_use_id": "b",
             "content": [{"type": "text", "text": "nested"}]},
        ]}),
    ])
    texts = [r.text for r in tr.messages[0].tool_results]
    assert texts == ["plain", '[{"text":"nested","type":"text"}]']
