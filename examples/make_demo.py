#!/usr/bin/env python3
"""Deterministically build examples/demo_transcript.jsonl.

Fixed seed, stdlib only: running this script twice produces byte-identical
output. The transcript is a synthetic 29-turn Claude Code session that
contains every kind of waste ctxwaste detects — a file read three times,
a duplicated tool result, one oversized tool result, and stale content
that is carried (and re-sent) to the very end.
"""
import json
import random
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent / "demo_transcript.jsonl"


def user(i, content):
    return {"type": "user", "uuid": "demo_u_%03d" % i,
            "message": {"role": "user", "content": content}}


def asst(i, content):
    return {"type": "assistant", "uuid": "demo_a_%03d" % i,
            "message": {"role": "assistant", "content": content}}


def text(t):
    return {"type": "text", "text": t}


def use(name, inputs, n):
    return {"type": "tool_use", "id": "toolu_demo_%02d" % n, "name": name,
            "input": inputs}


def res(n, text):
    return {"type": "tool_result", "tool_use_id": "toolu_demo_%02d" % n,
            "content": text}


def service_py(note, n_funcs):
    lines = ["# payments/service.py", "import logging", "",
             "logger = logging.getLogger(__name__)", "", note]
    for i in range(n_funcs):
        lines += [
            "def process_refund_%02d(order_id, gateway_txn_ref, amount):" % i,
            "    idempotency_key = 'refund_%02d' %% order_id" % i,
            "    if amount <= 0:",
            "        raise ValueError('invalid_amount')",
            "    return settle(gateway_txn_ref, amount, idempotency_key)",
            "",
        ]
    return "\n".join(lines)


def test_py(n_funcs):
    lines = ["# payments/test_service.py", "import pytest", ""]
    for i in range(n_funcs):
        lines += [
            "def test_settle_retry_%02d(order, assert_retry_budget):" % i,
            "    with mock_gateway(healthy=(%d %% 2 == 0)):" % i,
            "        assert settle_once(order) <= assert_retry_budget",
            "",
        ]
    return "\n".join(lines)


def grep_out():
    return "\n".join(
        "service.py:%d: dispatch_refund_task(order) queued retry_hint=transient"
        % (120 + i) for i in range(7))


def router_py():
    return "\n".join(
        ["# payments/router.py", ""] +
        ["def route_%d(payload): return proxy_pass_upstream(payload, shard=%d)" % (i, i)
         for i in range(5)])


def pytest_out():
    return "\n".join([
        "============================= test session starts ==============================",
        "platform darwin -- Python 3.12.4, pytest-8.2.0",
        "collected 61 items",
        "test_service.py ..F..F. [ 18%]",
        "test_router.py ...... [ 45%]",
        "=================================== FAILURES ===================================",
        "_____________ test_gateway_acks _____________",
        "assert dispatch_count == 3, dispatch_count was 2",
        "_____________ test_webhook_sig _____________",
        "TimeoutError: gateway did not ack within 30s",
        "========================= 2 failed, 59 passed in 14.23s =========================",
    ])


def pytest_out_dup():
    # identical tokens, different whitespace: duplicates after normalization
    return "\n\n".join("   " + l + "\t" for l in pytest_out().splitlines())


def pytest_out_fixed():
    return ("collected 61 items\n0 failed, 61 passed in 11.08s\n"
            "slowest: test_gateway_acks 3.9s")


def deploy_log():
    lines = ["/var/log/payments/deploy.log (tail -n 2000)"]
    for i in range(160):
        stamp = "2026-10-21T09:%02d:%02d" % (i // 60, i % 60)
        latency = random.randint(18, 120)
        lines.append("%s INFO worker heartbeat ok status=200 latency_ms=%d" % (stamp, latency))
    return "\n".join(lines)


def build():
    svc = service_py("# revision: original", 5)
    svc2 = service_py("# revision: with hotfix", 6)
    svc3 = service_py("# revision: stable", 5)
    r = []
    r.append(user(1, "Fix the flaky refund tests in the payments service. Read /src/payments/service.py first, then run the suite."))
    r.append(asst(2, [text("Reading the service file now."), use("Read", {"file_path": "/src/payments/service.py"}, 1)]))
    r.append(user(3, [res(1, svc)]))
    r.append(asst(4, [text("Now the tests."), use("Read", {"file_path": "/src/payments/test_service.py"}, 2)]))
    r.append(user(5, [res(2, test_py(4))]))
    r.append(asst(6, [text("Let me re-check the refund path in service.py."), use("Read", {"file_path": "/src/payments/service.py"}, 3)]))
    r.append(user(7, [res(3, svc2)]))
    r.append(asst(8, [text("Where is retry used?"), use("Grep", {"pattern": "retry", "path": "/src/payments"}, 4)]))
    r.append(user(9, [res(4, grep_out())]))
    r.append(asst(10, [text("Checking the router too."), use("Read", {"file_path": "/src/payments/router.py"}, 5)]))
    r.append(user(11, [res(5, router_py())]))
    r.append(asst(12, [text("Running the suite."), use("Bash", {"command": "pytest -q /src/payments"}, 6)]))
    r.append(user(13, [res(6, pytest_out())]))
    r.append(asst(14, "The retry helper swallows transient errors on the first attempt; problems only surface on the second pass."))
    r.append(asst(15, [text("Re-reading service.py to be safe."), use("Read", {"file_path": "/src/payments/service.py"}, 7)]))
    r.append(user(16, [res(7, svc3)]))
    r.append(asst(17, [text("Running it again to confirm."), use("Bash", {"command": "pytest -q /src/payments"}, 8)]))
    r.append(user(18, [res(8, pytest_out_dup())]))
    r.append(user(19, "Also check the deploy log — it looked huge yesterday."))
    r.append(asst(20, [text("Pulling the log."), use("Bash", {"command": "tail -n 2000 /var/log/payments/deploy.log"}, 9)]))
    r.append(user(21, [res(9, deploy_log())]))
    r.append(asst(22, "The deploy log is mostly worker heartbeat noise; nothing actionable in it."))
    r.append(user(23, "OK. Now patch the retry loop and re-run."))
    r.append(asst(24, [text("Patching."), use("Edit", {"file_path": "/src/payments/service.py", "old_string": "raise ValueError", "new_string": "log.warning"}, 10)]))
    r.append(user(25, [res(10, "The file /src/payments/service.py has been updated. (1 replacement)")]))
    r.append(user(26, "Run the suite again."))
    r.append(asst(27, [text("Re-running."), use("Bash", {"command": "pytest -q /src/payments"}, 11)]))
    r.append(user(28, [res(11, pytest_out_fixed())]))
    r.append(asst(29, "Done. The retry loop now surfaces transient dispatch errors immediately; the suite is 61/61 green."))
    return r


def main():
    records = build()
    OUT.write_text(
        "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records),
        encoding="utf-8")
    print("wrote %d turns to %s (%d bytes)" % (len(records), OUT, OUT.stat().st_size))


if __name__ == "__main__":
    main()
