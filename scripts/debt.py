#!/usr/bin/env python3
"""
debt.py — report or clear this session's row in the session-debt file(s).

WHY A SCRIPT OWNS THE CLEAR (SYN-087, observed live on a cold trial): the
recorder in hooks/pre_tool_use.py upserts this session's row on every
file-write TOOL call. A close that cleared its own row with a file-edit tool
— or made any write-tool call after the clear — silently restored the row,
so the close reported "cleared" while the next boot reported phantom debt
from a session that had closed properly. A script's file writes are not tool
calls, so clearing by script is ordering-proof: run it anywhere in the close
and the clear is final no matter what was edited before it.

Usage:
  python scripts/debt.py [--dir ROOT]                    # report rows — no writes
  python scripts/debt.py --clear [--dir ROOT] [--session ID]

--session defaults to $CLAUDE_CODE_SESSION_ID; with neither, --clear refuses
(exit 2) rather than guess whose row to delete. The clear walks EVERY
.session_debt.tsv under the root (skipping .git and friends) — a nested repo
that is itself an ARCH instance keeps its own debt file, and a session that
edited both left a row in each — and ALSO covers the ARCH_DEBT_FILE override
path when that variable is set, because the recorder honors it and a clear
that did not would reintroduce the exact failure this script closes. Exit 0
on success and on nothing-to-do — bookkeeping must never block a close.

The rewrite is ATOMIC (tmp + os.replace, the recorder's own pattern) and
operates on BYTES: rows are matched as raw `sid\t` prefixes, so a hand-edited
file carrying non-UTF-8 bytes neither crashes the clear nor gets its other
rows re-encoded — everything that is not this session's row is preserved
byte for byte.
"""

import argparse
import os
import sys

DEBT_FILENAME = ".session_debt.tsv"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def find_debt_files(root):
    """Every debt file under `root`, bounded by the skip list, plus the
    ARCH_DEBT_FILE override when set — the recorder writes there instead, so a
    walk that ignored it would clear nothing while reporting success."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if DEBT_FILENAME in filenames:
            found.append(os.path.join(dirpath, DEBT_FILENAME))
    override = os.environ.get("ARCH_DEBT_FILE")
    if override:
        override = os.path.abspath(override)
        if os.path.isfile(override) and not any(
                os.path.normcase(override) == os.path.normcase(f) for f in found):
            found.append(override)
    return found


def clear_own_row(path, sid):
    """Delete `sid`'s row from one debt file. Returns True if a row was removed.

    Byte-level and atomic, deliberately: everything that is not this session's
    row — the header, other sessions' rows, malformed or non-UTF-8 lines — is
    preserved byte-for-byte, and the rewrite goes through tmp + os.replace so
    a concurrent reader never sees a truncated file and a crash mid-write
    cannot destroy rows. (A concurrent recorder rewrite can still interleave —
    neither writer takes a lock — but the window no longer includes an empty
    file, which was the row-destroying case.)"""
    with open(path, "rb") as fh:
        lines = fh.read().splitlines(keepends=True)
    prefix = sid.encode("utf-8") + b"\t"
    kept = [ln for ln in lines if not ln.startswith(prefix)]
    if len(kept) == len(lines):
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(b"".join(kept))
        os.replace(tmp, path)            # atomic on the same filesystem
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return True


def _display_rows(path):
    """Data lines of one debt file, decoded for DISPLAY only (replace errors —
    the file itself is never rewritten through this decoding)."""
    with open(path, "rb") as fh:
        for raw in fh.read().splitlines():
            ln = raw.decode("utf-8", "replace")
            if ln.strip() and not ln.startswith("#"):
                yield ln


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Report or clear this session's row in .session_debt.tsv.")
    ap.add_argument("--dir", default=".",
                    help="instance root to search under (default: cwd)")
    ap.add_argument("--clear", action="store_true",
                    help="delete this session's row from every debt file under "
                         "--dir (default: report only, write nothing)")
    ap.add_argument("--session", default=None,
                    help="session id whose row to clear "
                         "(default: $CLAUDE_CODE_SESSION_ID)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.abspath(args.dir)
    files = find_debt_files(root)

    if not args.clear:
        if not files:
            print("no %s under %s" % (DEBT_FILENAME, root))
            return 0
        for f in files:
            print(f)
            try:
                for ln in _display_rows(f):
                    print("  " + ln)
            except OSError as exc:
                print("  (unreadable: %s)" % exc)
        return 0

    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        print("ERROR: no session id — pass --session or set CLAUDE_CODE_SESSION_ID. "
              "Refusing to guess whose row to delete.")
        return 2

    if not files:
        print("no %s under %s — nothing to clear" % (DEBT_FILENAME, root))
        return 0
    cleared_any = False
    for f in files:
        try:
            hit = clear_own_row(f, sid)
            cleared_any = cleared_any or hit
            print("%s: %s" % (f, "own row cleared" if hit else "no own row"))
        except (OSError, UnicodeError) as exc:
            # Reported, not raised: a bookkeeping failure must not block a close.
            print("%s: could not update (%s)" % (f, exc))

    # THE ID-MISMATCH CASE MUST BE LOUD (observed live in a nested claude run):
    # the recorder keys rows by the hook payload's session id, while this tool
    # defaults to $CLAUDE_CODE_SESSION_ID — and a nested or scripted session
    # INHERITS the parent's value, so the clear targets a row that does not
    # exist while the real row sits one line away. "no own row" alone reads
    # like success; naming the survivors turns it into an actionable miss.
    if not cleared_any:
        leftover = []
        for f in files:
            try:
                leftover += [ln.split("\t")[0] for ln in _display_rows(f)]
            except OSError:
                pass
        if leftover:
            print("NOTE: cleared nothing for session id %r, but %d unwrapped row(s) "
                  "remain: %s" % (sid, len(leftover), ", ".join(leftover)))
            print("      If one of these is THIS session (a nested or scripted run "
                  "inherits the parent's $CLAUDE_CODE_SESSION_ID), re-run with "
                  "--session <that id>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ═══ EOF debt.py ═══
