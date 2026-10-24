"""Tests for each waste metric and the re-send cost model."""
from pathlib import Path

import pytest

from ctxwaste.metrics import OVERSIZED_TOOL_RESULT_TOKENS, audit
from ctxwaste.parse import Message, ToolResult, ToolUse, Transcript, parse_file

FIX = Path(__file__).parent / "fixtures"


def msg(role="user", text="", uses=(), results=(), line=0):
    m = Message(role=role, text=text, line=line)
    m.tool_uses = [ToolUse(name=n, input_str=i, path=p, use_id=uid, line=line)
                   for (n, i, p, uid) in uses]
    m.tool_results = [ToolResult(text=t, tool_use_id=uid, line=line)
                      for (t, uid) in results]
    return m


def tr(*messages, skipped=0):
    return Transcript(messages=list(messages), skipped_lines=skipped,
                      formats=[m.fmt or "generic" for m in messages])


def test_resend_cost_model():
    res = audit("t", tr(
        msg("user", "x" * 400, line=1),
        msg("assistant", "y" * 400, line=2),
        msg("user", "z" * 400, line=3),
        msg("assistant", "w" * 400, line=4),
    ))
    # assistant turn 2 re-sends 100; assistant turn 4 re-sends 300
    assert res["total_input_tokens_sent"] == 400
    assert res["unique_context_tokens"] == 400
    assert res["per_turn_cumulative_tokens"] == [100, 200, 300, 400]


def test_duplicate_content_counts_occurrences_after_first():
    body = "A" * 16 + " " + "B" * 23  # 40 chars -> 10 tokens, >= min length
    dup = "  " + "A" * 16 + "\n\n" + "B" * 23 + "\t"  # same normalized text
    res = audit("t", tr(
        msg("user", text=body, line=1),
        msg("assistant", text="one ok", line=2),
        msg("user", text=dup, line=3),
        msg("assistant", text="two ok", line=4),
    ))
    d = res["waste"]["duplicate_content"]
    assert d["tokens"] == 10  # one repeat of a 10-token text
    assert res["top_offenders"][0]["kind"] == "duplicate_content"
    assert res["top_offenders"][0]["line_number"] == 3


def test_duplicate_result_size_times_repeats():
    body = "Z" * 200  # 50 tokens
    res = audit("t", tr(
        msg("assistant", uses=[("Read", '{}', "/f", "u1")], line=1),
        msg("user", results=[(body, "u1")], line=2),
        msg("assistant", uses=[("Read", '{}', "/f", "u2")], line=3),
        msg("user", results=[(body, "u2")], line=4),
        msg("assistant", uses=[("Read", '{}', "/f", "u3")], line=5),
        msg("user", results=[(body, "u3")], line=6),
    ))
    assert res["waste"]["duplicate_content"]["tokens"] == 100  # 50 x 2 repeats


def test_repeated_reads_count_repeat_result_sizes():
    res = audit("t", tr(
        msg("assistant", uses=[("Read", '{}', "/f", "u1")], line=1),
        msg("user", results=[("a" * 400, "u1")], line=2),   # 100 tok, first read
        msg("assistant", uses=[("Read", '{}', "/f", "u2")], line=3),
        msg("user", results=[("b" * 800, "u2")], line=4),   # 200 tok, repeat
        msg("assistant", uses=[("Read", '{}', "/f", "u3")], line=5),
        msg("user", results=[("c" * 400, "u3")], line=6),   # 100 tok, repeat
    ))
    assert res["waste"]["repeated_reads"]["tokens"] == 300
    assert any(o["kind"] == "repeated_read" for o in res["top_offenders"])


def test_different_files_not_repeated_reads():
    res = audit("t", tr(
        msg("assistant", uses=[("Read", '{}', "/a", "u1")], line=1),
        msg("user", results=[("a" * 400, "u1")], line=2),
        msg("assistant", uses=[("Read", '{}', "/b", "u2")], line=3),
        msg("user", results=[("b" * 400, "u2")], line=4),
    ))
    assert res["waste"]["repeated_reads"]["tokens"] == 0


def test_oversized_threshold_is_strictly_greater():
    exact = "x" * (OVERSIZED_TOOL_RESULT_TOKENS * 4)          # exactly 2000 tok
    over = "y" * (OVERSIZED_TOOL_RESULT_TOKENS * 4 + 4)       # 2001 tok
    base = tr(
        msg("assistant", uses=[("Bash", '{}', "", "u1")], line=1),
        msg("user", results=[(exact, "u1")], line=2),
    )
    assert audit("t", base)["waste"]["oversized_tool_results"]["tokens"] == 0
    res = audit("t", tr(
        msg("assistant", uses=[("Bash", '{}', "", "u1")], line=1),
        msg("user", results=[(over, "u1")], line=2),
    ))
    assert res["waste"]["oversized_tool_results"]["tokens"] == 2001
    off = [o for o in res["top_offenders"] if o["kind"] == "oversized_tool_result"]
    assert off and off[0]["est_tokens"] == 2001 and off[0]["line_number"] == 2


