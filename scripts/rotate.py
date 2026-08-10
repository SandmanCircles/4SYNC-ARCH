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
   hour). Refuses to write if any row's status cell carries no recognized mark:
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

8. STATUS-FACTS REPORT: check the hand-copied numbers in the STATUS file the
   manifest declares — manifest caps, byte counts attributed to a named path,
   suite counts, commit SHAs, boot cost — against what they claim to describe.
   Seven of them went stale here in two days, every one caught by eye. Reports
   and never rewrites: unlike the Tally these numbers sit INSIDE prose carrying
   the reasoning around them. A claim must self-identify to be checked at all,
   because a report that flags prose is a report nobody reads.

9. PICKUP-READY REPORT: the ledger's hand-maintained "Pickup-ready right now"
   list against the ⏳ rows in the table. It has drifted in both directions —
   naming a row after it closed, omitting one that was open. Same reason it
   reports rather than rewrites: the list carries an argument per row.

10. FINDINGS REPORT: measure FINDINGS.md and flag entries with no `Trigger:`.
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
import json
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
    recognized mark, the computed total would silently omit it — and writing a
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
        print(f"  ! row #{tid} has no recognized status mark (cell: {cell!r}) — not counted")
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
    # Phrased as a RATIO AGAINST A THRESHOLD rather than as two byte counts.
    # MP#56: this line printed the number that eventually produced a whole
    # restructure, at every close, for weeks — and it read as scenery, because a
    # bare measurement with no stated limit gives a reader nothing to fail. The
    # figure has to arrive already compared to something or it is furniture.
    # A MEASUREMENT YOU SEE EVERY DAY STOPS BEING READ.
    share = (100.0 * prose / total) if total else 0.0
    verdict = "over" if prose > rows else "under"
    print(f"table: prose is {share:.0f}% of the summary section "
          f"({prose:,} B prose / {rows:,} B rows, ~{total // 4:,} tok of boot) "
          f"— {verdict} the 50% threshold")
    if prose > rows:
        print(f"  ! OVER THRESHOLD. A tally is a count; once it explains why each row "
              f"closed it is a second copy of {TASKS_DIRNAME}/closed/, and it drifts from "
              "both. Cut it back to a count. (Reported, not blocked.)")
    return rows, prose


# ── STATUS size report ───────────────────────────────────────────────────────

# Soft threshold, not a cap: nothing is refused, and no session should raise this
# number instead of trimming. It sits above a healthy file and below the size the
# real one reached before anyone counted it.
STATUS_SOFT_MAX = 20480
STATUS_SOFT_MAX_ENV = "ARCH_STATUS_MAX"


def report_status_size(root, soft_max=None):
    """Measure the DECLARED STATUS file per top-level field.

    THE FOURTH PLACE THE GROWTH WENT (MP#48). The split capped descriptions;
    report_subjects capped the row cells they fled into; report_table_prose
    caught the paragraphs around the table. None of them watch the OTHER boot
    file that grows. `config/STATUS.yaml` reached 29,253 B — second only to the
    ledger, about a third of everything a session pays before doing any work —
    while its own header declared overwrite mode and said the log of how state
    got here belongs in the task ledger. Roughly twenty `in_flight:` entries had
    become closure narratives already held in full by tasks/closed/: a THIRD copy
    of state, drifting from both and announcing nothing when it did.

    Per-FIELD, not just a total, for the reason the meter reports per-file — the
    question later is never "did it grow" but WHICH field grew, and the answer is
    almost always the one holding a list.

    A soft threshold rather than a ratio, because unlike the summary table there
    is no companion quantity to weigh narrative against; a snapshot has no rows.
    The honest signal is absolute size plus the field breakdown that says where
    to look. **The fix is always to cut, never to raise the number** — the
    manifest's own doctrine, that a cap you raise on reflex is not a cap.

    Reports; never blocks. Returns (total_bytes, [(field, bytes), …])."""
    path = find_status_file(root)
    if not path or not os.path.isfile(path):
        print("status-size: no STATUS file declared or found — skipped")
        return 0, []
    if soft_max is None:
        try:
            soft_max = int(os.environ.get(STATUS_SOFT_MAX_ENV) or STATUS_SOFT_MAX)
        except ValueError:                  # a junk env value must not break a close
            soft_max = STATUS_SOFT_MAX
    text = read(path)
    fields, cur = [], None
    for line in text.splitlines(keepends=True):
        n = len(line.encode("utf-8"))
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m:
            cur = [m.group(1), 0]
            fields.append(cur)
        if cur is not None:
            cur[1] += n
    total = len(text.encode("utf-8"))
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    print(f"status-size: {rel} {total:,} B (~{total // 4:,} tok of boot)")
    for name, n in sorted(fields, key=lambda f: -f[1])[:3]:
        print(f"    {name}: {n:,} B")
    if total > soft_max:
        print(f"  ! over the {soft_max:,} B soft threshold. STATUS is a SNAPSHOT — "
              "check whether a field has become a log of closed work that "
              f"{TASKS_DIRNAME}/closed/ already holds. Cut it; do not raise this "
              "number. (Reported, not blocked.)")
    return total, [(n, b) for n, b in fields]


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


