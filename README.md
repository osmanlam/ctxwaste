<div align="center">

# ctxwaste

**Find the dead weight in your AI agent transcripts.**

`ctxwaste` audits agent conversation logs (JSONL) and tells you what was
rotting in the context window: duplicated content, oversized tool results,
files the agent re-read over and over, and early turns that nothing ever
referenced again — with the number of input tokens each one re-sent
on **every single turn**.

[![CI](https://img.shields.io/badge/CI-passing-brightgreen)](https://github.com/osmanlam/ctxwaste/actions)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/ctxwaste/)
[![License: MIT](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-success)](pyproject.toml)

*100% offline · zero runtime dependencies · works with any JSONL transcript*

</div>

---

```console
$ ctxwaste examples/demo_transcript.jsonl

ctxwaste 0.1.0 — context waste report
----------------------------------------------------------------
File      examples/demo_transcript.jsonl
Format    claude-code
Turns     29
Context   4,823 tokens (final cumulative context)
Re-sent   29,222 tokens (sum of context per assistant turn — the re-send cost model)

Waste breakdown
  kind                            tokens  % of re-sent
  duplicate_content                  138          0.5%
  repeated_reads                     688          2.4%
  oversized_tool_results           2,818          9.6%
  stale_carry                      3,680         12.6%
  ----------------------------------------------------
  total                            7,324         25.1%

Context growth per turn  (tokens = chars/4, an approximation)
    1 |#    27
   ...
   20 |#######################   1,871
   21 |########################################################## 4,689
   ...
   29 |############################################################ 4,823

Top offenders
  1. oversized_tool_result       2,818 tok  line   21  /var/log/payments/deploy.log (tail -n 2000) ...
  2. stale_carry                 2,818 tok  line   21  /var/log/payments/deploy.log ...
  3. repeated_read                 365 tok  line    6  /src/payments/service.py (read 4x) ...

Recommendations
  - Compacting after turn 21 would have avoided ~14,720 re-sent tokens.
  - Tool Bash results total 3,128 tokens across 4 calls — truncate or summarize its output.
  - File /src/payments/service.py was re-read 4 times (688 tokens re-fetched) — batch edits or trust the first read.
  - Context grew from 27 to 4,823 tokens over 29 turns — compact periodically to cap re-send cost.

≈ 25% of context sent was dead weight (≈7,324 tokens)
```

## Why

Every turn of an agent session re-sends the whole conversation. A single
9,000-token log dump that nothing references again is not a one-time cost —
it is a tax paid on **every subsequent turn** until the session ends.
That compounding is why long agent sessions get slow, expensive, and
increasingly confused: the useful signal drowns in stale context
([Anthropic on effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
`ctxwaste` measures that tax from the transcripts you already have on disk.

## Install

```console
$ pipx install ctxwaste        # recommended
$ pip install ctxwaste         # or
$ git clone https://github.com/osmanlam/ctxwaste && pip install -e ./ctxwaste
```

## Usage

```console
$ ctxwaste transcript.jsonl                # audit one or more transcripts
$ ctxwaste --scan                          # rank every ~/.claude/projects/*.jsonl by waste
$ ctxwaste --scan ~/codex/sessions         # rank transcripts in any directory
$ ctxwaste --json transcript.jsonl         # machine-readable report (CI-friendly)
$ ctxwaste --max-turns 20 transcript.jsonl # analyse only the first 20 turns
```

Formats are auto-detected per line: **Claude Code** session JSONL
(`{"type": ..., "message": ...}`) and a **generic** shape
(`{"role": ..., "content": ...}`, JSONL or a JSON array). Unknown lines are
skipped and counted, never fatal.

## How it computes waste

All token counts are `chars // 4` — a rough, honest approximation. The
**re-send cost model**: for each assistant turn, the cumulative context so
far is what got sent to the model; summing those gives *total re-sent
tokens*, the denominator for every percentage.

| Metric | Rule (v0.1 heuristic) |
|---|---|
| `duplicate_content` | normalised text/tool-result identical to an earlier one; every repeat re-counts |
| `repeated_reads` | the same file path passed to a tool more than once; each re-read counts the tool result it fetched again |
| `oversized_tool_results` | a single tool result above `OVERSIZED_TOOL_RESULT_TOKENS` (2,000 est. tokens); the excess counts as waste |
| `stale_carry` | a turn older than the last 30% of the session whose distinctive words never appear in any later turn — carried to the end, referenced never |

These are heuristics, deliberately simple and deterministic. They find the
big, unambiguous wins — not philosophical debates about relevance.

## Compared to

| | [ccusage](https://github.com/ryoppippi/ccusage) | [cc-audit](https://github.com/pa-arth/cc-audit) | **ctxwaste** |
|---|---|---|---|
| What it reports | spend | spend + waste signals | **waste + what to do about it** |
| Transcript formats | Claude Code | Claude Code | any JSONL (auto-detected) |
| Re-send cost model | — | — | ✔ per-turn cumulative |
| Recommendations | — | partial | ✔ |
| Dependencies | node | python | **0** (stdlib only) |

## Roadmap

- [ ] OpenAI / Codex session format detection
- [ ] `--watch` mode for live sessions
- [ ] cost in `$` per model pricing table
- [ ] `--fix`: emit a compacted transcript proposal

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with an
anonymised transcript snippet get fixed fastest.

## License

[MIT](LICENSE) © 2026 osmanlam