def _filler(n=10):  # short words only: never eligible for stale-carry probes
    return "ok fine " * n


MARKER = "unreferenced_distinctive_marker_"  # 32 chars, one distinctive word
MARKER_TEXT = (MARKER + " ") * 7             # 7 probe-able words, ~57 tokens


def _stale_transcript(referenced=False):
    later_text = ("see " + MARKER) if referenced else "moving right along here"
    return tr(
        msg("user", text=MARKER_TEXT, line=1),
        msg("assistant", text=_filler(), line=2),
        msg("user", text=later_text, line=3),
        msg("assistant", text=_filler(), line=4),
    )


def test_stale_carry_detected_when_never_referenced():
    res = audit("t", _stale_transcript(referenced=False))
    assert res["waste"]["stale_carry"]["tokens"] == len(MARKER_TEXT) // 4
    assert res["top_offenders"][0]["kind"] == "stale_carry"


def test_stale_carry_exemption_when_referenced_later():
    res = audit("t", _stale_transcript(referenced=True))
    assert res["waste"]["stale_carry"]["tokens"] == 0


def test_stale_carry_exempts_last_30_percent_and_tiny_messages():
    marker = "unreferenced_distinctive_marker_"
    res = audit("t", tr(
        msg("user", text=_filler(), line=1),
        msg("assistant", text=_filler(), line=2),
        msg("user", text=_filler(), line=3),
        msg("assistant", text=marker * 7, line=4),  # in last 30% of 5 turns
        msg("user", text=marker * 7, line=5),       # in last 30%
    ))
    assert res["waste"]["stale_carry"]["tokens"] == 0
    tiny = audit("t", tr(
        msg("user", text="distinctive_words_only tiny", line=1),  # < 20 tok
        msg("assistant", text=_filler(), line=2),
        msg("user", text=_filler(), line=3),
    ))
    assert tiny["waste"]["stale_carry"]["tokens"] == 0


def test_recommendations_are_3_to_5_data_driven_strings():
    res = audit("t", tr(
        msg("user", text="please examine " + "unreferenced_distive_body " * 8, line=1),
        msg("assistant", uses=[("Read", '{}', "/f", "u1")], line=2),
        msg("user", results=[("a" * 8000, "u1")], line=3),
        msg("assistant", uses=[("Read", '{}', "/f", "u2")], line=4),
        msg("user", results=[("a" * 8000, "u2")], line=5),
        msg("assistant", text="all done here", line=6),
    ))
    recs = res["recommendations"]
    assert 3 <= len(recs) <= 5
    assert all(isinstance(r, str) and r for r in recs)
    joined = " ".join(recs)
    assert "re-read" in joined or "Compacting" in joined
    assert "Tool Read" in joined or "Tool Bash" in joined


def test_top_offenders_capped_at_five_and_sorted():
    texts = [("f" * 400, "u%d" % i) for i in range(7)]
    messages = [msg("user", results=[(t, uid)], line=i + 1)
                for i, (t, uid) in enumerate(texts)]
    messages.append(msg("assistant", text="end", line=99))
    res = audit("t", tr(*messages))
    assert len(res["top_offenders"]) <= 5
    ests = [o["est_tokens"] for o in res["top_offenders"]]
    assert ests == sorted(ests, reverse=True)
    assert set(res["top_offenders"][0]) == \
        {"kind", "est_tokens", "line_number", "preview"}
    assert all(len(o["preview"]) <= 60 for o in res["top_offenders"])


def test_waste_percentages_of_total_input():
    body = "A" * 40
    res = audit("t", tr(
        msg("user", text=body, line=1),
        msg("assistant", text=body, line=2),
        msg("user", text=body, line=3),
        msg("assistant", text="C" * 40, line=4),
    ))
    sent = res["total_input_tokens_sent"]
    assert sent > 0
    for entry in res["waste"].values():
        assert entry["pct"] == pytest.approx(
            entry["tokens"] * 100.0 / sent, abs=0.06)


def test_fixture_audit_all_four_waste_kinds():
    res = audit(str(FIX / "claude_code.jsonl"),
                parse_file(FIX / "claude_code.jsonl"))
    w = res["waste"]
    assert w["duplicate_content"]["tokens"] > 0
    assert w["repeated_reads"]["tokens"] > 0
    assert w["oversized_tool_results"]["tokens"] > OVERSIZED_TOOL_RESULT_TOKENS
    assert res["turns"] == 29