# ── STATUS-facts report ──────────────────────────────────────────────────────
#
# STATUS is dense with numbers a human copied in by hand — manifest caps, byte
# counts, boot-token figures, suite counts, commit SHAs — and NOTHING recomputed
# any of them. Written once by whichever session happened to measure something,
# they go stale in silence, because STATUS is overwrite-mode and carries no diff
# between "this was true when written" and "this is true now". Measured on this
# silo: SEVEN stale facts in two days, every one caught by eye. A catch rate that
# depends on who happened to look is not a mechanism.
#
# THIS REPORTS AND NEVER REWRITES, and that is the deliberate half. reconcile_tally
# writes because a tally is a bare count with a line of its own. These numbers sit
# INSIDE prose that carries the reasoning around them — rewriting one would mangle
# the argument it is embedded in. Same distinction, opposite side: a check that
# needs no judgement rewrites, a check whose subject needs judgement reports.
#
# THE ENEMY IS NOISE, not coverage. A checker that flags prose gets ignored, and
# an ignored report is worth less than no report. So every claim below must
# SELF-IDENTIFY before it is checked — a byte figure needs a real path beside it,
# a suite count needs a real test file, a short SHA needs a commit cue. Anything
# that does not identify itself is prose and passes in silence. The cost is real
# claims missed; the benefit is that a `!` line here always means something. That
# trade also gives STATUS a house style: phrase a fact so this can check it.

STATUS_SUFFIX = "status.yaml"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}

# How far under the instance root to look for sibling git repos. See _git_roots.
MAX_REPO_DEPTH = 3

# A number quoted in single quotes is CITED, not asserted. STATUS deliberately
# quotes superseded figures to record why they were wrong ("the previous entry
# said '99% OF ITS CAP (12,153 / 12,288)'"), and flagging those would fire every
# close forever on a sentence that is doing its job. The lookarounds keep
# apostrophes out of it — in "the silo's cap" the quote sits between two word
# characters and opens nothing.
QUOTED_RE = re.compile(r"(?<!\w)'([^'\n]{1,400})'(?!\w)")

PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-/]+")
CLAIM_EXTS = (".yaml", ".yml", ".md", ".py", ".json", ".jsonl", ".tsv",
              ".html", ".txt", ".toml", ".cfg", ".sh", ".ps1")
SIZE_CLAIM_RE = re.compile(r"(\d[\d,]*)\s*B\b")
SIZE_WINDOW = 40          # chars a path may sit before its byte figure

# Both ends are bounded so a ratio cannot be cut out of a longer token: a short
# hash pair (`937b6c2/6e15382`) must not read as `2 / 6`, and a date (`2026/08/05`)
# must not read as `2026 / 08`. The trailing bound admits a full stop — a cap claim
# routinely ends a sentence — but not a dot followed by a digit, which is the date.
CAP_PAIR_RE = re.compile(
    r"(?<![0-9A-Za-z,./])(\d[\d,]*)\s*/\s*(\d[\d,]*)(?![0-9A-Za-z,]|[./]\d)")
CAP_CUE_RE = re.compile(r"\b(cap|max_bytes)\b", re.I)
CAP_CUE_WINDOW = 60

# 7-40 hex with at least one DIGIT: a run of pure [a-f] letters is an English
# word far more often than a commit ("defaced"), and a real short hash without a
# digit is a 1-in-1000 accident.
SHA_RE = re.compile(r"(?<![0-9A-Za-z])((?=[0-9a-f]*\d)[0-9a-f]{7,40})(?![0-9A-Za-z])")
SHA_CUES = ("commit", "sha", "origin", "head", "pushed", "push", "pin",
            "pairing", "branch", "main", "revision", "rev-parse", "@")
SHA_WINDOW = 60

# `sha256:64305da8` is a CONTENT DIGEST, not a commit — and the literal "sha256"
# contains the cue "sha", so the one token in the sentence that says *this is not
# a git commit* was the token promoting it to one. The strongest available
# disambiguator, read backwards. Found 2026-08-05 against a second instance whose
# STATUS carries live ECR image digests (MP#47/D1); three false positives there.
#
# Checked BEFORE the unconditional 40-char rule, not after: a sha1 digest is
# itself 40 hex characters, so a suffix-only fix would still fire on one. Only
# algorithm prefixes are suppressed — `commit:`/`origin:` stay cues, because
# "commit: 7760f30" is exactly the pin this check exists to verify.
# Matches the WHOLE `<algo>:<hex>` token, not just its prefix, because the token
# has to be removed from the cue text as a unit — see _mask_digests and MP#49.
# 7-64 hex covers a short form through a full sha256.
DIGEST_TOKEN_RE = re.compile(r"(?:sha\d+|md5|blake2[bs])\s*:\s*[0-9a-f]{7,64}", re.I)


