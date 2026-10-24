"""Render text and JSON reports for ctxwaste."""
from __future__ import annotations

import json
import os
from typing import IO, List

from . import __version__
from .metrics import WASTE_KINDS

BOLD, DIM, RED, GREEN, YELLOW, CYAN = "1", "2", "31", "32", "33", "36"

#: Longest bar in the per-turn chart, in terminal columns.
CHART_MAX_WIDTH = 60
#: Max chart rows before even sampling (deterministic).
CHART_MAX_ROWS = 40


def use_color(stream: IO) -> bool:
    """ANSI color only on a TTY, and never when NO_COLOR is set (non-empty)."""
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _c(code: str, s: str, on: bool) -> str:
    return "\033[%sm%s\033[0m" % (code, s) if on else s


def render_report(res: dict, color: bool = False) -> str:
    """Full text report for one audited transcript."""
    w = res["waste"]
    out: List[str] = []
    out.append(_c(BOLD, "ctxwaste %s — context waste report" % __version__, color))
    out.append("-" * 64)
    skip = " (%d skipped lines)" % res["skipped_lines"] if res["skipped_lines"] else ""
    out.append("File      %s" % res["path"])
    out.append("Format    %s" % res["format"])
    out.append("Turns     %d%s" % (res["turns"], skip))
    out.append("Context   %s tokens (final cumulative context)"
               % f"{res['unique_context_tokens']:,}")
    out.append("Re-sent   %s tokens (sum of context per assistant turn — "
               "the re-send cost model)" % f"{res['total_input_tokens_sent']:,}")
    out.append("")
    out.append(_c(BOLD, "Waste breakdown", color))
    out.append("  %-26s%12s%14s" % ("kind", "tokens", "% of re-sent"))
    for kind in WASTE_KINDS:
        out.append(_waste_row(kind, w[kind], color))
    out.append("  " + "-" * 52)
    out.append(_waste_row("total", w["total"], color, bold=True))
    out.append("")
    out.append(_c(BOLD, "Context growth per turn", color)
               + _c(DIM, "  (tokens = chars/4, an approximation)", color))
    out.extend(_chart(res["per_turn_cumulative_tokens"], color))
    out.append("")
    if res["top_offenders"]:
        out.append(_c(BOLD, "Top offenders", color))
        for i, o in enumerate(res["top_offenders"], 1):
            out.append("  %d. %-24s%9s tok  line %4d  %s"
                       % (i, o["kind"], f"{o['est_tokens']:,}",
                          o["line_number"], _c(YELLOW, o["preview"], color)))
    else:
        out.append("Top offenders: none")
    out.append("")
    if res["recommendations"]:
        out.append(_c(BOLD, "Recommendations", color))
        for rec in res["recommendations"]:
            out.append("  - %s" % rec)
    total = w["total"]
    out.append("")
    out.append(_c(BOLD, "≈ %d%% of context sent was dead weight (≈%s tokens)"
                  % (int(round(total["pct"])), f"{total['tokens']:,}"), color))
    return "\n".join(out)


def _waste_row(kind: str, entry: dict, color: bool, bold: bool = False) -> str:
    pct = entry["pct"]
    tone = BOLD if bold else (RED if pct >= 5 else YELLOW if pct >= 1 else GREEN)
    return _c(tone, "  %-26s%12s%13.1f%%"
              % (kind, f"{entry['tokens']:,}", pct), color)


def _chart(cumulative: List[int], color: bool) -> List[str]:
    """ASCII horizontal bar chart of cumulative context tokens per turn."""
    if not cumulative:
        return ["  (no turns)"]
    rows = list(enumerate(cumulative, 1))
    note = ""
    if len(rows) > CHART_MAX_ROWS:
        step = (len(rows) - 1) / float(CHART_MAX_ROWS - 1)
        picked = sorted({round(i * step) for i in range(CHART_MAX_ROWS)})
        rows = [rows[i] for i in picked]
        note = "  (showing %d of %d turns)" % (len(rows), len(cumulative))
    peak = max(cumulative)
    num_w = len(f"{peak:,}")
    lines = []
    for turn, value in rows:
        filled = int(round(value / float(peak) * CHART_MAX_WIDTH)) if peak else 0
        if value and filled < 1:
            filled = 1
        lines.append("%5d |%s %s%s"
                     % (turn, _c(CYAN, "#" * filled, color),
                        f"{value:,}".rjust(num_w), ""))
    if note:
        lines.append(_c(DIM, note.strip(), color))
    return lines


def render_scan(scan_dir: str, results: List[dict], found: int,
                color: bool = False) -> str:
    """Ranked 'worst offenders' table for --scan."""
    out: List[str] = [
        _c(BOLD, "ctxwaste %s — scan of %s" % (__version__, scan_dir), color),
        "%d transcript(s) audited — top %d by waste %%"
        % (found, len(results)),
        "",
        _c(BOLD, "%4s  %8s  %10s  %12s  %5s  %s"
           % ("rank", "waste %", "waste tok", "re-sent tok", "turns",
              "transcript"), color),
    ]
    for rank, r in enumerate(results, 1):
        w = r["waste"]["total"]
        out.append("%4d  %7.1f%%  %10s  %12s  %5d  %s"
                   % (rank, w["pct"], f"{w['tokens']:,}",
                      f"{r['total_input_tokens_sent']:,}", r["turns"], r["path"]))
    return "\n".join(out)


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
