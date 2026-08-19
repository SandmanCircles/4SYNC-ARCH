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

# KEPT IN STEP with the same set in scripts/rotate.py and scripts/meter.py. This
# was a third divergent copy and the divergence was silent: `dist` and `build`
# were in the other two and missing here, so a close walked build artefacts that
# no other tool did (SYN-090). Dot-directories are pruned as a CLASS below rather
# than listed, because listing them one at a time is what left `.cache`, `.tox`,
# `.next` and `.terraform` walked — a maintainer cannot keep guessing the set.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}

# How far under the instance root to look. A debt file only ever sits at an
# instance root, so an unbounded walk of the whole tree was paying a multi-second
# cost at every close on a large adopter repo to look where the file cannot be.
# Three levels reaches any nested-instance layout and stops — the same bound and
# the same reasoning as rotate.py's MAX_REPO_DEPTH.
MAX_DEBT_DEPTH = 3


def find_debt_files(root):
    """Every debt file under `root`, plus the ARCH_DEBT_FILE override when set.

    Bounded two ways: SKIP_DIRS and the dot-directory class prune what is not
    instance state, and MAX_DEBT_DEPTH stops the descent. The override is added
    AFTER the walk and is exempt from both — the recorder writes there instead of
    the default path, so a walk that dropped it would clear nothing while
    reporting success, and it may legitimately sit outside the bounded region."""
    found = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
        if depth >= MAX_DEBT_DEPTH:
            dirnames[:] = []                    # bounded: descend no further
        else:
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
        if DEBT_FILENAME in filenames:
            found.append(os.path.join(dirpath, DEBT_FILENAME))
    override = os.environ.get("ARCH_DEBT_FILE")
    if override:
        override = os.path.abspath(override)
        if os.path.isfile(override) and not any(
                os.path.normcase(override) == os.path.normcase(f) for f in found):
            found.append(override)
    return found


def _split_lines_lf(data):
    """Split bytes into lines on \\n ONLY, keeping the terminators.

    Never bytes.splitlines(): it also splits on a lone \\r, so a foreign row
    carrying a stray 0x0D whose post-CR bytes happen to start with `sid\\t`
    would be truncated at the CR — exactly the hand-edited-file case the
    byte-preservation promise covers. CRLF endings pass through intact."""
    parts = data.split(b"\n")
    out = [p + b"\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])            # final line without trailing newline
    return out


def clear_own_row(path, sid):
    """Delete `sid`'s row from one debt file. Returns True if a row was removed.

    Byte-level and atomic, deliberately: everything that is not this session's
    row — the header, other sessions' rows, malformed or non-UTF-8 lines — is
    preserved byte-for-byte, and the rewrite goes through a UNIQUE tmp +
    os.replace so a concurrent reader never sees a truncated file and a crash
    mid-write cannot destroy rows. The tmp name carries the pid because the
    RECORDER is a second writer with the same atomic pattern: a shared fixed
    tmp name would let the two writers truncate or unlink each other's
    in-flight temp — the cross-process race the debt file exists to survive.
    (Interleaving is still possible — neither writer takes a lock — but no
    window includes an empty or half-written live file.)"""
    with open(path, "rb") as fh:
        lines = _split_lines_lf(fh.read())
    prefix = sid.encode("utf-8") + b"\t"
    kept = [ln for ln in lines if not ln.startswith(prefix)]
    if len(kept) == len(lines):
        return False
    tmp = "%s.tmp.%d" % (path, os.getpid())
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
    #
    # NAMING THEM IS NOT PERMISSION TO PICK ONE (SYN-090). This block used to end
    # "re-run with --session <that id>", which invites choosing a row from a list
    # whose other entries may belong to sessions that are LIVE RIGHT NOW — and a
    # deleted row is the only evidence that session was working, which is the one
    # thing the tracker exists to preserve. The id is not something to deduce
    # here: the boot receipt prints it whenever it differs from the environment,
    # which is exactly when this branch fires.
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
            print("      DO NOT pick one from this list. Any of these may belong to a "
                  "session that is LIVE right now, and clearing another session's row "
                  "destroys the only evidence it was working.")
            print("      Your own id is on your BOOT RECEIPT — it prints the payload id "
                  "whenever it differs from $CLAUDE_CODE_SESSION_ID, which is exactly "
                  "the case that lands you here. Re-run with that id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ═══ EOF debt.py ═══
