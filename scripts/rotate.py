#!/usr/bin/env python3
"""
4SYNC ARCH — ledger rotation. Run at wrap from a GIT-CAPABLE,
host-side session only (git is the undo; never run through a sandbox mount).

Three passes MOVE or REWRITE (1-3, all verbatim or derived, all gated on
--apply); the rest only measure and never block:

1. JOURNAL KEEP-N, THEN CAP-BY-SIZE: if the '## Session journal (recent)'
   section of the ledger (MERGE_PLAN.md) holds more than N blocks (default 5),
   move the oldest (bottom) blocks to the TOP of the history file's journal
   area, newest-first order preserved. THEN, if what remains still exceeds
   close.journal.max_bytes, keep moving the oldest until it fits — because
   `keep` is a COUNT cap and one block can run several KB, so the count rule
   alone cannot bound the journal (measured here: 5 blocks obeying KEEP-5
   exactly, 70% over the byte cap, reported by three reviews and never acted on
   because the only remedy on offer was a hand-trim). The newest block is NEVER
   moved — it is the previous session's handoff. Verbatim moves either way:
   nothing is rewritten, nothing is deleted. The history file is whatever the
   manifest's close.journal.overflow_to declares, defaulting to
   JOURNAL_HISTORY.md.

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

3. TALLY: derive the ledger's '**Tally:**' line from the summary table and, on
   --apply, rewrite it. The only pass here that changes text rather than moving
   it, because a tally needs no human judgement — it is a count of rows sitting
   right there, and a hand-maintained count against a table the same session is
   editing drifts every time (measured here: rewritten by hand, wrong within the
   hour). Refuses to write if any row's status cell carries no recognised mark:
   a total that silently omits a row is worse than a stale one.

4. ABBA ARCHIVE: move 'Status: DONE' messages older than --age days (default 10)
   from ABBA.md to ABBA_ARCHIVE.md (created with a header if absent), newest-first.

5. SIZE REPORT: print the ledger's boot cost and the journal's share of it, and
   warn when the journal exceeds the manifest's close.journal.max_bytes. `keep`
   is a COUNT cap, and a single session block can run several KB — so the count
   cap alone cannot bound the journal. Reports; never blocks. ARCH capped its
   8 KB manifest and left the file that actually dominates boot unmeasured;
   this is the number that closes that asymmetry.

6. SUBJECT REPORT: flag summary-table rows whose Subject cell has grown into a
   description (default cap ~120 chars). Once long form lives in tasks/, the
   TABLE is the boot cost and nothing bounds it — capping descriptions while
   leaving rows unbounded just relocates the growth one level up. Reports;
   never blocks, and the fix is a task document, not a shorter sentence.

7. TABLE-PROSE REPORT: measure the non-row prose in the summary-table section
   (the running "Tally:" commentary and friends) against the rows it annotates.
   The third place the growth went: descriptions were capped and moved to
   tasks/, row cells were capped after them, and the paragraphs AROUND the table
   were watched by nothing. Prose outweighing rows means the section stopped
   being a table with a note on it. Reports; never blocks.

8. FINDINGS REPORT: measure FINDINGS.md and flag entries with no `Trigger:`.
   That file is on-demand and never booted, so it is cheap — but only while
   every entry is greppable and every entry has an exit. An entry nothing can
   grep for never reaches the session that needed it and is counted forever.

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
HIST_SECTION = "## Session entries (newest first)"


def _insert_under_section(text, heading, payload):
    """Insert `payload` immediately under a column-0 `heading`, newest-first.

    Destination files are DOCUMENTS, not append logs: they open with a title and
    explanatory prose, carry their own how-to section, and end with a footer.
    Inserting after line 1 — which this did until 2026-07-30 — buries the entry
    above the file's own explanation; appending at EOF strands the footer (the
    same defect fixed for the description archive a day earlier). Both stem from
    treating a structured document as a place to dump text. Anchor on the
    section that exists to hold entries; fall back to append only when the file
    has no such section (a freshly created one)."""
    m = re.search(r"^" + re.escape(heading) + r"[ \t]*$", text, re.M)
    if not m:
        return text.rstrip("\n") + "\n\n" + payload + "\n"
    cut = text.index("\n", m.start()) + 1
    return text[:cut] + "\n" + payload + "\n" + text[cut:]


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


def journal_bytes(blocks):
    """Size of a journal section built from `blocks`.

    Measured exactly as rotate_journal writes it back and as report_sizes reads
    it — strip()ed and rejoined on a blank line. Three call sites deriving the
    same number three ways is how a cap starts disagreeing with the file it caps."""
    return len("\n\n".join(b.strip() for b in blocks).encode("utf-8"))


def rotate_journal(ledger_path, history_path, keep, apply_, journal_max=None):
    """Move journal blocks to history — by COUNT, then by SIZE.

    THE ASYMMETRY THIS CLOSES: `keep` bounds the journal by count and cannot
    bound it by size, because one session block can run several KB. Measured on
    this silo the day this was written: 5 blocks (KEEP-5 exactly obeyed, nothing
    to move by count) totalling 27,764 B against a 16,384 B cap — 70% over,
    reported over three consecutive external reviews and never acted on, because
    the only mechanism on offer was a hand-trim.

    A number nobody can act on mechanically gets re-reported until it is
    ignored. So the size cap now MOVES blocks instead of only counting them, and
    it moves them the same way the count rule does: verbatim, oldest-first, into
    the history file the manifest declares. Nothing is rewritten and nothing is
    deleted — a journal that edits itself clean teaches nothing, which is why
    trimming prose out of a past block was never the right fix.

    FLOOR OF ONE: the newest block is never moved, whatever the cap says. It is
    the previous session's handoff, and a cap that can empty the journal would
    take the last close with it. An instance whose newest block alone exceeds
    the cap is over-cap by one block and gets told so, which is the honest
    report — the alternative is a rule that deletes the thing it exists to keep."""
    text = read(ledger_path)
    parts = split_journal(text)
    if not parts:
        print(f"journal: section '{JOURNAL_HEAD}' not found in {ledger_path} — skipped")
        return []
    before, blocks, after = parts
    kept, moved = blocks[:keep], blocks[keep:]
    by_count = len(moved)

    if journal_max is not None:
        # oldest kept block is kept[-1]; it is NEWER than everything already in
        # `moved`, so it goes to the front to preserve newest-first order.
        while len(kept) > 1 and journal_bytes(kept) > journal_max:
            moved.insert(0, kept.pop())

    if not moved:
        size = journal_bytes(kept)
        note = "" if journal_max is None else f", {size:,}/{journal_max:,} B"
        print(f"journal: {len(blocks)} blocks (cap {keep}{note}) — nothing to move")
        return []

    by_size = len(moved) - by_count
    why = f"oldest {by_count} over the count cap" if by_count else ""
    if by_size:
        why += (" plus " if why else "") + f"{by_size} more to get under the {journal_max:,} B size cap"
    print(f"journal: {len(blocks)} blocks — moving {why} to history")
    for b in moved:
        print("  -", b.strip().splitlines()[0][:100])
    if journal_max is not None:
        after_bytes = journal_bytes(kept)
        print(f"  journal {journal_bytes(blocks):,} B → {after_bytes:,} B "
              f"(cap {journal_max:,})"
              + ("" if after_bytes <= journal_max else
                 " — STILL OVER: the newest block alone exceeds the cap and is never moved"))
    if not apply_:
        return moved
    hist = read(history_path) if os.path.exists(history_path) else (
        "# Session journal — history (newest-first)\n\n" + HIST_SECTION + "\n")
    new_hist = _insert_under_section(hist, HIST_SECTION,
                                     "\n\n".join(b.strip() for b in moved))
    new_ledger = before + "\n\n".join(b.strip() for b in kept) + "\n\n" + after.lstrip("\n")
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
    listing sorts in ID order past 99 — a real adopter ledger hit 117 rows, where
    unpadded names sort 1, 10, 100, 11 and the folder stops being readable."""
    return f"MP-{int(task_id):03d}.md"


