"""Waste metrics over a parsed transcript.

All heuristics are deterministic; constants are module-level and documented
in the README. The four waste kinds overlap by design (one tool result can be
oversized *and* duplicated *and* stale) — the total is a sum of the parts,
which is conservative re: the re-send model (each item is counted once, not
once per turn it was re-sent).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .parse import (Message, ToolUse, Transcript, est_tokens,
                    message_tokens, normalize)

#: A single tool_result above this many estimated tokens is "oversized".
OVERSIZED_TOOL_RESULT_TOKENS = 2000
#: Messages within the last STALE_EXEMPT_FRACTION of turns are never stale.
STALE_EXEMPT_FRACTION = 0.30
#: Messages smaller than this (estimated tokens) are never stale-carry.
STALE_MIN_TOKENS = 20
#: A "reference" is a >=8-char whitespace-delimited word appearing in a later message.
STALE_MIN_SUBSTRING = 8
#: How many distinctive (longest) words to probe per message.
STALE_MAX_PROBES = 6
#: Normalized texts shorter than this are never counted as duplicates.
MIN_DUPLICATE_CHARS = 16

WASTE_KINDS = ("duplicate_content", "repeated_reads",
               "oversized_tool_results", "stale_carry")


def audit(path: str, tr: Transcript) -> dict:
    """Compute the full waste report dict for one transcript."""
    msgs = tr.messages
    sizes = [message_tokens(m) for m in msgs]
    cumulative: List[int] = []
    running = 0
    for size in sizes:
        running += size
        cumulative.append(running)
    # Re-send cost model: every assistant turn re-sends the whole context
    # accumulated before it. Sum those prefixes.
    sent = sum(sum(sizes[:i]) for i, m in enumerate(msgs) if m.role == "assistant")

    pairs = _pairs(msgs)
    d_waste, d_off, d_groups = _duplicates(msgs)
    r_waste, r_off, r_groups = _repeated_reads(msgs, pairs)
    o_waste, o_off = _oversized(msgs)
    s_waste, s_off, s_items = _stale(msgs, sizes)

    def pct(tokens: int) -> float:
        return round(tokens * 100.0 / sent, 1) if sent else 0.0

    waste = {
        "duplicate_content": {"tokens": d_waste, "pct": pct(d_waste)},
        "repeated_reads": {"tokens": r_waste, "pct": pct(r_waste)},
        "oversized_tool_results": {"tokens": o_waste, "pct": pct(o_waste)},
        "stale_carry": {"tokens": s_waste, "pct": pct(s_waste)},
    }
    total = d_waste + r_waste + o_waste + s_waste
    waste["total"] = {"tokens": total, "pct": pct(total)}

    offenders = sorted(d_off + r_off + o_off + s_off,
                       key=lambda o: (-o["est_tokens"], o["line_number"]))[:5]
    recs = _recommendations(msgs, cumulative, d_groups, d_waste, pairs,
                            r_groups, s_items)
    return {
        "path": str(path),
        "format": tr.format,
        "turns": len(msgs),
        "skipped_lines": tr.skipped_lines,
        "unique_context_tokens": cumulative[-1] if cumulative else 0,
        "total_input_tokens_sent": sent,
        "waste": waste,
        "top_offenders": offenders,
        "recommendations": recs,
        "per_turn_cumulative_tokens": cumulative,
    }


def _preview(text: str, width: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= width else text[: width - 3] + "..."


def _duplicates(msgs: List[Message]) -> Tuple[int, List[dict], List[Tuple[int, int]]]:
    """Normalized tool_result/message texts appearing more than once; every
    occurrence after the first counts (tokens = size x repeats)."""
    seen: Dict[str, List[int]] = {}
    for m in msgs:
        for unit in [m.text] + [r.text for r in m.tool_results]:
            norm = normalize(unit)
            if len(norm) >= MIN_DUPLICATE_CHARS:
                seen.setdefault(norm, []).append(m.line)
    waste, offenders, groups = 0, [], []
    for norm, lines in seen.items():
        repeats = len(lines) - 1
        if repeats < 1:
            continue
        unit_tokens = est_tokens(norm)
        waste += unit_tokens * repeats
        groups.append((len(lines), unit_tokens * repeats))
        offenders.append({
            "kind": "duplicate_content",
            "est_tokens": unit_tokens * repeats,
            "line_number": lines[1],
            "preview": _preview(norm),
        })
    return waste, offenders, groups


def _pairs(msgs: List[Message]) -> List[Tuple[ToolUse, object]]:
    """Pair each tool_use with its tool_result: by id when both carry one,
    else FIFO in encounter order. Deterministic."""
    events = sorted(
        [("use", u) for m in msgs for u in m.tool_uses]
        + [("res", r) for m in msgs for r in m.tool_results],
        key=lambda e: e[1].seq)
    pending: List[ToolUse] = []
    pairs: List[Tuple[ToolUse, object]] = []
    for kind, obj in events:
        if kind == "use":
            pending.append(obj)
            continue
        match = None
        if obj.tool_use_id:
            for i, u in enumerate(pending):
                if u.use_id == obj.tool_use_id:
                    match = pending.pop(i)
                    break
        if match is None and pending:
            match = pending.pop(0)
        if match is not None:
            pairs.append((match, obj))
    return pairs


def _repeated_reads(msgs: List[Message], pairs) -> Tuple[int, List[dict], list]:
    """Same file path in tool_use inputs more than once: each repeat counts
    the size of its own tool_result as waste."""
    size_by_use = {id(use): est_tokens(res.text) for use, res in pairs}
    by_path: Dict[str, list] = {}
    for m in msgs:
        for u in m.tool_uses:
            if u.path:
                by_path.setdefault(u.path, []).append((u, size_by_use.get(id(u), 0)))
    waste, offenders, groups = 0, [], []
    for path, occs in by_path.items():
        if len(occs) < 2:
            continue
        group_waste = sum(size for _, size in occs[1:])
        waste += group_waste
        groups.append((len(occs), group_waste, path))
        for u, size in occs[1:]:
            if size:
                offenders.append({
                    "kind": "repeated_read",
                    "est_tokens": size,
                    "line_number": u.line,
                    "preview": _preview(
                        "%s (read %dx; this re-read re-fetched %s tokens)"
                        % (path, len(occs), size)),
                })
    groups.sort(key=lambda g: (-g[1], g[2]))
    return waste, offenders, groups


def _oversized(msgs: List[Message]) -> Tuple[int, List[dict]]:
    """Any single tool_result above OVERSIZED_TOOL_RESULT_TOKENS."""
    waste, offenders = 0, []
    for m in msgs:
        for r in m.tool_results:
            tokens = est_tokens(r.text)
            if tokens > OVERSIZED_TOOL_RESULT_TOKENS:
                waste += tokens
                offenders.append({
                    "kind": "oversized_tool_result",
                    "est_tokens": tokens,
                    "line_number": r.line,
                    "preview": _preview(normalize(r.text)),
                })
    return waste, offenders


def _stale(msgs: List[Message], sizes: List[int]) -> Tuple[int, List[dict], list]:
    """Heuristic: a message outside the last 30% of turns is stale-carry if
    none of its distinctive (>=8-char) words ever appears in a later message.
    Its full token estimate then counts as carried-to-the-end waste."""
    n = len(msgs)
    units = [normalize(" ".join([m.text] + [r.text for r in m.tool_results]))
             for m in msgs]
    later = [""] * n
    acc = ""
    for i in range(n - 1, -1, -1):
        later[i] = acc
        acc = ("%s %s" % (units[i], acc)).strip()
    waste, offenders, items = 0, [], []
    cutoff = n * (1.0 - STALE_EXEMPT_FRACTION)
    for i, m in enumerate(msgs):
        if i >= cutoff or sizes[i] < STALE_MIN_TOKENS:
            continue
        words = [w for w in units[i].split() if len(w) >= STALE_MIN_SUBSTRING]
        words.sort(key=len, reverse=True)
        probes = words[:STALE_MAX_PROBES]
        if not probes or any(p in later[i] for p in probes):
            continue
        waste += sizes[i]
        items.append((i, sizes[i]))
        offenders.append({
            "kind": "stale_carry",
            "est_tokens": sizes[i],
            "line_number": m.line,
            "preview": _preview(units[i]),
        })
    return waste, offenders, items


def _recommendations(msgs: List[Message], cumulative: List[int],
                     dup_groups: List[Tuple[int, int]], dup_waste: int,
                     pairs, read_groups: list, stale_items: list) -> List[str]:
    recs: List[str] = []
    if stale_items:
        last = max(i for i, _ in stale_items)
        assistant_after = sum(1 for j in range(last + 1, len(msgs))
                              if msgs[j].role == "assistant")
        avoided = sum(size for _, size in stale_items) * assistant_after
        if avoided > 0:
            recs.append("Compacting after turn %d would have avoided ~%s "
                        "re-sent tokens." % (last + 1, f"{avoided:,}"))
    tool_totals: Dict[str, List[int]] = {}
    for use, res in pairs:
        agg = tool_totals.setdefault(use.name or "unknown", [0, 0])
        agg[0] += 1
        agg[1] += est_tokens(res.text)
    if tool_totals:
        name, (calls, tokens) = max(tool_totals.items(),
                                    key=lambda kv: (kv[1][1], kv[0]))
        if tokens > 0:
            recs.append("Tool %s results total %s tokens across %d calls — "
                        "truncate or summarize its output."
                        % (name, f"{tokens:,}", calls))
    if read_groups:
        n, gwaste, path = read_groups[0]
        recs.append("File %s was re-read %d times (%s tokens re-fetched) — "
                    "batch edits or trust the first read."
                    % (path, n, f"{gwaste:,}"))
    if dup_waste:
        plural = "" if len(dup_groups) == 1 else "s"
        recs.append("%d distinct text%s appear%s more than once (%s tokens "
                    "duplicated) — dedupe repeated tool output."
                    % (len(dup_groups), plural, "" if plural else "s",
                       f"{dup_waste:,}"))
    if len(cumulative) >= 2:
        recs.append("Context grew from %s to %s tokens over %d turns — "
                    "compact periodically to cap re-send cost."
                    % (f"{cumulative[0]:,}", f"{cumulative[-1]:,}", len(msgs)))
    if len(recs) < 3:
        recs.append("No major hotspots across %d turns — keep tool results "
                    "under %s estimated tokens and avoid re-reading files."
                    % (len(msgs), f"{OVERSIZED_TOOL_RESULT_TOKENS:,}"))
    return recs[:5]
