#!/usr/bin/env python3
"""
4SYNC ARCH — ledger rotation. Run at wrap from a GIT-CAPABLE,
host-side session only (git is the undo; never run through a sandbox mount).

Does two things, both as verbatim block moves:

1. JOURNAL KEEP-N: if the '## Session journal (recent)' section of the ledger
   (MERGE_PLAN.md) holds more than N blocks (default 5), move the oldest
   (bottom) blocks to the TOP of the history file's journal area
   (JOURNAL_HISTORY.md), newest-first order preserved.

2. TASK DOCS: long-form task documents live OUTSIDE the ledger, one per row, at
   tasks/MP-0NN.md — derived from the row ID, never written down as a pointer.
   This pass does two things with them:
     - moves the document of every TERMINAL row (✅/❌) to tasks/closed/. No age
       lag: the document is not in the boot path either way, so there is nothing
       to wait for. (This supersedes the dated MERGE_PLAN_ARCHIVE.md scheme —
       when descriptions lived inline, a lag was the only way to keep recent
       context reachable. Now every document is always reachable and never in
       the boot path, so the lag bought nothing and the date-parsing heuristic
       it needed was pure risk.)
     - reports any NON-terminal row whose document is missing. A row with no
       document is a task nobody can execute — the exact failure the ledger's
       task-authoring rule exists to prevent — so this exits non-zero.

3. ABBA ARCHIVE: move 'Status: DONE' messages older than --age days (default 10)
   from ABBA.md to ABBA_ARCHIVE.md (created with a header if absent), newest-first.

4. SIZE REPORT: print the ledger's boot cost and the journal's share of it, and
   warn when the journal exceeds the manifest's close.journal.max_bytes. `keep`
   is a COUNT cap, and a single session block can run several KB — so the count
   cap alone cannot bound the journal. Reports; never blocks. ARCH capped its
   8 KB manifest and left the file that actually dominates boot unmeasured;
   this is the number that closes that asymmetry.

Safety:
  --dry-run          : print what would move; write nothing (DEFAULT unless --apply).
  --apply            : actually write.
  git-dirty refusal  : refuses to run if the target files have uncommitted changes
                       (override with --allow-dirty). Requires being inside a git repo.
  copy-verify        : after writing, re-reads both files and asserts every moved
                       block is present verbatim in the destination and absent from
                       the source; on any mismatch it restores the originals.
  atomic writes      : temp file + os.replace + fsync.

Usage:
  python scripts/rotate.py --dir /path/to/project [--keep 5] [--age 10] [--apply]
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

JOURNAL_HEAD = "## Session journal (recent)"


def _utf8_stdout():
    """Windows consoles default to cp1252, where this module's '✓'/'✅' status
    prints raise UnicodeEncodeError — AFTER an --apply has already written, so the
    exit code lies about what happened on disk.

    This runs at IMPORT, not in main(): main() alone left every other caller
    exposed, and the test suite is exactly such a caller — it failed on a cp1252
    console while passing wherever stdout happened to be UTF-8, which made the
    suite's own result depend on the terminal it ran in."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):   # detached//redirected stream
                pass


_utf8_stdout()


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def atomic_write(p, s):
    tmp = p + ".rotate_tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(s)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def git_dirty(repo_dir, paths):
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] + paths,
                             cwd=repo_dir, capture_output=True, text=True, timeout=30)
        return bool(out.stdout.strip())
    except Exception:
        return True  # can't verify => treat as dirty (refuse)


# ── journal rotation ─────────────────────────────────────────────────────────

def split_journal(ledger_text):
    """Return (before, blocks, after) where blocks are the blank-line-separated
    journal blocks inside the recent-journal section.

    Leading HTML-comment blocks are INSTRUCTIONS, not journal entries — the KEEP-N
    rule lives at the top of that section in the shipped template. Counting them as
    blocks inflated the count by one, so a section holding exactly `keep` real blocks
    looked over-cap and the oldest real block was rotated out on every close.

    They cannot simply be filtered: rotate_journal rebuilds the ledger as
    `before + blocks`, so a dropped comment would be DELETED on write-back. They move
    into `before` instead, where they survive verbatim and out of the count."""
    # anchor on the real column-0 heading line — a plain .find() matches prose
    # MENTIONS of the heading (e.g. the ledger's line-3 layout pointer) first
    h = re.search(r"^" + re.escape(JOURNAL_HEAD) + r"[ \t]*$", ledger_text, re.M)
    if not h:
        return None
    body_start = ledger_text.index("\n", h.start()) + 1
    # section ends at the next '## ' heading or '---' rule at column 0
    m = re.search(r"^(## |---\s*$)", ledger_text[body_start:], re.M)
    body_end = body_start + (m.start() if m else len(ledger_text) - body_start)
    body = ledger_text[body_start:body_end]
    blocks = [b for b in re.split(r"\n\s*\n", body) if b.strip()]

    before = ledger_text[:body_start]
    lead = []
    while blocks and blocks[0].strip().startswith("<!--") and blocks[0].strip().endswith("-->"):
        lead.append(blocks.pop(0).strip())
    if lead:
        before += "\n" + "\n\n".join(lead) + "\n\n"
    return before, blocks, ledger_text[body_end:]