def summary_table_span(ledger_text):
    """Return (start, end) offsets of the canonical summary-table section, or None.

    The section is bounded by the next `## ` OR `### ` heading — not `## ` alone.
    A ledger that has not been split yet keeps its `### #NNN` description blocks
    inside this same section (one real ledger had 23 of them there), so a `## `-only
    bound runs the row scan through tens of KB of prose. It finds no phantom row
    there today, but the moment a description quotes a table whose first column
    is numeric it would invent one — and rotate.py would then fail every close
    demanding a document for a row that does not exist.

    Offsets rather than text, because reconcile_tally has to WRITE back into this
    section and a substring cannot say where it came from. summary_table_section()
    stays the reader's interface and is defined in terms of this, so the bound is
    computed in exactly one place."""
    m = re.search(r"^## Summary table[ \t]*$", ledger_text, re.M)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^#{2,3} ", ledger_text[start:], re.M)
    return start, (start + nxt.start() if nxt else len(ledger_text))


def summary_table_section(ledger_text):
    """Return the text of the canonical summary table, or None if absent."""
    span = summary_table_span(ledger_text)
    return None if span is None else ledger_text[span[0]:span[1]]


def parse_summary_table(ledger_text):
    """Return {task_id: is_terminal} from the canonical summary table.

    STATUS IS READ HERE AND NOWHERE ELSE. The table is the single source of truth
    for state; the document holds substance only. If this ever falls back to
    reading status out of a document, the two copies can disagree and neither
    announces it."""
    table = summary_table_section(ledger_text)
    if table is None:
        return None
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


