#!/usr/bin/env python3
"""
4SYNC ARCH — ONE-TIME ledger migration: inline descriptions → tasks/MP-0NN.md.

Run once per instance, when adopting the short-form ledger. Reads MERGE_PLAN.md
(and MERGE_PLAN_ARCHIVE.md if the instance still has one), writes one document
per task into tasks/ (live rows) or tasks/closed/ (terminal rows), then rewrites
the ledger with every inline description removed.

STATUS COMES FROM THE SUMMARY TABLE, never from a heading emoji — the table is
the source of truth and the emoji beside a description is a copy that can drift.

────────────────────────────────────────────────────────────────────────────────
WHY THIS REFUSES MORE THAN IT USED TO
────────────────────────────────────────────────────────────────────────────────
The first version bounded its description scan on the `## Task descriptions`
heading. That held on the ledger it was written against and nowhere else: the
first real adopter ledger keeps 23 of its 47 `### #NNN` blocks ABOVE that heading,
interleaved in the summary-table section. A run there would have migrated 25,
orphaned 22, and left a ledger that LOOKS migrated.

Silent partial migration is the worst failure available to this script. It is a
one-time, irreversible, whole-file restructure; there is no second run to catch
what the first one dropped, and nothing downstream announces the gap. So the
scan is bounded by TABLE-ROW COVERAGE, not by a heading — every `### #NNN` block
anywhere in the file is collected — and anything that does not reconcile is
FATAL rather than a warning.

The fatal set is deliberately asymmetric, because the two directions are not the
same kind of problem:

  FATAL  a description whose id has no table row      → the ledger disagrees
         with itself; migrating would strand the doc under an id nobody tracks.
  FATAL  two descriptions sharing one id              → no way to choose; a
         silent pick would delete one of them.
  FATAL  an OPEN row with no description              → this is exactly what
         rotate.py rejects at the very next close ("a row with no document is a
         task nobody can execute"). Migrating first would hand the instance a
         ledger that cannot be closed. Write those documents by hand, then run.
  REPORT a TERMINAL row with no description           → harmless. A closed task's
         document is never in the boot path, so there is nothing to fix, and
         demanding one would make the migration impossible on any mature ledger
         (91 of one real ledger's 119 rows are terminal and 67 carry no description).

A bijection in BOTH directions is the intuitive rule and it is wrong — it would
refuse forever on exactly the ledgers this exists to migrate.

Usage:
  python scripts/split_ledger.py --dir /path/to/instance [--apply]
"""

import argparse
import os
import re
import sys

TERMINAL = {"completed", "dropped"}
TASKS_DIRNAME = "tasks"
CLOSED_DIRNAME = "closed"

# `### #111 — Subject ✅`  ·  the dash is optional, the trailing status mark is stripped
# `(?![-\w])` is load-bearing: without it a SUB-SECTION heading — `### #32-original-design-context`
# — parses as a second description for task 32, and the duplicate refusal then names a collision
# the ledger does not contain (often in another file this scans), which no inspection can find.
HEADING_RE = re.compile(r"^### #(\d+)(?![-\w])[ \t]*(?:—|--|–|:)?[ \t]*(.*?)[ \t]*$", re.M)
# a block ends at the next heading of either level
BLOCK_END_RE = re.compile(r"^#{2,3} ", re.M)
TABLE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|", re.M)
FOOTER_RE = re.compile(r"\n---\n+\*[^\n]*\*\s*$", re.S)
STATUS_TAIL_RE = re.compile(r"[\s✅⏳⏸️🔄❌]+$")

DOC_HEADER = """# MP#{id} — {subject}

<!-- Long form for row {id} of MERGE_PLAN.md.
     STATE (status, blocked-by, owner) lives in that table and ONLY there.
     Do not repeat it here — two copies of state drift and neither announces it. -->

"""


def _utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_utf8_stdout()


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def atomic_write(p, s):
    tmp = p + ".split_tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def strip_footer(text):
    """Return (body, footer). The footer must never ride into the last task doc."""
    m = FOOTER_RE.search(text)
    return (text, "") if not m else (text[: m.start()], text[m.start():])


