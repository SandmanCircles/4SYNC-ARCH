# [PROJECT NAME] — Merge Plan / Persistent Task Ledger

**Last updated:** YYYY-MM-DD [session label] — see **## Session journal (recent)** below.

**Purpose:** Canonical persistent backing store for the session task tool.

The Claude Code / Cowork task tool is a **session-local view** — task state does NOT survive between sessions. This file is the **persistent backing store**: it captures full task state across all sessions and is the source of truth.

## File layout — short form here, long form in `tasks/`

This file is read at **every** boot. Nothing else in the ledger is. That single fact drives the whole layout:

- **`MERGE_PLAN.md`** (this file) — header, the **`## Session journal (recent)`** section (the most-recent ~5 session entries as blank-line blocks; the **`Last updated:`** line is just a one-line pointer, **not** the journal), status legend, and the full summary table (all rows).
- **`tasks/MP-0NN.md`** — one long-form document per **live** task. Loaded **on demand**, never at boot.
- **`tasks/closed/MP-0NN.md`** — the same document once its row reaches a terminal state. Never loaded; kept greppable.
- **`JOURNAL_HISTORY.md`** — session-journal blocks older than the top ~5, newest-first, same block format.

**The path is derived from the row ID, never written down:** row `27` → `tasks/MP-027.md`, zero-padded to three digits so the folder still sorts in ID order past 99. There is no pointer column, because a pointer can be typo'd and a convention cannot.

**Table owns state; document owns substance.** Status, blocked-by and owner live in the summary table and **only** there — a document that repeats them creates two copies of state, and neither announces when it goes stale. The *fact* "blocked by 21" belongs in the table; the *argument for why* belongs in the document.

> **Start here, don't graduate into it.** An earlier version of this template kept descriptions inline and offered the split as a growth path for "larger projects." That advice was wrong, and the project that ships this template is what proved it. Inline descriptions are invisible at 3 open tasks, noticeable at 12, and the majority of the file at 26 — and by the time they are noticeable you are already paying on every session, and the fix is a migration rather than a habit. Measured on that project: descriptions were **66% of the ledger** and the ledger was **67% of boot**. Creating one file per task from day one costs nothing. Retrofitting cost a day.
>
> The deeper reason it can't be a growth path: **age-based archiving cannot reach open work.** Closed descriptions can be aged out, but a task that stays open for months keeps its long form in the boot path for exactly as long as the work is alive — and a real project has many. Depth is orthogonal to lifecycle, so the tiering has to be by depth, which is what `tasks/` does.

**Keep a Subject under ~120 characters.** It is a label, not a description. Once long form lives in `tasks/`, **the table *is* the boot cost** and nothing else bounds it — cap the descriptions but not the rows and the growth simply relocates one level up, to be rediscovered in a month. `rotate.py` reports every row over the cap with its ID; it never blocks, because a session mid-write must always be able to record a row. **An over-long Subject is a signal the row wants a document, not a shorter sentence** — never compress meaning out of the ledger to satisfy the number. (Measured on a real 119-row ledger: 24,087 B of Subject text, ~6,000 tokens of pure boot cost, mean 199 chars, longest 2,426 — a bolded multi-clause paragraph carrying defect IDs. That is a description that migrated into a table cell.)

Cross-references between tasks resolve by ID. The summary table here is always canonical and never splits.

## Session protocol

- **Session start:** read this file — the table, not the task documents. Populate the session task tool from the table below using `TaskCreate` for each task, and restore blocked-by relationships with `TaskUpdate addBlockedBy`. **Open `tasks/MP-0NN.md` only for the task you are actually about to work.** Reading them all defeats the split; that is the entire point of it.
- **Session close:** mirror back task additions, status changes, and description updates. Substance goes to `tasks/MP-0NN.md`; state goes to the table row. Prepend a new block to the **`## Session journal (recent)`** section (newest at top) and refresh the **`Last updated:`** pointer's date + label. If the section now holds more than 5 blocks, move the oldest (bottom) block verbatim to the top of `JOURNAL_HISTORY.md`. `scripts/rotate.py` does the journal overflow, the closed-task document moves, and the size report — run it rather than doing this by hand.
- **Opening a task:** add the table row **and** write `tasks/MP-0NN.md` in the same edit. A row with no document is a task nobody can execute — the exact failure the authoring rule below exists to prevent. `rotate.py` exits non-zero if any non-terminal row is missing its document, so a close cannot quietly ship one.
- **Closing a task:** flip the row to ✅/❌ and move the file to `tasks/closed/`. No waiting period — the document is not in the boot path either way, so there is nothing to age out. `rotate.py` moves it for you.
- **Claiming a row (the `Owner` column):** when you move a row to 🔄, write your **agent/roster name + short session id** into `Owner` — e.g. `LoCo·b2df30b8`. Clear it back to `—` when the row leaves 🔄. The name says *which surface*; the session id separates two concurrent sessions **on that surface**, which no roster entry and no environment pin can do, because both resolve identically. If you run one session at a time this column costs you one character per row and can stay `—`; the moment you run two, it is the only thing standing between them and the same row.
- **Before claiming a row someone else owns, cross-check `.session_debt.tsv`.** Owner says *who*; the debt file says *whether they are still here*. A row for that session id with `last_activity` inside the manifest's `session_debt.live_within` means **taken and live — pick another row**. No recent row means the owner is gone and the claim is stale — **take it, and overwrite the `Owner` cell**. Without this cross-check a stale 🔄 owner is just a new kind of litter, which is why the column and the live-session boot reading are one change, not two.
- **Mid-session:** the table here is the canonical state. If the session tool gets reset, repopulate from this file.
- **Task-authoring rule (self-contained):** write every task document so it stands fully alone. A different surface — another agent, or an autonomous scheduled run — must be able to pick it up cold and execute it without access to the session that created it. No "continue what we did earlier" and no unstated context: name the files, the acceptance criteria, and the *why* inline. A task a stranger can't execute isn't ledgered yet. **This rule is why the split exists:** depth is not the problem, depth *in the boot path* is. Write the document as long as the work honestly needs — then keep it out of boot.
- **Cross-midnight sessions:** a session that spans midnight keeps ONE journal block, headed with the span (`2026-07-28/29`), and dates any later addendum inline. The block header carries the date the session *opened*, so without this rule a block reads as a day it did not happen on.
- **Adopting the split on an existing ledger:** if you are retrofitting a project whose descriptions are still inline, `scripts/split_ledger.py --dir <root>` migrates every `### #NNN` block into `tasks/`. Dry-run is the default; `--apply` writes. It runs **once** and is irreversible, so it is built to refuse rather than half-migrate — the worst outcome available to it is a ledger that *looks* migrated. It collects blocks from **anywhere in the file**, not just under a `## Task descriptions` heading (a heading-bound scan silently skips every block above it — on the ledger this was rewritten against, 23 of them), and it is FATAL on: a duplicate task id, a description with no table row, an **open** row with no description (`rotate.py` would reject that at the very next close), and a ledger that does not end in a newline — the cheap signature of a truncated write, which no irreversible restructure should run on top of. A *terminal* row with no description is fine and reported only; demanding one would make the migration impossible on any mature ledger.