def _cue_matcher(cues):
    """Compile the cue list into one regex that respects WORD BOUNDARIES.

    THE DEFECT THIS FIXES (MP#51, found on a second instance 2026-08-06): cues
    were tested with plain substring containment, so every one of them also
    fired inside ordinary English. `pin` matched in "re**pin**s", `main` in
    "do**main**", `head` in "beach**head**", `origin` in "**origin**al",
    `commit` in "un**commit**ted". Any bare hex within SHA_WINDOW of one of
    those words was promoted to a commit claim and then reported as resolving
    nowhere. The observed case was `3ee8019` convicted by "rollback **repins**
    the digest" — and MP#49 was opened, and shipped, believing the neighbouring
    `sha256:` digest was the culprit. It was not.

    THIS SILO NEVER SAW IT AND WAS NEVER IMMUNE: its own STATUS already carries
    "domain", "original" and "headline"; no hex happens to sit near them. The
    hazard is also WIDER since MP#47/D2 taught repo discovery to descend, because
    discovered repo names join this list — `web` here, `4cite` there — so the
    same code is differently dangerous depending on what the folders are called.

    Boundaries are applied per-END, not blindly: `\\b` only means anything next
    to a word character, so `@` stays a bare literal while `rev-parse` — letters
    at both ends — gets both. Non-alphanumeric cues are unchanged by design.

    NOT fixed here, deliberately: whether repo BASENAMES belong in the cue list
    at all. `web` is a weak cue even as a whole word. That is a judgement call,
    and bundling it with a mechanical fix is how the mechanical part stops being
    reviewable — see the row."""
    parts = []
    for c in cues:
        c = (c or "").strip().lower()
        if not c:
            continue
        parts.append((r"\b" if c[0].isalnum() else "")
                     + re.escape(c)
                     + (r"\b" if c[-1].isalnum() else ""))
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!)")


def _mask_digests(text):
    """Blank every `<algo>:<hex>` token, preserving offsets.

    THE DEFECT THIS FIXES (MP#49, found on a second instance 2026-08-05):
    suppressing a digest removed it from the FINDINGS but left its text in the
    line, so its `sha` substring stayed in the cue window and promoted the next
    bare hex on the same line. `3ee8019` — prose about an image, not a pin — was
    convicted by the `sha256:d32dfac2` sitting beside it, the very token that had
    just been ruled out. Suppression has to withdraw the cue, not only the match.

    Length-preserving on purpose: every offset in this module is computed against
    the original text (quote spans, line numbers, windows), so the masked copy has
    to stay in lockstep with it. Replacing with spaces is what guarantees that.

    Scoped to `<algo>:` forms only. `commit:` and `origin:` stay cues — over-
    withdrawal would silently stop checking real pins, which fails quiet and is
    worse than the loud false positive this removes."""
    if not DIGEST_TOKEN_RE.search(text):
        return text
    out = list(text)
    for m in DIGEST_TOKEN_RE.finditer(text):
        out[m.start():m.end()] = " " * (m.end() - m.start())
    return "".join(out)

SUITES_RE = re.compile(r"\bsuites?\b([^.\n]{0,300})", re.I)
# A suite claim is a RUN of `N name` items directly after the word, not any such
# pair loose in the sentence. "the suite shipped … MP#42 to stop that" contains
# the pair "42 to" and means nothing by it; anchoring the run at the word keeps
# an ordinary sentence from manufacturing a suite called `to`.
SUITE_LIST_RE = re.compile(r"^[\s:=]*((?:\d+\s+[A-Za-z][A-Za-z0-9_]*(?:\s*[/,]\s*)?)+)")
SUITE_PAIR_RE = re.compile(r"(\d+)\s+([A-Za-z][A-Za-z0-9_]*)")
TEST_METHOD_RE = re.compile(r"^[ \t]+def (test\w*)\s*\(", re.M)

BOOT_CLAIM_RE = re.compile(r"\bboot\s+cost\b\D{0,24}?(\d[\d,]*)\s*tokens?"
                           r"(?:\s*/\s*(\d[\d,]*)\s*B\b)?", re.I)


def _n(s):
    return int(str(s).replace(",", ""))


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _finding(line, kind, subject, claimed, measured, note=""):
    return {"line": line, "kind": kind, "subject": subject,
            "claimed": claimed, "measured": measured, "note": note}


def _quoted_spans(text):
    return [(m.start(), m.end()) for m in QUOTED_RE.finditer(text)]


def _is_quoted(pos, spans):
    return any(a <= pos < b for a, b in spans)


def _path_shaped(tok):
    """Is this token a PATH, or just a word with a dot in it?

    `manifest_rules.max_bytes` is a YAML key, not a file — it has a dot and must
    not be resolved as one. Requiring a known extension or a slash separates them,
    and the absolute forms (drive letters, leading slash) are dropped because a
    claim about a path outside the instance is not this instance's to measure."""
    if tok.startswith(("http:", "https:", "/")) or ":" in tok:
        return False
    return tok.lower().endswith(CLAIM_EXTS) or "/" in tok