# ── tally reconciliation ─────────────────────────────────────────────────────

# Ordered — this is also the order the rendered line reports in. Keys are the
# BASE codepoint with no variation selector: ⏸️ is U+23F8 U+FE0F but a ledger may
# write the bare U+23F8, and two ledgers rendering the same status differently
# must not produce two different counts.
STATUS_MARKS = (
    ("✅",         "completed"),    # ✅
    ("\U0001f504",     "in_progress"),  # 🔄
    ("⏳",         "pending"),      # ⏳
    ("⏸",         "blocked"),      # ⏸️
    ("❌",         "dropped"),      # ❌
)

TALLY_RE = re.compile(r"^\*\*Tally:\*\*[^\n]*$", re.M)


def compute_tally(ledger_text):
    """Count summary-table rows by status mark. Returns (counts, unknown).

    Reads the TABLE and nothing else, for the same reason parse_summary_table
    does: the table is the single source of truth for state. A tally derived
    from anywhere else is a second copy of state that cannot announce its own
    drift — which is the entire defect this closes."""
    table = summary_table_section(ledger_text)
    if table is None:
        return None, None
    counts = {name: 0 for _, name in STATUS_MARKS}
    unknown = []
    for r in TABLE_ROW_RE.finditer(table):
        cell = r.group(2).replace("️", "")
        hit = [name for mark, name in STATUS_MARKS if mark in cell]
        if len(hit) == 1:
            counts[hit[0]] += 1
        else:
            # zero marks (a typo, an empty cell) or two (an edit half-applied).
            # Both are "this row has no single status", and guessing at one is
            # how a count starts lying with total confidence.
            unknown.append((int(r.group(1)), " ".join(r.group(2).split())))
    return counts, unknown


def render_tally(counts):
    """The one place the Tally sentence is spelled. Matches the shipped format
    exactly, so a reconciled ledger diffs on the NUMBERS and never on wording."""
    parts = ", ".join(f"{counts[name]} {name}" for _, name in STATUS_MARKS)
    return f"**Tally:** {sum(counts.values())} tasks total — {parts}."


