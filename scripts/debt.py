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
.session_debt.tsv under the root (skipping .git and friends), because a nested
repo that is itself an ARCH instance keeps its own debt file, and a session
that edited both left a row in each. Exit 0 on success and on nothing-to-do —
bookkeeping must never block a close.
"""

import argparse
import os
import sys

DEBT_FILENAME = ".session_debt.tsv"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def find_debt_files(root):
    """Every debt file under `root`, bounded by the skip list."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if DEBT_FILENAME in filenames:
            found.append(os.path.join(dirpath, DEBT_FILENAME))
    return found


def clear_own_row(path, sid):
    """Delete `sid`'s row from one debt file. Returns True if a row was removed.

    Everything that is not this session's row — the header, other sessions'
    rows, even malformed lines — is preserved byte-for-byte: this tool has
    exactly one opinion, and it is about one row."""
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    kept = [ln for ln in lines if not ln.startswith(sid + "\t")]
    if len(kept) == len(lines):
        return False
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(kept)
    return True


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
            with open(f, encoding="utf-8") as fh:
                for ln in fh:
                    if ln.strip() and not ln.startswith("#"):
                        print("  " + ln.rstrip("\n"))
        return 0

    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        print("ERROR: no session id — pass --session or set CLAUDE_CODE_SESSION_ID. "
              "Refusing to guess whose row to delete.")
        return 2

    if not files:
        print("no %s under %s — nothing to clear" % (DEBT_FILENAME, root))
        return 0
    for f in files:
        try:
            print("%s: %s" % (f, "own row cleared" if clear_own_row(f, sid)
                              else "no own row"))
        except OSError as exc:
            # Reported, not raised: a bookkeeping failure must not block a close.
            print("%s: could not update (%s)" % (f, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ═══ EOF debt.py ═══