## Status legend

| Symbol | Status | Meaning |
|---|---|---|
| ✅ | completed | shipped, in production or merged |
| 🔄 | in_progress | actively being worked, owner assigned or about to be |
| ⏳ | pending (open) | pickup-ready, no blockers |
| ⏸️ | blocked | waiting on upstream tasks (see Blocked by column) |
| ❌ | dropped | deliberately removed from scope; preserved as audit trail (see description for reasoning) |

---

## Session journal (recent)

<!-- KEEP-5 RULE: newest-first, blank-line-separated blocks, cap = 5.
     At session close (or via the `wrap` skill): PREPEND your new block here.
     If that makes 6 blocks, move the oldest (bottom) block verbatim to the top of
     JOURNAL_HISTORY.md. Keep the journal here as blocks — never re-chain it onto the
     one-line `**Last updated:**` pointer (a run-on chain balloons and the prune gets skipped). -->

PRIOR — YYYY-MM-DD [session label] — [what shipped / changed / was decided / was learned — concise].

PRIOR — YYYY-MM-DD [earlier session] — [...].

---

## Summary table

| ID | Status | Subject | Blocked by | Owner |
|---|---|---|---|---|
| 1 | ✅ | [Example completed task — e.g., "Initial schema migration"] | — | — |
| 2 | 🔄 | [Example in-progress task — e.g., "Wire payment provider"] | — | — |
| 3 | ⏳ | [Example pickup-ready task — e.g., "Add account settings page"] | — | — |
| 4 | ⏸️ | [Example blocked task — e.g., "Backfill historical records"] | #2 | — |
| 5 | ❌ | [Example dropped task — e.g., "Original Stripe webhook design"] | — | — |

**Tally:** [N tasks total. X completed, Y in_progress, Z pending, A blocked, B dropped.] [Short narrative on current state — what just shipped, what's next.]

**Pickup-ready right now (no blockers):** [List the ⏳ rows as bare `#3`, `#7` — NOT `MP-003`, which is the right ID form everywhere else in ARCH but is not what this line's check counts — plus a one-line note on each. Write a *historical* mention as "row 3" so commentary stays out of the list.]

---

## Task documents

**There is no descriptions section in this file, deliberately.** Each task's long form lives at `tasks/MP-0NN.md` — see **File layout** above. Row `2` → `tasks/MP-002.md`; once the row goes ✅ or ❌ the file moves to `tasks/closed/`.

`tasks/MP-001.md` ships as a worked example. Delete it with the example rows above.

---

## How to use this file

**On day one:** delete the example rows above. Add your real first three or four tasks. Don't over-design — the structure earns its keep over months, not minutes.

**Per session:**
1. Open a fresh Claude session.
2. The CLAUDE.md at your project root should already tell Claude to read this file at session start (see `templates/CLAUDE.md` in the source repo).
3. Claude will populate the session task tool from the summary table.
4. Work normally — update the session task tool as you go (`TaskUpdate` for status changes, `TaskCreate` for new work).
5. **Before the session ends:** ask Claude to mirror the task tool state back into this file. Prepend a session-journal block to `## Session journal (recent)` and refresh the `Last updated:` pointer. Commit.

**Bigger projects:** the summary table can grow to 100+ rows without ceasing to be useful — a row costs roughly 50 tokens, so task count can triple and stay cheap. `tasks/` provides the depth. The summary stays scannable.

**Keeping it lean:** run `scripts/rotate.py` at close. It moves journal overflow past `keep` into `JOURNAL_HISTORY.md`, moves closed tasks' documents into `tasks/closed/`, reports what this file costs at boot, and warns when the journal exceeds `close.journal.max_bytes` in your manifest. Dry-run by default; pass `--apply` to write.

**Watch the journal.** `keep` is a **count** cap and a single session block can run several KB, so the count alone cannot bound the file. Once descriptions move to `tasks/`, the journal becomes the largest thing left in the boot path — which is exactly how descriptions got there. That is what the size report is for; act on it rather than reading past it.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Adapt freely.*