def reconcile_tally(ledger_path, apply_):
    """Derive the Tally line from the table and, on --apply, WRITE it.

    WHY THIS WRITES INSTEAD OF REPORTING. Every other check in this file
    reports, and that is right for them: an over-long Subject wants a human
    judgement about what the row is for. A tally wants no judgement at all — it
    is a count of rows that are sitting right there. Measured on this silo
    2026-08-04: the Tally paragraph was rewritten by hand and was WRONG WITHIN
    THE HOUR, because the same session closed four more rows afterwards. A
    number a human maintains by hand against a table the same human is editing
    will drift every time, and rotate.py's own journal fix is the precedent —
    'a number nobody can act on mechanically gets re-reported until it is
    ignored.' So this makes the line DERIVED. On a dry run it prints the drift;
    on --apply the ledger stops disagreeing with itself.

    UNKNOWN ROWS BLOCK THE REWRITE. If any row's status cell carries no
    recognised mark, the computed total would silently omit it — and writing a
    confidently wrong number is strictly worse than leaving a stale one, which
    at least still looks like something to check. Report and leave it alone.

    Never blocks the close; returns (counts, changed)."""
    text = read(ledger_path)
    counts, unknown = compute_tally(text)
    if counts is None:
        print("tally: no '## Summary table' found — skipped")
        return None, False

    total = sum(counts.values())
    print(f"tally: {total} rows — " +
          ", ".join(f"{counts[n]} {n}" for _, n in STATUS_MARKS))

    for tid, cell in unknown[:10]:
        print(f"  ! row #{tid} has no recognised status mark (cell: {cell!r}) — not counted")
    if len(unknown) > 10:
        print(f"  … and {len(unknown) - 10} more")
    if unknown:
        print("  Tally left alone: a total that silently omits rows is worse than a "
              "stale one. Fix the status cells, then re-run. (Reported, not blocked.)")
        return counts, False

    span = summary_table_span(text)
    match = next((m for m in TALLY_RE.finditer(text) if span[0] <= m.start() < span[1]), None)
    if match is None:
        # Bounded to the table section on purpose: a journal block quoting a
        # tally must never be mistaken for the ledger's own. Absent means absent
        # — this reconciles a line the ledger already has, it does not invent
        # structure in a ledger that never asked for one.
        print("  (no '**Tally:**' line in the summary-table section — nothing to reconcile)")
        return counts, False

    written, computed = match.group(0), render_tally(counts)
    if written == computed:
        print("  written Tally matches the table ✓")
        return counts, False

    print("  ! the written Tally disagrees with the table")
    print(f"      written:  {written}")
    print(f"      computed: {computed}")
    if not apply_:
        print("  Run with --apply to rewrite it from the rows. (Reported, not blocked.)")
        return counts, False

    new_text = text[:match.start()] + computed + text[match.end():]
    atomic_write(ledger_path, new_text)
    if computed not in read(ledger_path):            # verify BEFORE claiming it
        atomic_write(ledger_path, text)
        print("VERIFY FAILED — Tally not written; ledger restored.", file=sys.stderr)
        sys.exit(1)
    print("  Tally rewritten from the table ✓")
    return counts, True


# ── subject-length report ────────────────────────────────────────────────────

SUBJECT_MAX_DEFAULT = 120
# id | status | subject | …  — the 3rd cell, which TABLE_ROW_RE deliberately skips
TABLE_SUBJECT_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*[^|]*\|\s*(.*?)\s*\|", re.M)


def report_subjects(ledger_path, subject_max):
    """Flag summary-table rows whose Subject cell has become a description.

    THE ASYMMETRY THIS CLOSES: the split moved task substance out of the boot
    path and capped nothing about the table it left behind — so after the split
    the TABLE IS THE BOOT COST, and a row may hold any amount of text. Measured
    on a real adopter ledger the day this was written: 119 rows carrying 24,087 B
    of Subject text (~6,000 tok), mean 199 chars, longest 2,426 — a bolded
    multi-clause paragraph carrying defect IDs. That is a description that
    migrated into a table cell. Ship the split without this and the growth
    simply relocates one level up and is rediscovered in a month.

    REPORTS, NEVER BLOCKS. A session mid-write must not be stopped from
    recording a row; the number is the whole intervention. And the fix for an
    over-long Subject is never to compress meaning out of the ledger — it is a
    signal that the row wants a tasks/MP-0NN.md, which now exists to receive it.

    ~120 chars is roughly where a subject stops being a label and starts being a
    description. Returns (rows, over) for callers and tests."""
    table = summary_table_section(read(ledger_path))
    if table is None:
        print("subjects: no '## Summary table' found — skipped")
        return {}, []
    subjects = {int(r.group(1)): r.group(2) for r in TABLE_SUBJECT_RE.finditer(table)}
    if not subjects:
        print("subjects: summary table has no rows — nothing to measure")
        return {}, []

    total = sum(len(s.encode("utf-8")) for s in subjects.values())
    longest = max(len(s) for s in subjects.values())
    over = sorted(((len(s), t) for t, s in subjects.items() if len(s) > subject_max),
                  reverse=True)
    if not over:
        print(f"subjects: {len(subjects)} rows, {total:,} B — longest {longest} chars "
              f"(cap {subject_max}) ✓")
        return subjects, over

    mean = round(sum(len(s) for s in subjects.values()) / len(subjects))
    print(f"subjects: {len(over)} of {len(subjects)} rows over the {subject_max}-char cap — "
          f"mean {mean}, {total:,} B total (~{total // 4:,} tok)")
    for n, tid in over[:10]:
        head = " ".join(subjects[tid].split())[:70]
        print(f"  ! #{tid:<4} {n:>5,} chars — {head}…")
    if len(over) > 10:
        print(f"  … and {len(over) - 10} more")
    print(f"  An over-long Subject wants a {TASKS_DIRNAME}/MP-0NN.md, not a shorter "
          "sentence. (Reported, not blocked.)")
    return subjects, over


