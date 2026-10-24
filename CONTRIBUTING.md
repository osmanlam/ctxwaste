# Contributing to ctxwaste

Thanks for helping make agent transcripts lighter.

## Dev setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## Ground rules

- **Python 3.9+, stdlib only.** `ctxwaste` has zero runtime dependencies; do not
  add one. `pytest` is the only dev dependency.
- **100% offline.** No network calls, ever — not for telemetry, not for docs.
- **Never crash on weird input.** Malformed transcript lines are counted in
  `skipped_lines`, never raised.
- **Deterministic.** Every metric must be reproducible; if you add a heuristic,
  fix its constants at module level and document it in the README.

## Adding a transcript format

1. Add a fixture under `tests/fixtures/` that exercises the format (including
   malformed lines).
2. Extend the auto-detection in `src/ctxwaste/parse.py`.
3. Add tests for detection and round-tripping before touching metrics.
4. Update the README format table.

## Pull requests

- Keep the diff focused; one feature or fix per PR.
- Run `pytest -q` on Python 3.9 if you can — CI runs 3.9 through 3.13.
- Conventional commit messages are appreciated but not enforced.

## Reporting bugs

Open an issue with the `ctxwaste --json` output on a minimized transcript
(redact anything sensitive first).
