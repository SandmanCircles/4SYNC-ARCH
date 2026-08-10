# Ledger guide — how `MERGE_PLAN.md` works, and why

**On demand. Never booted.** Every rule this explains is stated operatively in
`MERGE_PLAN.md`'s **Operating rules**; this file holds the reasoning, the measurements, and
the failure each rule exists to prevent. Rule names match, so you can look one up by name.

**Why this file exists at all.** The ledger template used to carry all of this inline. It was
**70% of the template**, in the one file read at every boot, so every session paid ~2,240
tokens for text that is useful exactly once. A protocol whose entire claim is disciplined
context spending cannot ship its own explanation in the boot path. Splitting read-once
teaching from every-boot operative text is the same discipline ARCH already applies to task
documents, deep canon, and findings.

---

## File layout

- **`MERGE_PLAN.md`** — read at **every** boot. Journal + table + the operating rules. Nothing
  else in the ledger is booted, and that single fact drives the whole layout.
- **`tasks/MP-0NN.md`** — one long-form document per **live** task. On demand, never at boot.
- **`tasks/closed/MP-0NN.md`** — the same document once its row is terminal. Never loaded, kept
  greppable.
- **`JOURNAL_HISTORY.md`** — journal blocks older than the top ~5, newest-first, same format.
- **`LEDGER_GUIDE.md`** — this file.

**The path is derived from the row ID, never written down:** row `27` → `tasks/MP-027.md`,
zero-padded to three digits so the folder still sorts in ID order past 99. There is no pointer
column, because a pointer can be typo'd and a convention cannot.

## Start here, don't graduate into it

An earlier version of this template kept task descriptions inline and offered the split as a
growth path for "larger projects." **That advice was wrong, and the project that ships this
template is what proved it.** Inline descriptions are invisible at 3 open tasks, noticeable at
12, and the majority of the file at 26 — and by the time they are noticeable you are already
paying on every session, and the fix is a migration rather than a habit. Measured on that
project: descriptions were **66% of the ledger** and the ledger was **67% of boot**. Creating
one file per task from day one costs nothing. Retrofitting cost a day.

**The deeper reason it cannot be a growth path: age-based archiving cannot reach open work.**
Closed descriptions can be aged out, but a task that stays open for months keeps its long form
in the boot path for exactly as long as the work is alive — and a real project has many. Depth
is orthogonal to lifecycle, so the tiering has to be by depth, which is what `tasks/` does.

## Table owns state, document owns substance

Status, blocked-by and owner live in the summary table and **only** there. A document that
repeats them creates two copies of state, and neither announces when it goes stale. The *fact*
"blocked by 21" belongs in the table; the *argument for why* belongs in the document.

## Subject cap ~120 characters

It is a label, not a description. Once long form lives in `tasks/`, **the table *is* the boot
cost** and nothing else bounds it — cap the descriptions but not the rows and the growth simply
relocates one level up, to be rediscovered in a month. `rotate.py` reports every row over the
cap with its ID; it never blocks, because a session mid-write must always be able to record a
row.

**An over-long Subject is a signal the row wants a document, not a shorter sentence** — never
compress meaning out of the ledger to satisfy the number. (Measured on a real 119-row ledger:
24,087 B of Subject text, ~6,000 tokens of pure boot cost, mean 199 chars, longest 2,426 — a
bolded multi-clause paragraph carrying defect IDs. That is a description that migrated into a
table cell.)

## Boot

Read the table, not the task documents. Populate the session task tool with `TaskCreate` per
row and restore blocked-by with `TaskUpdate addBlockedBy`. **Open `tasks/MP-0NN.md` only for
the task you are actually about to work.** Reading them all defeats the split; that is the
entire point of it.

## Close

Mirror back task additions, status changes and description updates — substance to
`tasks/MP-0NN.md`, state to the table row. Prepend a new block to `## Session journal (recent)`
(newest at top) and refresh the `Last updated:` pointer. If the section holds more than 5
blocks, move the oldest to the top of `JOURNAL_HISTORY.md`. **Run `scripts/rotate.py` rather
than doing any of the mechanical parts by hand** — it does the journal overflow, the closed-task
document moves, and the size report.