def rotate_journal(ledger_path, history_path, keep, apply_):
    text = read(ledger_path)
    parts = split_journal(text)
    if not parts:
        print(f"journal: section '{JOURNAL_HEAD}' not found in {ledger_path} — skipped")
        return []
    before, blocks, after = parts
    if len(blocks) <= keep:
        print(f"journal: {len(blocks)} blocks (cap {keep}) — nothing to move")
        return []
    moved = blocks[keep:]           # oldest are at the bottom (newest-first file)
    print(f"journal: {len(blocks)} blocks — moving oldest {len(moved)} to history")
    for b in moved:
        print("  -", b.strip().splitlines()[0][:100])
    if not apply_:
        return moved
    hist = read(history_path) if os.path.exists(history_path) else "# Session journal — history (newest-first)\n"
    # insert after the first heading line of the history file, newest-first
    lines = hist.split("\n", 1)
    head, rest = lines[0], (lines[1] if len(lines) > 1 else "")
    new_hist = head + "\n\n" + "\n\n".join(b.strip() for b in moved) + "\n\n" + rest.lstrip("\n")
    new_ledger = before + "\n\n".join(b.strip() for b in blocks[:keep]) + "\n\n" + after.lstrip("\n")
    atomic_write(history_path, new_hist)
    atomic_write(ledger_path, new_ledger)
    verify_moves(moved, src=read(ledger_path), dst=read(history_path),
                 restore=[(ledger_path, text), (history_path, hist)])
    return moved


# ── ABBA archive ─────────────────────────────────────────────────────────────

