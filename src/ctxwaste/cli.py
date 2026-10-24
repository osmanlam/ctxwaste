"""Command-line interface for ctxwaste."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, report
from .metrics import audit
from .parse import parse_file

DEFAULT_SCAN_DIR = os.path.expanduser("~/.claude/projects")

EPILOG = ("examples:\n"
          "  ctxwaste session.jsonl                 audit one transcript\n"
          "  ctxwaste a.jsonl b.jsonl --json        audit several, JSON out\n"
          "  ctxwaste --scan                        audit ~/.claude/projects\n"
          "  ctxwaste --scan ~/logs --max-turns 50  first 50 turns of each")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctxwaste",
        description="Audit AI-agent transcript JSONL files for context waste: "
                    "duplicated content, repeated file reads, oversized tool "
                    "results, stale carry, and the input tokens they re-send "
                    "every turn.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("transcripts", metavar="TRANSCRIPT.jsonl", nargs="*",
                   help="transcript file(s) to audit")
    p.add_argument("--scan", metavar="DIR", nargs="?",
                   const=DEFAULT_SCAN_DIR, default=None,
                   help="recursively audit *.jsonl under DIR "
                        "(default: %s; overrides TRANSCRIPT args)"
                        % DEFAULT_SCAN_DIR)
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.add_argument("--max-turns", metavar="N", type=int, default=None,
                   help="analyze only the first N turns (default: all)")
    p.add_argument("--version", action="version",
                   version="ctxwaste %s" % __version__)
    return p


def _audit(path: str, max_turns: int) -> dict:
    tr = parse_file(path)
    if max_turns is not None:
        tr.messages = tr.messages[:max_turns]
    return audit(path, tr)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be >= 1")
    color = report.use_color(sys.stdout)
    if args.scan is not None:
        return _run_scan(args, color)
    if not args.transcripts:
        parser.error("no transcripts given — pass TRANSCRIPT.jsonl files "
                     "or use --scan [DIR]")
    missing = [t for t in args.transcripts if not os.path.isfile(t)]
    if missing:
        for t in missing:
            print("ctxwaste: error: no such file: %s" % t, file=sys.stderr)
        return 2
    results = []
    try:
        for t in args.transcripts:
            results.append(_audit(t, args.max_turns))
    except OSError as exc:
        print("ctxwaste: error: cannot read transcript: %s" % exc,
              file=sys.stderr)
        return 2
    _emit(results, args, color)
    return 0


def _run_scan(args, color: bool) -> int:
    scan_dir = os.path.expanduser(args.scan)
    if not os.path.isdir(scan_dir):
        print("ctxwaste: error: scan directory not found: %s" % scan_dir,
              file=sys.stderr)
        return 2
    paths = sorted(str(p) for p in Path(scan_dir).rglob("*.jsonl"))
    if not paths:
        print("No *.jsonl transcripts found under %s" % scan_dir)
        return 0
    results = []
    for p in paths:
        try:
            results.append(_audit(p, args.max_turns))
        except OSError as exc:
            print("ctxwaste: warning: skipping unreadable file %s: %s"
                  % (p, exc), file=sys.stderr)
    if not results:
        print("ctxwaste: error: no readable transcripts under %s" % scan_dir,
              file=sys.stderr)
        return 2
    results.sort(key=lambda r: (-r["waste"]["total"]["pct"], r["path"]))
    if args.json:
        print(report.render_json(
            {"ctxwaste_version": __version__, "scan_dir": scan_dir,
             "files": results}))
    else:
        print(report.render_scan(scan_dir, results[:10], len(results), color))
    return 0


def _emit(results, args, color: bool) -> None:
    if args.json:
        print(report.render_json(
            {"ctxwaste_version": __version__, "files": results}))
    else:
        print("\n\n".join(report.render_report(r, color) for r in results))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