# ── table-prose report ───────────────────────────────────────────────────────


def report_table_prose(ledger_path):
    """Measure the NON-ROW prose sitting in the summary-table section.

    THE THIRD PLACE THE GROWTH WENT. The split capped descriptions and moved
    them to tasks/; report_subjects then capped the row cells they fled into.
    Neither watches the prose paragraphs AROUND the table — the running
    "Tally:" commentary, the "Pickup-ready" note — and that is where it went
    next. Measured on this silo the day this was written: 7,880 B of Tally
    against 3,910 B of actual rows, i.e. the annotation had grown to twice the
    thing it annotates, every byte of it duplicating a tasks/closed/MP-0NN.md
    that already held the same closure narrative in full.

    That duplication is the tell, and it is why this reports a RATIO rather than
    a byte cap. A tally is a count; the moment it starts explaining WHY each row
    closed it has become a second copy of the task documents, and the ledger's
    own rule — table owns state, document owns substance — is already the
    argument against it. There is no honest fixed number here (a 200-row ledger
    legitimately carries more prose than a 20-row one), but prose outweighing
    rows means the section stopped being a table with a note on it.

    Reports; never blocks. Returns (row_bytes, prose_bytes)."""
    table = summary_table_section(read(ledger_path))
    if table is None:
        print("table-prose: no '## Summary table' found — skipped")
        return 0, 0
    rows = prose = 0
    for line in table.splitlines(keepends=True):
        n = len(line.encode("utf-8"))
        if line.lstrip().startswith("|"):
            rows += n
        elif line.strip():
            prose += n
    total = rows + prose
    print(f"table: {rows:,} B of rows + {prose:,} B of prose "
          f"(~{total // 4:,} tok of boot)")
    if prose > rows:
        print(f"  ! prose outweighs the rows it annotates ({prose:,} > {rows:,} B). "
              "A tally is a count; once it explains why each row closed it is a "
              f"second copy of {TASKS_DIRNAME}/closed/. (Reported, not blocked.)")
    return rows, prose


# ── size report ──────────────────────────────────────────────────────────────

JOURNAL_MAX_DEFAULT = 12288
JOURNAL_HISTORY_DEFAULT = "JOURNAL_HISTORY.md"


def manifest_journal_overflow(root, manifest_name="4SYNC.yaml"):
    """close.journal.overflow_to from the manifest, or the default filename.

    Mirrors manifest_journal_max(), fallback and all. A manifest that declares
    where its journal overflows must be OBEYED — this was hardcoded, so an
    instance declaring its own history file had rotation silently scatter journal
    blocks into a second file it never declared, with no error to notice. A
    manifest key that is parsed but never honoured is worse than an absent one,
    because it is trusted."""
    p = os.path.join(root, os.environ.get("ARCH_MANIFEST") or manifest_name)
    if not os.path.exists(p):
        return JOURNAL_HISTORY_DEFAULT
    text = read(p)
    try:
        import yaml  # type: ignore
        v = (yaml.safe_load(text) or {}).get("close", {}).get("journal", {}).get("overflow_to")
        if isinstance(v, str) and v.strip():
            return os.path.basename(v.strip())
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    m = re.search(r"(?ms)^\s{2}journal:[^\n]*\n(.*?)(?=^\s{0,2}\S|\Z)", text)
    if m:
        ov = re.search(r"^\s*overflow_to:\s*[\"']?([^\"'\s#]+)", m.group(1), re.M)
        if ov:
            return os.path.basename(ov.group(1))
    return JOURNAL_HISTORY_DEFAULT


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
              "run with --apply to roll the oldest blocks into history. (Reported, not "
              "blocked.) If this persists after an --apply, the newest block alone is "
              "over cap: that one is never moved, and the fix is a shorter close.")
    return total, jbytes