## Opening and closing a task

A row with no document is a task nobody can execute — the exact failure the authoring rule
exists to prevent. `rotate.py` exits non-zero if any non-terminal row is missing its document,
so a close cannot quietly ship one.

Closing needs no waiting period: the document is not in the boot path either way, so there is
nothing to age out.

## Claiming a row — the `Owner` column

The name says *which surface*; the session id separates two concurrent sessions **on that
surface**, which no roster entry and no environment pin can do, because both resolve
identically. If you run one session at a time this column costs you one character per row and
can stay `—`; the moment you run two, it is the only thing standing between them and the same
row.

**Cross-check `.session_debt.tsv` before taking a row someone owns.** Owner says *who*; the
debt file says *whether they are still here*. A row for that session id with `last_activity`
inside the manifest's `session_debt.live_within` means **taken and live — pick another row**.
No recent row means the owner is gone and the claim is stale — take it, and overwrite the
`Owner` cell. Without this cross-check a stale 🔄 owner is just a new kind of litter, which is
why the column and the live-session boot reading are one change, not two.

## Task-authoring rule — self-contained

Write every task document so it stands fully alone. A different surface — another agent, or an
autonomous scheduled run — must be able to pick it up cold and execute it without access to the
session that created it. No "continue what we did earlier" and no unstated context: name the
files, the acceptance criteria, and the *why* inline. A task a stranger can't execute isn't
ledgered yet.

**This rule is why the split exists:** depth is not the problem, depth *in the boot path* is.
Write the document as long as the work honestly needs — then keep it out of boot.

## Cross-midnight sessions

One journal block, headed with the span (`2026-07-28/29`), with any later addendum dated
inline. The block header carries the date the session *opened*, so without this rule a block
reads as a day it did not happen on.

## Retrofitting an existing ledger

`scripts/split_ledger.py --dir <root>` migrates every `### #NNN` block into `tasks/`. Dry-run
is the default; `--apply` writes. It runs **once** and is irreversible, so it is built to refuse
rather than half-migrate — the worst outcome available to it is a ledger that *looks* migrated.

It collects blocks from **anywhere in the file**, not just under a `## Task descriptions`
heading (a heading-bound scan silently skips every block above it — on the ledger this was
rewritten against, 23 of them), and it is FATAL on: a duplicate task id, a description with no
table row, an **open** row with no description (`rotate.py` would reject that at the very next
close), and a ledger that does not end in a newline — the cheap signature of a truncated write,
which no irreversible restructure should run on top of. A *terminal* row with no description is
fine and reported only; demanding one would make the migration impossible on any mature ledger.

## Day one, and the shape of a working week

**Day one:** delete the example rows. Add your real first three or four tasks. Don't
over-design — the structure earns its keep over months, not minutes.

**Per session:** open a session; `CLAUDE.md` already tells it to read the ledger at boot; it
populates the task tool from the table; you work normally, updating the task tool as you go;
before the session ends you ask for the state to be mirrored back, a journal block prepended and
the `Last updated:` pointer refreshed. Commit.

**Bigger projects:** the summary table can grow past 100 rows without ceasing to be useful — a
row costs roughly 50 tokens, so task count can triple and stay cheap. `tasks/` provides the
depth; the summary stays scannable.

## Watch the journal

`keep` is a **count** cap and a single session block can run several KB, so the count alone
cannot bound the file. Once descriptions move to `tasks/` **and the teaching prose moves here,
the journal becomes the largest thing left in the boot path** — which is exactly how
descriptions got there in the first place. That is what `rotate.py`'s size report is for; act on
it rather than reading past it.

**And read the report as a threshold, not as scenery.** `rotate.py` prints the prose-vs-rows
split at every close. It printed the number that produced this whole restructure for weeks
before anyone read it as an accusation. **A measurement you see every day stops being read.**

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH).*