def find_status_file(root, manifest_name=None):
    """The STATUS file this instance DECLARES in its manifest `boot:` list.

    Read from the declaration, never hardcoded: genesis prefixes the loader stack
    to config/<PROJECT>_STATUS.yaml, so a hardcoded config/STATUS.yaml silently
    checks nothing on every instance past its own genesis — a checker that reports
    clean because it found no file is the worst possible failure for this pass."""
    name = os.path.basename(manifest_name or os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml")
    rels = []
    p = os.path.join(root, name)
    if os.path.exists(p):
        m = re.search(r"(?ms)^boot:[^\n]*\n(.*?)(?=^\S|\Z)", read(p))
        if m:
            rels = [x.group(1) for x in re.finditer(r"^\s*-\s*([^\s#]+)", m.group(1), re.M)]
    rels = [r for r in rels if os.path.basename(r).lower().endswith(STATUS_SUFFIX)]
    for r in rels + ["config/STATUS.yaml"]:
        q = os.path.join(root, r.replace("/", os.sep))
        if os.path.exists(q):
            return q
    return None


def manifest_max_bytes(path):
    """integrity.manifest_rules.max_bytes — the cap g5 enforces on that manifest.

    Scoped to the manifest_rules block and returning None when it is absent: an
    unscoped `max_bytes:` search finds close.journal.max_bytes first, which is a
    different cap on a different file, and the two happen to be equal in this silo
    — the exact coincidence that would make a wrong reading look right."""
    try:
        text = read(path)
    except OSError:
        return None
    try:
        import yaml  # type: ignore
        rules = ((yaml.safe_load(text) or {}).get("integrity") or {}).get("manifest_rules") or {}
        if isinstance(rules.get("max_bytes"), int):
            return rules["max_bytes"]
    except Exception:  # noqa: BLE001 — yaml missing or manifest not valid yaml
        pass
    m = re.search(r"(?ms)^\s*manifest_rules:[^\n]*\n(.*?)(?=^\s{0,2}\S|\Z)", text)
    if not m:
        return None
    mb = re.search(r"^\s*max_bytes:\s*(\d+)", m.group(1), re.M)
    return int(mb.group(1)) if mb else None


def discover_manifests(root, manifest_name=None):
    """Every ARCH manifest reachable from root: this instance's, plus any nested
    repo shipping one (the product repo does). [(relpath, bytes, max_bytes|None)]."""
    name = os.path.basename(manifest_name or os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml")
    rels = [name]
    try:
        rels += [d + "/" + name for d in sorted(os.listdir(root))
                 if d not in SKIP_DIRS and os.path.isdir(os.path.join(root, d))]
    except OSError:
        pass
    out = []
    for rel in rels:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.exists(p):
            out.append((rel, os.path.getsize(p), manifest_max_bytes(p)))
    return out


def _git_roots(root, max_depth=MAX_REPO_DEPTH):
    """[(name, path)] for every git repo at or under `root`, to a bounded depth.

    THE DEFECT THIS FIXES (MP#47/D2, 2026-08-05): this scanned the root plus one
    level of children, and held only because THIS instance is the shallow case —
    `4SYNC-ARCH/` and `web/` are immediate children. A second instance kept its
    repos one level further down (`4CITE/web/`, `4CITE/mcp/`), so every true SHA
    in them was reported as resolving in no repo. That is the worst way for this
    file to fail: flagging CORRECT facts teaches its reader to ignore the report,
    and an ignored report is worth less than none — the exact failure mode the
    status checker was built to avoid, arriving from the side nothing guarded.

    Bounded, never unbounded. `SKIP_DIRS` already drops `node_modules` and friends,
    but a close should not pay for a full-tree walk either; three levels reaches a
    nested-instance layout and stops. Descent continues THROUGH a repo, because a
    repo inside a repo is the normal shape here (`4SYNC/4SYNC-ARCH/`).

    Nested repos are named by their path relative to the root (`4CITE/web`) rather
    than by basename. Two instances can both hold a `web/`, and the name is also
    fed to the SHA cue list — a bare `web` is short enough to fire on ordinary
    prose, where `4cite/web` is not."""
    out = []
    if os.path.isdir(os.path.join(root, ".git")):
        out.append((os.path.basename(root.rstrip("/\\")) or ".", root))

    def walk(path, depth, prefix):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        for d in entries:
            if d in SKIP_DIRS:
                continue
            p = os.path.join(path, d)
            if not os.path.isdir(p):
                continue
            rel = prefix + d
            if os.path.isdir(os.path.join(p, ".git")):
                out.append((d if depth == 1 else rel, p))
            walk(p, depth + 1, rel + "/")

    walk(root, 1, "")
    return out


def resolve_shas(repos, shas):
    """{sha: [repo names it resolves in]}.

    ONE `git cat-file --batch-check` per repo rather than one per SHA: a close
    should not pay thirty process spawns to check eight pins. Output is one line
    per input line, so the answers zip back onto the inputs; a length mismatch
    means git said something unexpected and that repo's answers are discarded
    rather than shifted by one, which would blame the wrong SHA."""
    found = {s: [] for s in shas}
    if not shas:
        return found
    payload = "".join(s + "^{commit}\n" for s in shas)
    for name, path in repos:
        try:
            out = subprocess.run(["git", "cat-file", "--batch-check"], cwd=path,
                                 input=payload, capture_output=True, text=True, timeout=30)
        except Exception:  # noqa: BLE001 — no git, or a repo we cannot read
            continue
        lines = out.stdout.splitlines()
        if len(lines) != len(shas):
            continue
        for sha, line in zip(shas, lines):
            if " missing" not in line and "ambiguous" not in line and line.strip():
                found[sha].append(name)
    return found


def _test_file_index(root):
    idx = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("test_") and fn.endswith(".py"):
                idx.setdefault(fn, []).append(os.path.join(dirpath, fn))
    return idx


def count_test_methods(path):
    """unittest test methods in a file. None when the file has none.

    STATICALLY counted, and NOT extended to guess at hand-rolled harnesses. The
    one in this repo runs 35 assertions from 32 `check(` call sites — some sit in
    loops — so the obvious static count is wrong by three, and a checker reporting
    32 against a correct 35 manufactures drift where there is none. A previous
    session's suite-count parser made exactly this class of error in the other
    direction (75 for a suite of 35). None means 'not statically countable', which
    is reported as a note and never as a finding."""
    try:
        n = len(TEST_METHOD_RE.findall(read(path)))
    except OSError:
        return None
    return n or None


def _check_caps(text, spans, manifests, findings, notes):
    """Manifest cap claims: `N / M`, where M is a byte cap and N a manifest size.

    A pair qualifies as a cap claim only if M is a cap SOME manifest declares, or
    the words `cap`/`max_bytes` sit just before it. Both gates matter and both are
    drawn from observed defects: the live claims here carry no cue and are found
    by M, while the 2026-08-05 defect — '99% OF ITS CAP (12,153 / 12,288)' — was
    wrong PRECISELY IN M, so a cap-matching gate alone could never have seen it.
    The byte count was right; the cap was invented; the wall it warned about did
    not exist."""
    caps = {}
    for rel, size, mx in manifests:
        if mx is None:
            continue
        caps.setdefault(mx, []).append((rel, size))
        if size > mx:
            findings.append(_finding(
                None, "manifest", rel, f"cap {mx:,} B", f"{size:,} B",
                "over its own declared manifest_rules.max_bytes — g5 blocks edits to it"))
    for m in CAP_PAIR_RE.finditer(text):
        if _is_quoted(m.start(), spans):
            continue
        claimed, cap = _n(m.group(1)), _n(m.group(2))
        cued = bool(CAP_CUE_RE.search(text[max(0, m.start() - CAP_CUE_WINDOW):m.start()]))
        if cap not in caps and not cued:
            continue                                    # not a cap claim — prose
        line = _line_of(text, m.start())
        if cap not in caps:
            known = ", ".join(f"{c:,}" for c in sorted(caps)) or "none declared"
            findings.append(_finding(
                line, "cap", m.group(0), f"cap {cap:,}", f"declared caps: {known}",
                "no manifest declares that cap — the CAP is the wrong number, "
                "which no check of the size alone can see"))
            continue
        if any(size == claimed for _, size in caps[cap]):
            continue
        measured = ", ".join(f"{rel} {size:,}" for rel, size in caps[cap])
        findings.append(_finding(line, "cap", f"against cap {cap:,}",
                                 f"{claimed:,} B", measured))


def _check_sizes(root, text, spans, findings):
    """`<path> … N B` — a byte figure with a real path in front of it.

    The path is the whole gate. STATUS is full of unattributed byte figures
    ('4,955 B vs 0'), and a checker that tried to guess their subject would be
    inventing claims to fail. A figure nothing names is prose."""
    for m in SIZE_CLAIM_RE.finditer(text):
        if _is_quoted(m.start(), spans):
            continue
        window = text[max(0, m.start() - SIZE_WINDOW):m.start()]
        cands = [t for t in PATH_TOKEN_RE.findall(window) if _path_shaped(t)]
        if not cands:
            continue
        rel = cands[-1].rstrip(".,;:)")
        line, claimed = _line_of(text, m.start()), _n(m.group(1))
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(p):
            # A token qualifies as path-shaped on a known extension OR a bare
            # slash. The slash alone is too weak to carry a MISSING-file finding:
            # a second instance's sentence "template 80,592B > 51,200B inline
            # limit" put `--template-url/S3` in the window and produced two
            # findings about a file nobody ever claimed existed (MP#47/D3).
            # Requiring a real extension keeps the catch that matters — a claim
            # still naming `config/OLD_NAME.yaml` after a rename — and drops the
            # noise, which is this checker's standing trade: cover less, on
            # purpose, so that what it does say keeps being worth reading.
            if not rel.lower().endswith(CLAIM_EXTS):
                continue
            findings.append(_finding(line, "size", rel, f"{claimed:,} B", "no such file",
                                     "the claim names a path that is not there"))
            continue
        actual = os.path.getsize(p)
        if actual != claimed:
            findings.append(_finding(line, "size", rel, f"{claimed:,} B", f"{actual:,} B"))


def _check_shas(text, spans, repos, findings):
    """Commit SHAs quoted in STATUS resolve in some repo under this root.

    A 40-char hex string is a pin and is checked unconditionally — a session here
    once wrote a FABRICATED 40-char SHA into PAIRING.yaml, invented from a short
    hash rather than asked of git, which is the worst error this file can carry:
    authoritative-looking and pointing at nothing. A SHORT hash is only a pin when
    something nearby says so (a repo name, `origin`, `pushed`, `pins`, `@`).
    Without a cue it is not asserting a commit at all — a session id in prose
    ('the week-old 03721ead row') is 8 hex characters and is not a claim."""
    cues = tuple(SHA_CUES) + tuple(n.lower() for n, _ in repos)
    cue_re = _cue_matcher(cues)
    # ONE mechanism does both jobs: a masked token is neither a candidate itself
    # (D1) nor a cue for its neighbours (MP#49). Two separate checks is what let
    # the second half survive the first fix.
    cue_text = _mask_digests(text)
    todo = []
    for m in SHA_RE.finditer(text):
        if _is_quoted(m.start(), spans):
            continue
        if cue_text[m.start()] == " " != text[m.start()]:
            continue                    # inside `sha256:…` — a digest, not a pin
        if len(m.group(1)) < 40:
            window = cue_text[max(0, m.start() - SHA_WINDOW):m.end() + SHA_WINDOW].lower()
            if not cue_re.search(window):
                continue
        todo.append((m.start(), m.group(1)))
    resolved = resolve_shas(repos, sorted({s for _, s in todo}))
    for pos, sha in todo:
        if not resolved.get(sha):
            findings.append(_finding(
                _line_of(text, pos), "sha", sha, "a commit", "resolves in no repo",
                "repos searched: " + (", ".join(n for n, _ in repos) or "none")))


def _check_suites(root, text, spans, findings, notes):
    """`Suites N name / N name / …` against each suite's test-method count.

    A pair is a claim only when test_<name>.py exists; anything else in that
    sentence is prose. Two named suites can point at the same file (the silo and
    the product carry byte-identical copies, which check_sync enforces) — equal
    counts are one answer, not an ambiguity, and only genuinely disagreeing copies
    are refused."""
    idx = None
    for s in SUITES_RE.finditer(text):
        if _is_quoted(s.start(), spans):
            continue
        run = SUITE_LIST_RE.match(s.group(1))
        if not run:
            continue
        if idx is None:                 # one walk, and only if a claim exists
            idx = _test_file_index(root)
        base = s.start(1) + run.start(1)
        for m in SUITE_PAIR_RE.finditer(run.group(1)):
            claimed, name = int(m.group(1)), m.group(2)
            paths = idx.get("test_%s.py" % name)
            if not paths:
                notes.append("suite '%s' — no test_%s.py under this root, not checkable"
                             % (name, name))
                continue
            counts = {count_test_methods(p) for p in paths}
            if counts == {None}:
                notes.append("suite '%s' — no unittest test methods (hand-rolled harness), "
                             "not statically countable" % name)
                continue
            counts.discard(None)
            if len(counts) > 1:
                notes.append("suite '%s' — copies disagree (%s), not checkable"
                             % (name, ", ".join(str(c) for c in sorted(counts))))
                continue
            actual = counts.pop()
            if actual != claimed:
                findings.append(_finding(
                    _line_of(text, base + m.start()), "suite", name,
                    str(claimed), str(actual), "test methods in " + paths[0]))


def manifest_meter_script(root, manifest_name=None):
    """close.meter.script — the boot meter this instance declares."""
    name = os.path.basename(manifest_name or os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml")
    p = os.path.join(root, name)
    if not os.path.exists(p):
        return None
    m = re.search(r"(?ms)^\s{2}meter:[^\n]*\n(.*?)(?=^\s{0,2}\S|\Z)", read(p))
    if not m:
        return None
    s = re.search(r"^\s*script:\s*([^\s#]+)", m.group(1), re.M)
    return s.group(1) if s else None


def _meter_boot(root, script_rel):
    """(tokens, bytes) from the declared meter's --json, or None.

    The meter is DECLARED, not discovered: close.meter.script is where this
    instance says its meter lives, and in this silo that is the product's copy,
    one directory down. Any failure is a skip — the manifest's own posture for
    this step is on_missing: skip, and a measurement never blocks a close."""
    if not script_rel:
        return None
    p = os.path.join(root, script_rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    try:
        out = subprocess.run([sys.executable, p, "--dir", root, "--json"],
                             capture_output=True, text=True, timeout=120)
        d = json.loads(out.stdout)
        return d.get("boot_total_tokens"), d.get("boot_total_bytes")
    except Exception:  # noqa: BLE001 — no meter, no --json, bad output: skip
        return None


def _check_boot(text, spans, boot, findings):
    if not boot or boot[0] is None:
        return
    tokens, nbytes = boot
    for m in BOOT_CLAIM_RE.finditer(text):
        if _is_quoted(m.start(), spans):
            continue
        line = _line_of(text, m.start())
        if _n(m.group(1)) != tokens:
            findings.append(_finding(line, "boot", "tokens", f"{_n(m.group(1)):,}",
                                     f"{tokens:,}", "measured by the declared meter"))
        if m.group(2) and nbytes is not None and _n(m.group(2)) != nbytes:
            findings.append(_finding(line, "boot", "bytes", f"{_n(m.group(2)):,} B",
                                     f"{nbytes:,} B", "measured by the declared meter"))


def report_status_facts(root, manifest_name=None, run_meter=True):
    """Check STATUS's hand-copied numbers against what they claim to describe.

    Reports; NEVER rewrites, never blocks. Returns (status_path, findings, notes)."""
    status = find_status_file(root, manifest_name)
    if status is None:
        print("status: no STATUS file declared in boot: — skipped")
        return None, [], []
    text = read(status)
    rel = os.path.relpath(status, root).replace(os.sep, "/")
    spans = _quoted_spans(text)
    findings, notes = [], []

    _check_caps(text, spans, discover_manifests(root, manifest_name), findings, notes)
    _check_sizes(root, text, spans, findings)
    _check_shas(text, spans, _git_roots(root), findings)
    _check_suites(root, text, spans, findings, notes)
    if run_meter:
        _check_boot(text, spans, _meter_boot(root, manifest_meter_script(root, manifest_name)),
                    findings)

    print(f"status: {rel} — {len(findings)} claim(s) disagree with what they describe")
    for f in findings[:12]:
        where = f"line {f['line']}" if f["line"] else "measured"
        print(f"  ! {where} · {f['kind']} {f['subject']} — "
              f"claimed {f['claimed']}, measured {f['measured']}")
        if f["note"]:
            print(f"      {f['note']}")
    if len(findings) > 12:
        print(f"  … and {len(findings) - 12} more")
    for n in notes[:6]:
        print(f"  · {n}")
    if findings:
        print("  A stale fact here costs a future session either wasted work or a wrong "
              "decision made confidently, and both look correct at the time. Fix the "
              "sentence, not the checker. (Reported, not blocked — these numbers sit "
              "inside prose that carries the reasoning, so nothing here rewrites them.)")
    return status, findings, notes


# ── pickup-ready report ──────────────────────────────────────────────────────

PICKUP_RE = re.compile(r"^\*\*Pickup-ready[^\n]*$", re.M)
# `#NN`, but never `MP#NN`. A ledger cross-reference names a task in an argument
# ("the same disease MP#39 cured"); a bare `#NN` names a row in the list. Letting
# the two collide made a closed row look like a pickup candidate on the real file.
ROW_REF_RE = re.compile(r"(?<![A-Za-z])#(\d+)")
# An unfilled template placeholder — the product ships this line as
# "[List the ⏳ task IDs …]". Reporting every pending row as missing from a
# placeholder would greet each new adopter with a defect on the first close,
# which is how a report-only check loses its audience before it has one.
PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{3,}\](?!\()")
# The same row number written in a shape this check does not count. `MP-003` is
# ARCH's CANONICAL ID form everywhere else — the task document path derives from
# it (tasks/MP-003.md) — so an adopter who writes it there is following house
# style, and "pending but not named" tells them the opposite of what is true.
# Observed: it cost the first outside adopter two wrong fixes and a source read
# before he found the pattern himself. The parser is right; the MESSAGE was wrong.
OTHER_FORM_RE = re.compile(r"(?<![A-Za-z])(MP[-#]0*(\d+)|[Rr]ow\s+(\d+))")


def _named_in_another_form(segment):
    """{row number: the literal text that named it} for shapes not counted.

    Reported, never accepted — widening ROW_REF_RE instead would re-open the
    collision it was narrowed to close (`MP#39` inside an argument is not a
    pickup entry). Naming the shape found is enough to fix in one edit."""
    found = {}
    for m in OTHER_FORM_RE.finditer(segment):
        found.setdefault(int(m.group(2) or m.group(3)), m.group(1))
    return found


def report_pickup_ready(ledger_path):
    """The ledger's hand-maintained "Pickup-ready right now" list vs the table.

    Same disease as STATUS in the ledger's own file: the paragraph names the rows
    a session should pick up, nothing derives it, and it has already drifted in
    both directions — it named a row for a day after that row closed, and omitted
    another for the same day. The set IS derivable; the prose around each ID is
    not, which is why this reports rather than rewrites.

    IDs are matched as `#NN`. A historical mention inside this paragraph should be
    written 'row NN' so it does not read as a pickup candidate — the paragraph is
    a list with commentary, and the commentary must not enter the list."""
    text = read(ledger_path)
    m = PICKUP_RE.search(text)
    if not m or PLACEHOLDER_RE.search(m.group(0)):
        return None, None
    table = summary_table_section(text)
    if table is None:
        print("pickup: no '## Summary table' found — skipped")
        return None, None
    pending = {int(r.group(1)) for r in TABLE_ROW_RE.finditer(table)
               if "⏳" in r.group(2)}
    named = {int(x) for x in ROW_REF_RE.findall(m.group(0))}
    missing, extra = sorted(pending - named), sorted(named - pending)
    line = _line_of(text, m.start())
    if not missing and not extra:
        print(f"pickup: {len(pending)} pending row(s), all named in the list ✓")
        return missing, extra
    print(f"pickup: line {line} — the 'Pickup-ready' list disagrees with the table")
    if missing:
        forms = _named_in_another_form(m.group(0))
        unnamed = [t for t in missing if t not in forms]
        if unnamed:
            print("  ! pending but not named: " + ", ".join(f"#{t}" for t in unnamed))
        for t in (t for t in missing if t in forms):
            print(f"  ! pending and named as `{forms[t]}`, which this check does not "
                  f"count — write it as a bare `#{t}` to enter the list")
    if extra:
        print("  ! named but not pending: " + ", ".join(f"#{t}" for t in extra))
    print("  The list is prose with an argument in it, so this reports rather than "
          "rewrites. (Reported, not blocked.)")
    return missing, extra


# ── findings report ──────────────────────────────────────────────────────────

FINDINGS_FILENAME = "FINDINGS.md"


# ── Boot-growth alert ──────────────────────────────────────────────────────────

# Growth beyond this share since the last logged close is worth a session's
# attention. Not a cap and nothing is refused: boot legitimately grows when a
# journal block lands. The point is that it should grow VISIBLY and by an amount
# someone chose, rather than by accumulation nobody watched — which is exactly how
# the ledger became 70% teaching prose (MP#56).
BOOT_GROWTH_ALERT_PCT = 15.0
ROC_SERIES_REL = os.path.join("metrics", "roc_series.jsonl")


def report_boot_growth(root, manifest_name=None, run_meter=True, pct=BOOT_GROWTH_ALERT_PCT):
    """Compare boot cost now against the last row the meter logged.

    MP#56, absorbed from MP#54 Finding 7 #7. The meter already writes a per-close
    series; nothing ever read it back. A trend nobody compares against is the same
    failure as a measurement nobody reads — see report_table_prose.

    Reports; never blocks. Returns (now_tokens, prev_tokens) or None when it cannot
    compare, which it says out loud rather than passing silently."""
    if not run_meter:
        return None
    script = manifest_meter_script(root, manifest_name)
    if not script:
        return None
    got = _meter_boot(root, script)
    if not got:
        return None
    now = got[0]

    series = os.path.join(root, ROC_SERIES_REL)
    if not os.path.exists(series):
        print(f"boot-growth: no {ROC_SERIES_REL} yet — nothing to compare "
              f"(boot is {now:,} tok; the next close starts the series)")
        return None
    prev = None
    try:
        for line in read(series).splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row.get("boot_tokens"), int):
                prev = row["boot_tokens"]
    except Exception:  # noqa: BLE001 — a malformed series must not fail a close
        prev = None
    if not prev:
        print(f"boot-growth: {ROC_SERIES_REL} carries no usable boot figure — skipped")
        return None

    delta = now - prev
    share = (100.0 * delta / prev) if prev else 0.0
    arrow = "+" if delta >= 0 else ""
    print(f"boot-growth: {now:,} tok vs {prev:,} at the last logged close "
          f"({arrow}{delta:,}, {arrow}{share:.1f}%) — threshold {pct:.0f}%")
    if share > pct:
        print(f"  ! OVER THRESHOLD. Boot grew {share:.1f}% since the last close. "
              "Run the meter with --json to see WHICH file grew; the per-file "
              "breakdown is the whole reason it reports per file. (Reported, not blocked.)")
    return now, prev


# ── Manifest compliance AT REST ────────────────────────────────────────────────

# g5 judges a WRITE. Nothing judged the file sitting on disk, and the two are not
# the same question: a manifest can arrive non-compliant through any path the hook
# does not cover — a Bash redirect (g5 can only `ask` there, and `ask` resolves to
# ALLOW where no prompt can be shown), hooks unwired, an edit made on another
# machine, or a file that was already wrong before the rule existed. The failure is
# silent until the NEXT write is refused, and it then reads as "the guard is
# broken" rather than "this file is non-compliant" — which is exactly how the
# original write-lock episode was misdiagnosed (MP#54).
#
# The file's own recorded lesson, one rule over: a guard on the door says nothing
# about what is already in the room.
_DATE_RE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")


def report_manifest_at_rest(root, manifest_name=None):
    """Check every reachable manifest for the rules g5 enforces on write.

    Two checks, both cheap and both things g5 cannot see from the door:
      - does it still PARSE (when PyYAML is available)
      - if it declares declaration_only, is it free of calendar dates

    Reports; never blocks. Returns a list of (relpath, problem) findings."""
    found = []
    for rel, _size, _cap in discover_manifests(root, manifest_name):
        p = os.path.join(root, rel.replace("/", os.sep))
        try:
            text = read(p)
        except OSError:
            continue

        parsed_ok = None
        try:
            import yaml  # type: ignore
            try:
                yaml.safe_load(text)
                parsed_ok = True
            except Exception as exc:  # noqa: BLE001 — the finding, not a reason to skip
                parsed_ok = False
                mark = getattr(exc, "problem_mark", None)
                where = (" line %d" % (mark.line + 1)) if mark is not None else ""
                found.append((rel, "does NOT parse as YAML%s — %s"
                              % (where, (getattr(exc, "problem", None) or "invalid").strip())))
        except Exception:  # noqa: BLE001 — PyYAML absent; the date check still runs
            pass

        if parsed_ok is not False and re.search(r"(?m)^\s*declaration_only:\s*true\b", text):
            m = _DATE_RE.search(text)
            if m:
                line = text[:m.start()].count(chr(10)) + 1
                found.append((rel, "declares declaration_only but carries the date %s on line %d "
                                   "— g5 will refuse the NEXT write to this file, whoever makes it"
                              % (m.group(1), line)))

    if not found:
        print("manifest-at-rest: all reachable manifests compliant "
              "(parse + declaration_only) ✓")
    else:
        for rel, problem in found:
            print("  ! manifest-at-rest  %s %s" % (rel, problem))
        print("manifest-at-rest: %d finding(s). g5 guards WRITES; this is the file on disk. "
              "(Reported, not blocked.)" % len(found))
    return found


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
    # Explicit no-op. The usage block above has always documented `--dry-run`, and
    # argparse rejected it as unknown — so the first flag a cautious adopter reaches
    # for was the one guaranteed to fail (MP#47/D5). Registering it is the honest
    # fix; deleting the help line would have left them right and the tool wrong.
    ap.add_argument("--dry-run", action="store_true",
                    help="explicit no-op — dry-run is the default; pass --apply to write")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--journal-max-bytes", type=int, default=None,
                    help="override the manifest's close.journal.max_bytes")
    ap.add_argument("--subject-max", type=int, default=SUBJECT_MAX_DEFAULT,
                    help="summary-table Subject length to report over (0 disables)")
    ap.add_argument("--no-meter", action="store_true",
                    help="skip the boot-cost claim check (does not run the declared meter)")
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
        report_pickup_ready(ledger)
    report_status_size(d)
    report_status_facts(d, run_meter=not args.no_meter)
    report_boot_growth(d, run_meter=not args.no_meter)
    report_manifest_at_rest(d)
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
