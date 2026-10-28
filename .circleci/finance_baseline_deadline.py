# Copyright (c) 2026, CCPC and contributors
"""Fail-closed cumulative wall-clock bound for an *approved* disposable Finance CI job.

Only used by the manual, feature-branch eight-test baseline workflow.
This does NOT enforce a CircleCI billing/credit limit or cover container boot
or any unconditional starter-validation job. No runtime approval is implied.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

MAX_SECONDS = 5400  # 90-minute safety ceiling; NOT an approved spend allowance.
KILL_GRACE_SECONDS = 5
STATE = Path.home() / ".ccpc-finance-baseline-deadline"


def _read_remaining():
    try:
        raw = STATE.read_text(encoding="ascii").strip()
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
            raise ValueError("Invalid deadline representation")
        deadline = int(raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Finance deadline is absent or invalid") from exc
    now = time.time()
    if deadline > now + MAX_SECONDS:
        raise RuntimeError("Finance deadline exceeds allowed safety ceiling")
    remaining = deadline - now
    if remaining <= 0:
        raise RuntimeError("Finance cumulative deadline expired")
    return remaining


def start():
    # Open exclusively: a subsequent stage cannot silently restart the clock.
    now = int(time.time())
    old_mask = os.umask(0o077)
    try:
        with STATE.open("x", encoding="ascii") as deadline_file:
            deadline_file.write(str(now + MAX_SECONDS) + "\n")
    except FileExistsError as exc:
        raise RuntimeError("Finance cumulative deadline was already initialized") from exc
    finally:
        os.umask(old_mask)
    _read_remaining()


def run(argv):
    if not argv:
        raise RuntimeError("Finance deadline runner requires an explicit command")
    remaining = _read_remaining()
    child = subprocess.Popen(argv, start_new_session=True)
    try:
        code = child.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        # The child owns a process group; terminate children along with their
        # parent. An independently detached grandchild may require operator
        # cancellation at the CircleCI level.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        raise RuntimeError("Finance cumulative deadline exhausted; child stopped") from exc
    _read_remaining()
    return code


def main(argv):
    if argv == ["start"]:
        start()
    elif len(argv) >= 2 and argv[0] == "run":
        code = run(argv[1:])
        if code:
            raise SystemExit(code)
    else:
        raise RuntimeError("Usage: finance_baseline_deadline.py start | run COMMAND...")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except RuntimeError as exc:
        print("Finance CI timebox: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
