"""CLI behavior, JSON output shape, exit codes, and report rendering."""
import json
import re
import shutil
from pathlib import Path

import pytest

from ctxwaste import __version__
from ctxwaste.cli import main
from ctxwaste.report import _chart, render_report, use_color

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo_transcript.jsonl"


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_text_report_plain_when_piped(capsys):
    # capsys stdout is not a TTY -> colors auto-disable
    code, out, err = run(capsys, str(FIX / "claude_code.jsonl"))
    assert code == 0 and err == ""
    assert "\x1b[" not in out
    assert out.startswith("ctxwaste %s" % __version__)
    for needle in ("Waste breakdown", "duplicate_content",
                   "repeated_reads", "oversized_tool_results", "stale_carry",
                   "Context growth per turn", "Top offenders",
                   "Recommendations"):
        assert needle in out
    assert re.search(r"≈ \d+% of context sent was dead weight \(≈[\d,]+ tokens\)",
                     out)
    # chart bars stay within 60 columns
    bars = [l.split("|", 1)[1] for l in out.splitlines() if "|" in l and re.match(r"\s*\d+ \|", l)]
    assert bars and all(len(b.split()[0]) <= 60 for b in bars)


def test_multiple_files(capsys):
    code, out, _ = run(capsys, str(FIX / "claude_code.jsonl"),
                       str(FIX / "generic.jsonl"))
    assert code == 0
    assert out.count("ctxwaste %s" % __version__) == 2


def test_json_output_shape_and_validity(capsys):
    code, out, _ = run(capsys, "--json", str(FIX / "claude_code.jsonl"))
    assert code == 0
    data = json.loads(out)  # must be valid JSON on stdout only
    assert data["ctxwaste_version"] == __version__
    f = data["files"][0]
    assert set(f) == {
        "path", "format", "turns", "skipped_lines", "unique_context_tokens",
        "total_input_tokens_sent", "waste", "top_offenders", "recommendations",
        "per_turn_cumulative_tokens"}
    assert f["format"] == "claude-code"
    assert f["turns"] == 29
    assert set(f["waste"]) == {"duplicate_content", "repeated_reads",
                               "oversized_tool_results", "stale_carry", "total"}
    for entry in f["waste"].values():
        assert set(entry) == {"tokens", "pct"}
    assert 0 <= len(f["top_offenders"]) <= 5
    assert 3 <= len(f["recommendations"]) <= 5
    assert f["per_turn_cumulative_tokens"][-1] == f["unique_context_tokens"]


def test_max_turns_truncates(capsys):
    code, out, _ = run(capsys, "--json", "--max-turns", "5",
                       str(FIX / "claude_code.jsonl"))
    assert code == 0
    assert json.loads(out)["files"][0]["turns"] == 5


def test_missing_file_exits_2(capsys):
    code, out, err = run(capsys, "does_not_exist.jsonl")
    assert code == 2
    assert "no such file" in err


def test_no_args_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2


def test_bad_max_turns_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--max-turns", "0", str(FIX / "generic.jsonl")])
    assert e.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "ctxwaste %s" % __version__ in capsys.readouterr().out


def test_scan_ranks_worst_offenders(tmp_path, capsys):
    nested = tmp_path / "proj" / "deep"
    nested.mkdir(parents=True)
    shutil.copy(FIX / "claude_code.jsonl", nested / "a.jsonl")
    shutil.copy(FIX / "generic.jsonl", tmp_path / "b.jsonl")
    (tmp_path / "notes.txt").write_text("not a transcript")
    code, out, err = run(capsys, "--scan", str(tmp_path))
    assert code == 0 and err == ""
    assert "scan of" in out and "a.jsonl" in out and "b.jsonl" in out
    assert "notes.txt" not in out
    # claude fixture (oversized result) should rank above the small generic one
    assert out.index("a.jsonl") < out.index("b.jsonl")


def test_scan_json(tmp_path, capsys):
    shutil.copy(FIX / "generic.jsonl", tmp_path / "b.jsonl")
    code, out, _ = run(capsys, "--scan", str(tmp_path), "--json")
    assert code == 0
    data = json.loads(out)
    assert data["scan_dir"] == str(tmp_path)
    assert len(data["files"]) == 1


def test_scan_missing_dir_exits_2(capsys):
    code, out, err = run(capsys, "--scan", "/definitely/not/a/dir")
    assert code == 2
    assert "scan directory not found" in err


def test_scan_empty_dir(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    code, out, err = run(capsys, "--scan", str(tmp_path / "empty"))
    assert code == 0 and err == ""
    assert "No *.jsonl transcripts found" in out


def test_malformed_file_still_reports(capsys):
    code, out, _ = run(capsys, str(FIX / "malformed_mixed.jsonl"))
    assert code == 0
    assert "Turns     3 (6 skipped lines)" in out


def test_demo_transcript_report(capsys):
    if not DEMO.exists():
        pytest.skip("demo transcript not generated yet")
    code, out, err = run(capsys, str(DEMO))
    assert code == 0 and err == ""
    assert "claude-code" in out
    assert "dead weight" in out


class _FakeTTY:
    def isatty(self):
        return True


def test_use_color_rules(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(_FakeTTY()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(_FakeTTY()) is False
    monkeypatch.setenv("NO_COLOR", "")
    assert use_color(_FakeTTY()) is True


def test_use_color_requires_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    class NotTTY:
        def isatty(self):
            return False

    assert use_color(NotTTY()) is False


def test_chart_samples_long_transcripts():
    lines = _chart(list(range(1, 101)), color=False)
    assert len(lines) <= 41  # 40 bars + sampling note
    assert any("showing" in l for l in lines)
    for line in lines[:-1]:
        bar = line.split("|", 1)[1].strip().split()[0]
        assert len(bar) <= 60


def test_render_report_color_mode_contains_ansi():
    from ctxwaste.metrics import audit
    from ctxwaste.parse import parse_file
    res = audit("x", parse_file(FIX / "generic.jsonl"))
    out = render_report(res, color=True)
    assert "\x1b[1m" in out
    plain = render_report(res, color=False)
    assert "\x1b[" not in plain
