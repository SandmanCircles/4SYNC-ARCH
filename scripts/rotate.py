#!/usr/bin/env python3
"""
4SYNC ARCH — ledger rotation. Run at wrap from a GIT-CAPABLE,
host-side session only (git is the undo; never run through a sandbox mount).

Does two things, both as verbatim block moves:

1. JOURNAL KEEP-N: if the '## Session journal (recent)' section of the ledger
   (MERGE_PLAN.md) holds more than N blocks (default 5), move the oldest
   (bottom) blocks to the TOP of the history file's journal area
   (MERGE_PLAN_HISTORY.md), newest-first order preserved.

2. DESCRIPTION ARCHIVE: move long-form descriptions of TERMINAL tasks (✅/❌)
   closed longer than --age days from the ledger's '## Task descriptions'
   section to MERGE_PLAN_ARCHIVE.md. The summary-table row never moves — the
   table stays the canonical list; only the long form leaves the boot path.
   Descriptions are the largest thing in the ledger, so this is the single
   biggest boot-cost lever available. A terminal description with no parseable
   close date is skipped and reported, never guessed at.

3. ABBA ARCHIVE: move 'Status: DONE' messages older than --age days (default 10)
   from ABBA.md to ABBA_ARCHIVE.md (created with a header if absent), newest-first.

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


# ── task-description archive ─────────────────────────────────────────────────

DESC_HEAD_RE = re.compile(r"^### #(\d+) —[^\n]*$", re.M)
TERMINAL_MARKS = ("✅", "❌")   # ✅ completed · ❌ dropped
# The ledger's own convention: a terminal description opens by stating when it
# closed — "Completed 2026-07-28.", "Implemented …", "Resolved …", "Found AND
# fixed …". Anchor on that verb first; fall back to the first date in the body.
CLOSE_VERB_RE = re.compile(
    r"\b(?:completed|implemented|resolved|shipped|landed|fixed|dropped)\b[^\n]{0,60}?"
    r"(20\d\d-[01]\d-[0-3]\d)", re.I)


def split_descriptions(text):
    """Return (start, end, header) for every '### #N — …' block inside the
    '## Task descriptions' section. Bounded by that heading and the next column-0
    '## ', so a '### #' appearing anywhere else in the ledger is never touched."""
    m = re.search(r"^## Task descriptions[ \t]*$", text, re.M)
    if not m:
        return []
    body_start = text.index("\n", m.start()) + 1
    nxt = re.search(r"^## ", text[body_start:], re.M)
    body_end = body_start + (nxt.start() if nxt else len(text) - body_start)
    heads = [h for h in DESC_HEAD_RE.finditer(text) if body_start <= h.start() < body_end]
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else body_end
        out.append((h.start(), end, h.group(0)))
    return out


def description_close_date(block):
    """The date a description says it closed, or None. Searches the BODY only —
    a subject line can contain a date that is about the work, not its closure.
    None means SKIP: this function never guesses, because guessing early moves
    live context out of the ledger."""
    body = block.split("\n", 1)[1] if "\n" in block else ""
    m = CLOSE_VERB_RE.search(body) or DATE_RE.search(body)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def rotate_descriptions(ledger_path, archive_path, age_days, apply_):
    """Move long-form descriptions of terminal (✅/❌) tasks older than the lag out
    of MERGE_PLAN.md and into MERGE_PLAN_ARCHIVE.md.

    THE SUMMARY-TABLE ROW NEVER MOVES — only the description. The table stays the
    canonical list of every task; this is purely a boot-cost measure, since
    descriptions are the largest thing in the ledger and closed ones are dead
    weight in every session's context window.

    Terminal-but-undated descriptions are skipped and REPORTED, not guessed at."""
    text = read(ledger_path)
    cutoff = datetime.now() - timedelta(days=age_days)
    to_move, undated = [], []
    for start, end, header in split_descriptions(text):
        if not header.rstrip().endswith(TERMINAL_MARKS):
            continue
        when = description_close_date(text[start:end])
        if when is None:
            undated.append(header)
            continue
        if when < cutoff:
            to_move.append((start, end, header))

    for h in undated:
        print("  ! closed but undated, left in place:", h.strip()[:90])
    if not to_move:
        print(f"descriptions: none closed longer than {age_days} days — nothing to move")
        return []
    print(f"descriptions: moving {len(to_move)} closed description(s) (older than {age_days}d) to archive")
    for _, _, h in to_move[:10]:
        print("  -", h.strip()[:100])
    if len(to_move) > 10:
        print(f"  … and {len(to_move) - 10} more")
    if not apply_:
        return to_move

    blocks = [text[s:e] for s, e, _ in to_move]
    orig_archive = read(archive_path) if os.path.exists(archive_path) else None
    arch = orig_archive if orig_archive is not None else (
        "# Merge Plan Archive\n\nLong-form descriptions of closed tasks, moved out of "
        "MERGE_PLAN.md by scripts/rotate.py (verbatim). The summary-table row stays in "
        "the ledger — only the description lives here.\n")
    arch = arch.rstrip("\n") + "\n\n" + "\n\n".join(b.strip() for b in blocks) + "\n"
    new_text = text
    for s, e, _ in sorted(to_move, key=lambda t: -t[0]):   # delete bottom-up
        new_text = new_text[:s] + new_text[e:]
    new_text = re.sub(r"\n{4,}", "\n\n\n", new_text)
    atomic_write(archive_path, arch)
    atomic_write(ledger_path, new_text)
    verify_moves(blocks, src=read(ledger_path), dst=read(archive_path),
                 restore=[(ledger_path, text), (archive_path, orig_archive or "")])
    return to_move


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
    # Windows consoles default to cp1252 — the '✓' in the status prints would
    # raise UnicodeEncodeError AFTER a successful --apply (writes done, exit lies)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="project root (holds MERGE_PLAN.md / ABBA.md)")
    ap.add_argument("--keep", type=int, default=5)
    ap.add_argument("--age", type=int, default=10)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    d = os.path.abspath(args.dir)
    ledger = os.path.join(d, "MERGE_PLAN.md")
    history = os.path.join(d, "MERGE_PLAN_HISTORY.md")
    abba = os.path.join(d, "ABBA.md")
    archive = os.path.join(d, "ABBA_ARCHIVE.md")
    desc_archive = os.path.join(d, "MERGE_PLAN_ARCHIVE.md")

    if (sys.platform.startswith("linux") and os.path.isdir("/sessions")
            and os.environ.get("ARCH_ROTATE_SANDBOX_OK") != "1"):
        print("REFUSING: this looks like a sandbox mount environment. Run rotate.py "
              "host-side (native git) only. (ARCH_ROTATE_SANDBOX_OK=1 overrides — for "
              "tests on NON-mounted paths like /tmp only, never on the real ledgers.)",
              file=sys.stderr)
        sys.exit(1)

    targets = [p for p in (ledger, history, abba, archive, desc_archive) if os.path.exists(p)]
    if args.apply and not args.allow_dirty and git_dirty(d, targets):
        print("REFUSING: target files have uncommitted changes (or not a git repo). "
              "Commit first — git is the undo — or pass --allow-dirty.", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(ledger):
        rotate_journal(ledger, history, args.keep, args.apply)
        rotate_descriptions(ledger, desc_archive, args.age, args.apply)
    if os.path.exists(abba):
        rotate_abba(abba, archive, args.age, args.apply)
    print("mode:", "APPLIED" if args.apply else "dry-run (pass --apply to write)")


if __name__ == "__main__":
    main()
# ═══ EOF rotate.py ═══