MSG_HEAD = re.compile(r"^### \[\d+\] .*Status: DONE\s*$", re.M)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def split_messages(text):
    """Return list of (start, end, header) for every '### [n] …' message block."""
    heads = [m for m in re.finditer(r"^### \[\d+\][^\n]*$", text, re.M)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((m.start(), end, m.group(0)))
    return out


def rotate_abba(abba_path, archive_path, age_days, apply_):
    text = read(abba_path)
    cutoff = datetime.now() - timedelta(days=age_days)
    to_move = []
    for start, end, header in split_messages(text):
        if "Status: DONE" not in header:
            continue
        dm = DATE_RE.search(header)
        if not dm:
            continue
        try:
            when = datetime.strptime(dm.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if when < cutoff:
            to_move.append((start, end, header))
    if not to_move:
        print(f"abba: no DONE messages older than {age_days} days — nothing to move")
        return []
    print(f"abba: moving {len(to_move)} DONE messages (older than {age_days}d) to archive")
    for _, _, h in to_move[:10]:
        print("  -", h[:100])
    if len(to_move) > 10:
        print(f"  … and {len(to_move) - 10} more")
    if not apply_:
        return to_move
    blocks = [text[s:e] for s, e, _ in to_move]
    orig_archive = read(archive_path) if os.path.exists(archive_path) else None
    arch = orig_archive if orig_archive is not None else (
        "# ABBA — Archive\n\nDONE messages moved out of ABBA.md by scripts/rotate.py "
        "(verbatim, newest-first). The trail is the value — never edit these.\n\n")
    arch = arch.rstrip("\n") + "\n\n" + "\n\n".join(b.strip() for b in blocks) + "\n"
    new_text = text
    for s, e, _ in sorted(to_move, key=lambda t: -t[0]):  # delete bottom-up
        new_text = new_text[:s] + new_text[e:]
    new_text = re.sub(r"\n{4,}", "\n\n\n", new_text)
    atomic_write(archive_path, arch)
    atomic_write(abba_path, new_text)
    verify_moves(blocks, src=read(abba_path), dst=read(archive_path),
                 restore=[(abba_path, text), (archive_path, orig_archive or "")])
    return to_move


# ── task documents (tasks/MP-0NN.md) ─────────────────────────────────────────

TASKS_DIRNAME = "tasks"
CLOSED_DIRNAME = "closed"
TERMINAL_MARKS = ("✅", "❌")   # ✅ completed · ❌ dropped
TABLE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|", re.M)


def doc_name(task_id):
    """tasks/MP-027.md for row 27. Zero-padded to three digits so a directory
    listing sorts in ID order past 99 — Coworker is already at 117 rows, where
    unpadded names sort 1, 10, 100, 11 and the folder stops being readable."""
    return f"MP-{int(task_id):03d}.md"


def parse_summary_table(ledger_text):
    """Return {task_id: is_terminal} from the canonical summary table.

    STATUS IS READ HERE AND NOWHERE ELSE. The table is the single source of truth
    for state; the document holds substance only. If this ever falls back to
    reading status out of a document, the two copies can disagree and neither
    announces it."""
    m = re.search(r"^## Summary table[ \t]*$", ledger_text, re.M)
    if not m:
        return None
    tail = ledger_text[m.end():]
    nxt = re.search(r"^## ", tail, re.M)
    table = tail[: nxt.start()] if nxt else tail
    return {int(r.group(1)): any(k in r.group(2) for k in TERMINAL_MARKS)
            for r in TABLE_ROW_RE.finditer(table)}


def rotate_task_docs(root, ledger_path, apply_):
    """Move terminal rows' documents to tasks/closed/, and fail on any live row
    whose document is missing.

    No age lag by design: a document is out of the boot path the moment it stops
    being inline, whether it sits in tasks/ or tasks/closed/. Waiting ten days to
    move a file that costs nothing either way buys nothing, and the close-date
    heuristic a lag requires is a guess about live context — the one thing worth
    refusing to guess about.

    Returns (moved, missing). A non-empty `missing` is a hard failure at close."""
    rows = parse_summary_table(read(ledger_path))
    if rows is None:
        print("tasks: no '## Summary table' found — skipped")
        return [], []
    tasks_dir = os.path.join(root, TASKS_DIRNAME)
    closed_dir = os.path.join(tasks_dir, CLOSED_DIRNAME)

    moved, missing = [], []
    for tid in sorted(rows):
        name = doc_name(tid)
        live_p, closed_p = os.path.join(tasks_dir, name), os.path.join(closed_dir, name)
        if rows[tid]:                                   # terminal
            if os.path.exists(live_p):
                moved.append((tid, name, live_p, closed_p))
        else:                                           # live
            if not os.path.exists(live_p):
                # tolerate a live row whose doc is still in closed/ (a reopened
                # task) — report it as missing only if it exists nowhere at all
                missing.append((tid, name, os.path.exists(closed_p)))

    for tid, name, in_closed in missing:
        where = "found in tasks/closed/ — REOPENED task, move it back" if in_closed else "NOT FOUND anywhere"
        print(f"  ! row #{tid} is open but {TASKS_DIRNAME}/{name} is {where}")
    if not moved:
        print(f"tasks: no closed rows with a live document — nothing to move")
    else:
        print(f"tasks: moving {len(moved)} closed task document(s) to {TASKS_DIRNAME}/{CLOSED_DIRNAME}/")
        for tid, name, _, _ in moved[:10]:
            print(f"  - #{tid} {name}")
        if len(moved) > 10:
            print(f"  … and {len(moved) - 10} more")

    if apply_ and moved:
        os.makedirs(closed_dir, exist_ok=True)
        for tid, name, src, dst in moved:
            payload = read(src)
            atomic_write(dst, payload)
            if read(dst) != payload:                    # verify BEFORE unlinking
                print(f"VERIFY FAILED — {name} did not land in {CLOSED_DIRNAME}/; "
                      "source left in place.", file=sys.stderr)
                sys.exit(1)
            os.remove(src)
        print("verify: all moved documents present in destination, absent from source ✓")
    return moved, missing


# ── size report ──────────────────────────────────────────────────────────────

JOURNAL_MAX_DEFAULT = 12288


def manifest_journal_max(root, manifest_name="4SYNC.yaml"):
    """close.journal.max_bytes from the manifest, or the default.

    Tries yaml, falls back to a scoped regex — rotate.py must run on a bare
    interpreter with no third-party packages, the same constraint meter.py has."""
    p = os.path.join(root, os.environ.get("ARCH_MANIFEST") or manifest_name)
    if not os.path.exists(p):
        return JOURNAL_MAX_DEFAULT
    text = read(p)
    try:
        import yaml  # type: ignore
        v = (yaml.safe_load(text) or {}).get("close", {}).get("journal", {}).get("max_bytes")
        if isinstance(v, int):
            return v
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    m = re.search(r"(?ms)^\s{2}journal:[^\n]*\n(.*?)(?=^\s{0,2}\S|\Z)", text)
    if m:
        mb = re.search(r"^\s*max_bytes:\s*(\d+)", m.group(1), re.M)
        if mb:
            return int(mb.group(1))
    return JOURNAL_MAX_DEFAULT


def report_sizes(root, ledger_path, journal_max):
    """Print what the ledger costs at boot, and flag an over-cap journal.

    `keep` bounds the journal by COUNT; nothing bounds it by SIZE, and one
    session block can run several KB. Without this number the journal quietly
    becomes the next thing that dominates boot — which is precisely how the
    descriptions got there."""
    text = read(ledger_path)
    total = len(text.encode("utf-8"))
    parts = split_journal(text)
    jbytes = 0
    if parts:
        _, blocks, _ = parts
        # strip() each block, exactly as rotate_journal does when it rebuilds the
        # section. split_journal leaves a leading newline on blocks[0] when no
        # instruction comment precedes it, so an unstripped measurement would
        # differ by a byte depending on whether the comment is present — a number
        # that changes with the presence of a comment is not a measurement.
        jbytes = len("\n\n".join(b.strip() for b in blocks).encode("utf-8"))
    share = (jbytes / total * 100) if total else 0
    print(f"size: MERGE_PLAN.md {total:,} B (~{total // 4:,} tok) — "
          f"journal {jbytes:,} B (~{jbytes // 4:,} tok, {share:.0f}%)")
    if jbytes > journal_max:
        print(f"  ! journal is over its {journal_max:,} B cap by {jbytes - journal_max:,} B — "
              "trim the oldest blocks or lower --keep. (Reported, not blocked.)")
    return total, jbytes


# ── verify ───────────────────────────────────────────────────────────────────

def verify_moves(moved_blocks, src, dst, restore):
    for b in moved_blocks:
        key = b.strip() if isinstance(b, str) else None
        if key is None:
            continue
        if key not in dst or key in src:
            for path, original in restore:
                if original:
                    atomic_write(path, original)
            print("VERIFY FAILED — originals restored. Nothing was rotated.", file=sys.stderr)
            sys.exit(1)
    print("verify: all moved blocks present in destination, absent from source ✓")


def main():
    _utf8_stdout()   # belt and braces; also runs at import for library callers
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="project root (holds MERGE_PLAN.md / ABBA.md)")
    ap.add_argument("--keep", type=int, default=5)
    ap.add_argument("--age", type=int, default=10)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--journal-max-bytes", type=int, default=None,
                    help="override the manifest's close.journal.max_bytes")
    args = ap.parse_args()

    d = os.path.abspath(args.dir)
    ledger = os.path.join(d, "MERGE_PLAN.md")
    history = os.path.join(d, "JOURNAL_HISTORY.md")
    abba = os.path.join(d, "ABBA.md")
    archive = os.path.join(d, "ABBA_ARCHIVE.md")

    if (sys.platform.startswith("linux") and os.path.isdir("/sessions")
            and os.environ.get("ARCH_ROTATE_SANDBOX_OK") != "1"):
        print("REFUSING: this looks like a sandbox mount environment. Run rotate.py "
              "host-side (native git) only. (ARCH_ROTATE_SANDBOX_OK=1 overrides — for "
              "tests on NON-mounted paths like /tmp only, never on the real ledgers.)",
              file=sys.stderr)
        sys.exit(1)

    targets = [p for p in (ledger, history, abba, archive) if os.path.exists(p)]
    targets += [os.path.join(d, TASKS_DIRNAME)] if os.path.isdir(os.path.join(d, TASKS_DIRNAME)) else []
    if args.apply and not args.allow_dirty and git_dirty(d, targets):
        print("REFUSING: target files have uncommitted changes (or not a git repo). "
              "Commit first — git is the undo — or pass --allow-dirty.", file=sys.stderr)
        sys.exit(1)

    missing = []
    if os.path.exists(ledger):
        rotate_journal(ledger, history, args.keep, args.apply)
        _, missing = rotate_task_docs(d, ledger, args.apply)
    if os.path.exists(abba):
        rotate_abba(abba, archive, args.age, args.apply)
    if os.path.exists(ledger):
        jmax = args.journal_max_bytes if args.journal_max_bytes is not None else manifest_journal_max(d)
        report_sizes(d, ledger, jmax)
    print("mode:", "APPLIED" if args.apply else "dry-run (pass --apply to write)")

    # A live row with no document is an unexecutable task. Exit non-zero so a
    # close that would ship one is impossible to miss — this is the pointer-
    # integrity gate the split traded the inline description for.
    if missing:
        print(f"\nFAILED: {len(missing)} open row(s) have no task document. "
              f"Write {TASKS_DIRNAME}/MP-0NN.md for each before closing.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# ═══ EOF rotate.py ═══