# ── findings report ──────────────────────────────────────────────────────────

FINDINGS_FILENAME = "FINDINGS.md"


def report_findings(root, findings_name=FINDINGS_FILENAME):
    """Measure FINDINGS.md and flag entries with no `Trigger:` line.

    The file earns its place only because three rules are enforced rather than
    aspirational, and this is the enforcement for two of them. A findings file
    with no eviction path is a junk drawer with a nice name; a bucket nobody
    measures is exactly how the ledger reached 67% of boot.

    An entry without a Trigger is not a finding, it is a thought — nothing can
    grep for it, so it can never reach the session that needed it, and it sits
    there being counted forever. Reports; never blocks. Returns (bytes, missing).
    """
    p = os.path.join(root, findings_name)
    if not os.path.exists(p):
        return 0, []
    text = read(p)
    nbytes = len(text.encode("utf-8"))
    # Scan only below the `## Findings` heading. The protocol section above it
    # documents the entry format using the same `###` markup, and counting that
    # as a trigger-less finding is a false positive that trains people to ignore
    # this report — the one failure mode a reporting-only check cannot survive.
    body = text
    m = re.search(r"^## Findings[ \t]*$", text, re.M)
    if m:
        body = text[m.end():]
    heads = list(re.finditer(r"^### (.+)$", body, re.M))
    missing = []
    for i, h in enumerate(heads):
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        if not re.search(r"^Trigger:\s*\S", body[h.end():stop], re.M):
            missing.append(h.group(1).strip())
    print(f"findings: {findings_name} {nbytes:,} B (~{nbytes // 4:,} tok), "
          f"{len(heads)} entries — NOT in the boot path")
    for title in missing[:10]:
        print(f"  ! no Trigger: — {title[:80]}")
    if len(missing) > 10:
        print(f"  … and {len(missing) - 10} more")
    if missing:
        print("  An entry with no Trigger: cannot be grepped, so it never reaches the "
              "session that needed it. Add one or move it to the journal. (Reported, not blocked.)")
    return nbytes, missing


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
    ap.add_argument("--subject-max", type=int, default=SUBJECT_MAX_DEFAULT,
                    help="summary-table Subject length to report over (0 disables)")
    args = ap.parse_args()

    d = os.path.abspath(args.dir)
    ledger = os.path.join(d, "MERGE_PLAN.md")
    history = os.path.join(d, manifest_journal_overflow(d))
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

    # Resolved BEFORE the rotate, not after: the size cap is now an input to the
    # move, not just a line in the report that follows it.
    jmax = args.journal_max_bytes if args.journal_max_bytes is not None else manifest_journal_max(d)

    missing = []
    if os.path.exists(ledger):
        rotate_journal(ledger, history, args.keep, args.apply, jmax)
        _, missing = rotate_task_docs(d, ledger, args.apply)
        # BEFORE the reports, not after: report_table_prose measures this very
        # section, so a reconciled Tally is what gets measured. A close that
        # rewrote the line and then reported the pre-rewrite byte count would be
        # two numbers disagreeing about one file — the shape this pass exists to
        # remove.
        reconcile_tally(ledger, args.apply)
    if os.path.exists(abba):
        rotate_abba(abba, archive, args.age, args.apply)
    if os.path.exists(ledger):
        report_sizes(d, ledger, jmax)
        report_table_prose(ledger)
        if args.subject_max > 0:
            report_subjects(ledger, args.subject_max)
    report_findings(d)
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
