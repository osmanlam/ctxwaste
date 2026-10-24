"""Transcript parsing for ctxwaste.

Two record shapes are auto-detected per line (or per element of a single
top-level JSON array file):

``claude-code``
    ``{"type": "user" | "assistant", "message": {"role": ..., "content":
    str | [blocks]}}`` where blocks are ``{"type": "text" | "tool_use" |
    "tool_result", ...}``.

``generic``
    ``{"role": "user" | "assistant" | "system" | "tool", "content":
    str | [blocks]}``.

Any line that is not a dict matching one of those shapes is counted in
``Transcript.skipped_lines`` — parsing never raises on malformed input.
All token counts use the chars//4 approximation (see README).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

#: Input keys (checked in order) used to extract a file path from tool_use input.
PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")
CLAUDE_TURN_TYPES = ("user", "assistant")


@dataclass
class ToolUse:
    name: str = ""
    input_str: str = ""  # compact, deterministic string of the input payload
    path: str = ""       # extracted file path ("" if none)
    use_id: str = ""
    line: int = 0
    seq: int = 0        # global encounter order, for use/result pairing


@dataclass
class ToolResult:
    text: str = ""
    tool_use_id: str = ""
    line: int = 0
    seq: int = 0


@dataclass
class Message:
    role: str = ""
    text: str = ""
    tool_uses: List[ToolUse] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    line: int = 0
    fmt: str = ""  # "claude-code" or "generic"


@dataclass
class Transcript:
    messages: List[Message] = field(default_factory=list)
    skipped_lines: int = 0
    formats: List[str] = field(default_factory=list)

    @property
    def format(self) -> str:
        seen = sorted(set(self.formats))
        if not seen:
            return "unknown"
        return seen[0] if len(seen) == 1 else "mixed"


def est_tokens(text: str) -> int:
    """Estimated tokens for *text*: chars // 4 (documented approximation)."""
    return len(text) // 4


def message_tokens(m: Message) -> int:
    """Estimated tokens for a whole message (text + tool uses + results)."""
    chars = len(m.text)
    chars += sum(len(u.name) + len(u.input_str) for u in m.tool_uses)
    chars += sum(len(r.text) for r in m.tool_results)
    return chars // 4


def normalize(text: str) -> str:
    """Strip and collapse all whitespace — the canonical form for duplicates."""
    return " ".join(text.split())


def compact(obj: Any) -> str:
    """Compact, deterministic string form of a JSON-ish value."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


def parse_file(path: Any) -> Transcript:
    """Parse a transcript file: JSONL lines, or a single top-level JSON array."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    if raw.lstrip().startswith("["):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            # line numbers fall back to element position for array files
            return parse_records((i + 1, rec) for i, rec in enumerate(data))
    return parse_records(enumerate(raw.splitlines(), 1))


def parse_records(records: Iterable[Tuple[int, Any]]) -> Transcript:
    """Parse ``(line_number, record)`` pairs into a Transcript.

    Records may be already-parsed objects (JSON array files) or raw JSON
    strings (JSONL lines); unparsable strings count as skipped lines.
    """
    tr = Transcript()
    for line_no, rec in records:
        if isinstance(rec, str):
            try:
                rec = json.loads(rec)
            except ValueError:
                tr.skipped_lines += 1
                continue
        msg = _parse_record(rec)
        if msg is None:
            tr.skipped_lines += 1
        else:
            msg.line = line_no
            tr.messages.append(msg)
            tr.formats.append(msg.fmt)
    seq = 0
    for m in tr.messages:  # stable order for pairing: uses before results
        for u in m.tool_uses:
            u.line, u.seq = m.line, seq
            seq += 1
        for r in m.tool_results:
            r.line, r.seq = m.line, seq
            seq += 1
    return tr


def _parse_record(rec: Any) -> Optional[Message]:
    if not isinstance(rec, dict):
        return None
    if "message" in rec:  # claude-code shape
        rtype = rec.get("type")
        if rtype is not None and rtype not in CLAUDE_TURN_TYPES:
            return None
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return None
        role = msg.get("role") or rtype or "user"
        content = msg.get("content")
        fmt = "claude-code"
    elif "role" in rec:  # generic shape
        role = rec.get("role")
        content = rec.get("content")
        fmt = "generic"
    else:
        return None
    m = Message(role=str(role), fmt=fmt)
    m.text, m.tool_uses, m.tool_results = _content(m.role, content)
    if fmt == "generic":  # OpenAI-style tool replies carry the id on the record
        call_id = str(rec.get("tool_call_id") or "")
        for r in m.tool_results:
            if not r.tool_use_id and call_id:
                r.tool_use_id = call_id
    return m


def _content(role: str, content: Any) -> Tuple[str, List[ToolUse], List[ToolResult]]:
    text_parts: List[str] = []
    uses: List[ToolUse] = []
    results: List[ToolResult] = []
    if isinstance(content, str):
        # plain-string content on a "tool" role message is a tool result
        if role == "tool":
            results.append(ToolResult(text=content))
        else:
            text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                _block(block, text_parts, uses, results)
    elif isinstance(content, dict):
        _block(content, text_parts, uses, results)
    return "\n".join(p for p in text_parts if p), uses, results


def _block(block: dict, text_parts: List[str], uses: List[ToolUse],
           results: List[ToolResult]) -> None:
    btype = block.get("type")
    if btype == "text":
        t = block.get("text")
        if isinstance(t, str) and t.strip():
            text_parts.append(t)
    elif btype == "tool_use":
        raw_input = block.get("input", {})
        uses.append(ToolUse(
            name=str(block.get("name") or ""),
            input_str=compact(raw_input),
            path=_path(raw_input),
            use_id=str(block.get("id") or ""),
        ))
    elif btype == "tool_result":
        c = block.get("content", "")
        results.append(ToolResult(
            text=c if isinstance(c, str) else compact(c),
            tool_use_id=str(block.get("tool_use_id") or ""),
        ))


def _path(raw_input: Any) -> str:
    if not isinstance(raw_input, dict):
        return ""
    for key in PATH_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