def parse_table(ledger):
    """{task_id: status_word} from the canonical summary table.

    Bounded by the next `## ` OR `### ` heading: on an unmigrated ledger the
    description blocks sit inside this very section, and a `## `-only bound
    scans them for `| N |` rows."""
    m = re.search(r"^## Summary table[ \t]*$", ledger, re.M)
    if not m:
        return None
    tail = ledger[m.end():]
    nxt = re.search(r"^#{2,3} ", tail, re.M)
    table = tail[: nxt.start()] if nxt else tail
    out = {}
    for row in TABLE_ROW_RE.finditer(table):
        sym = row.group(2)
        if "✅" in sym:
            out[int(row.group(1))] = "completed"
        elif "❌" in sym:
            out[int(row.group(1))] = "dropped"
        elif "⏸" in sym:
            out[int(row.group(1))] = "blocked"
        elif "🔄" in sym:
            out[int(row.group(1))] = "in_progress"
        else:
            out[int(row.group(1))] = "pending"
    return out


def collect_descriptions(text):
    """Every `### #NNN` block ANYWHERE in the file → [(id, subject, body, start, end)].

    Deliberately not bounded by `## Task descriptions`. That heading is a
    convention some ledgers keep and some do not, and a scan anchored on it
    silently skips every block that sits above it."""
    body, _footer = strip_footer(text)
    heads = list(HEADING_RE.finditer(body))
    items = []
    for i, h in enumerate(heads):
        nxt = BLOCK_END_RE.search(body, h.end())
        stop = nxt.start() if nxt else len(body)
        subject = STATUS_TAIL_RE.sub("", h.group(2)).strip()
        items.append((int(h.group(1)), subject,
                      body[h.end():stop].strip("\n"), h.start(), stop))
    return items


def excise(text, spans):
    """Remove [start, end) spans, then tidy the seams.

    Drops a `## Task descriptions` heading left holding nothing, and the `---`
    rule immediately above it — an empty section heading is a pointer to content
    that no longer exists."""
    out = text
    for start, end in sorted(spans, key=lambda s: -s[0]):
        out = out[:start] + out[end:]
    m = re.search(r"^## Task descriptions.*$", out, re.M)
    if m:
        rest = out[m.end():]
        nxt = re.search(r"^#{2,3} ", rest, re.M)
        remainder = rest[: nxt.start()] if nxt else rest
        if not remainder.strip().strip("-"):
            cut = m.start()
            lead = re.search(r"(?:^|\n)(?:---[ \t]*\n)?\s*$", out[:cut])
            if lead:
                cut = lead.start() + 1 if lead.start() > 0 else lead.start()
            out = out[:cut] + (rest[nxt.start():] if nxt else "")
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    return out if out.endswith("\n") else out + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="instance root (holds MERGE_PLAN.md)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--allow-no-final-newline", action="store_true",
                    help="proceed even if the ledger does not end in a newline")
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    ledger_p = os.path.join(root, "MERGE_PLAN.md")
    archive_p = os.path.join(root, "MERGE_PLAN_ARCHIVE.md")
    if not os.path.exists(ledger_p):
        sys.exit(f"FATAL: no MERGE_PLAN.md in {root}")

    ledger = read(ledger_p)

    # A one-time irreversible restructure must not run on a file that may already
    # be missing its tail. A ledger that does not end in a newline is the cheap,
    # reliable signature of an interrupted write — and this is not hypothetical:
    # a real adopter ledger was found truncated mid-word, having silently lost
    # ~1 KB off the end of its last description months earlier, with every commit
    # since preserving the damage. Migrating that file would have carried the damage into a
    # task document and deleted the evidence from the ledger in one step.
    if not ledger.endswith("\n") and not args.allow_no_final_newline:
        sys.exit("FATAL: MERGE_PLAN.md does not end in a newline — it may be "
                 "TRUNCATED. Check the tail against git history and repair it "
                 "before migrating. (--allow-no-final-newline overrides.)")

    status = parse_table(ledger)
    if status is None:
        sys.exit("FATAL: no '## Summary table' found — cannot resolve task state")

    items = collect_descriptions(ledger)
    ledger_spans = [(s, e) for _, _, _, s, e in items]
    archive_items = []
    if os.path.exists(archive_p):
        archive_items = collect_descriptions(read(archive_p))

    seen, dupes = {}, []
    for tid, subject, body, _s, _e in items + archive_items:
        if tid in seen:
            dupes.append(tid)
        else:
            seen[tid] = (subject, body)

    desc_no_row = sorted(t for t in seen if t not in status)
    open_no_desc = sorted(t for t, st in status.items()
                          if st not in TERMINAL and t not in seen)
    term_no_desc = sorted(t for t, st in status.items()
                          if st in TERMINAL and t not in seen)

    print(f"  ledger:       {len(ledger.encode()):,} B")
    print(f"  table rows:   {len(status)}  ({len(status) - len(term_no_desc) - len(open_no_desc)} with a description)")
    print(f"  descriptions: {len(items) + len(archive_items)} blocks, {len(seen)} distinct"
          + (f" ({len(archive_items)} from MERGE_PLAN_ARCHIVE.md)" if archive_items else ""))
    if term_no_desc:
        print(f"  terminal rows with no description: {len(term_no_desc)} — fine, no doc will be written")
    print()

    fatal = []
    if dupes:
        fatal.append(f"{len(set(dupes))} task id(s) have TWO descriptions: "
                     f"{sorted(set(dupes))} — resolve by hand, a silent pick deletes one")
    if desc_no_row:
        fatal.append(f"{len(desc_no_row)} description(s) have NO table row: {desc_no_row} "
                     "— add the rows or delete the blocks")
    if open_no_desc:
        fatal.append(f"{len(open_no_desc)} OPEN row(s) have NO description: {open_no_desc} "
                     "— rotate.py rejects these at the next close; write "
                     f"{TASKS_DIRNAME}/MP-0NN.md for each first")
    if fatal:
        print("REFUSING — the ledger does not reconcile:")
        for f in fatal:
            print(f"  ✗ {f}")
        print("\nNothing was written. This migration runs ONCE and cannot be re-run to\n"
              "pick up what a partial pass missed, so it refuses rather than half-migrate.")
        sys.exit(1)

    plan = []
    for tid in sorted(seen):
        subject, body = seen[tid]
        sub = CLOSED_DIRNAME if status[tid] in TERMINAL else ""
        rel = os.path.join(TASKS_DIRNAME, sub, f"MP-{tid:03d}.md")
        plan.append((rel, DOC_HEADER.format(id=tid, subject=subject) + body + "\n",
                     status[tid]))

    for rel, content, st in plan[:12]:
        print(f"  {rel:28} {st:11} {len(content.encode()):7,} B")
    if len(plan) > 12:
        print(f"  … and {len(plan) - 12} more")

    _, footer = strip_footer(ledger)
    new_ledger = excise(strip_footer(ledger)[0], ledger_spans).rstrip("\n") + "\n"
    if footer:
        new_ledger += footer.lstrip("\n")
        if not new_ledger.endswith("\n"):
            new_ledger += "\n"

    print()
    print(f"  MERGE_PLAN.md  {len(ledger.encode()):,} B → {len(new_ledger.encode()):,} B "
          f"({100 - len(new_ledger.encode()) * 100 // max(len(ledger.encode()), 1)}% smaller)")
    print(f"  footer preserved: {footer.strip()[:60]!r}" if footer else "  (no footer)")

    if not args.apply:
        print("\n  DRY RUN — nothing written. Re-run with --apply.")
        return

    os.makedirs(os.path.join(root, TASKS_DIRNAME, CLOSED_DIRNAME), exist_ok=True)
    for rel, content, _ in plan:
        atomic_write(os.path.join(root, rel), content)
    # Verify EVERY document round-tripped before the ledger — the ledger rewrite
    # is the destructive half, and it must not happen if a doc did not land.
    for rel, content, _ in plan:
        if read(os.path.join(root, rel)) != content:
            sys.exit(f"FATAL: {rel} did not round-trip — ledger NOT modified")
    atomic_write(ledger_p, new_ledger)
    if read(ledger_p) != new_ledger:
        sys.exit("FATAL: ledger did not round-trip — task documents are on disk, "
                 "ledger left as found")
    print(f"\n  APPLIED — {len(plan)} documents written, ledger rewritten.")
    print("  verify: every document round-tripped before the ledger was touched ✓")


if __name__ == "__main__":
    main()
# ═══ EOF split_ledger.py ═══
