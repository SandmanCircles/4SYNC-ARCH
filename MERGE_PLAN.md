# [PROJECT NAME] — Merge Plan / Persistent Task Ledger

**Last updated:** YYYY-MM-DD [session label] — see **## Session journal (recent)** below.

**Purpose:** Canonical persistent backing store for the session task tool.

The Claude Code / Cowork task tool is a **session-local view** — task state does NOT survive between sessions. This file is the **persistent backing store**: it captures full task state across all sessions and is the source of truth.

## File layout (optional 3-file ledger for larger projects)

For a small project, this single `MERGE_PLAN.md` is the whole pattern. As the project grows past a few months of use, the file accumulates two distinct kinds of weight: long-form descriptions of tasks closed weeks ago, and a growing stack of session-journal blocks in the `## Session journal (recent)` section. Both still matter, but neither needs to load on every session.

The 3-file split is the recommended growth path:

- **`MERGE_PLAN.md`** (this file) — header, the **`## Session journal (recent)`** section (the most-recent ~5 session entries as blank-line blocks; the **`Last updated:`** line is just a one-line pointer, **not** the journal), status legend, full summary table (all rows, including archived ones), and task descriptions for **open + recently-closed** tasks.
- **`MERGE_PLAN_ARCHIVE.md`** — task descriptions for tasks closed more than N days ago (recommended lag: **10 days**, tune to taste).
- **`MERGE_PLAN_HISTORY.md`** — session-journal blocks older than the top ~5 (overflow from the `## Session journal (recent)` section), newest-first, same block format.

Cross-references between tasks resolve by ID and work across both files. The summary table here is always canonical and never splits.

Skip the split until you actually need it. See `How to use this file` below.

## Session protocol

- **Session start:** read this file. Populate the session task tool from the table below using `TaskCreate` for each task. Restore blocked-by relationships with `TaskUpdate addBlockedBy`. If a task's long-form description has been archived (see file layout above), open `MERGE_PLAN_ARCHIVE.md` only if you need that depth. Then proceed with work.
- **Session close:** mirror back any task additions, status changes, or description updates from the session tool into this file. Prepend a new block to the **`## Session journal (recent)`** section (newest at top) and refresh the **`Last updated:`** pointer's date + label. If a task crosses the archive lag this session, move its long-form description into `MERGE_PLAN_ARCHIVE.md` (leave the summary-table row here). If the section now holds more than 5 blocks, move the oldest (bottom) block verbatim to the top of `MERGE_PLAN_HISTORY.md`.
- **Mid-session:** the table here is the canonical state. If the session tool gets reset, repopulate from this file.
- **Task-authoring rule (self-contained):** write every task description so it stands fully alone. A different surface — another agent, or an autonomous scheduled run — must be able to pick it up cold and execute it without access to the session that created it. No "continue what we did earlier" and no unstated context: name the files, the acceptance criteria, and the *why* inline. A task a stranger can't execute isn't ledgered yet.

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
     MERGE_PLAN_HISTORY.md. Keep the journal here as blocks — never re-chain it onto the
     one-line `**Last updated:**` pointer (a run-on chain balloons and the prune gets skipped). -->

PRIOR — YYYY-MM-DD [session label] — [what shipped / changed / was decided / was learned — concise].

PRIOR — YYYY-MM-DD [earlier session] — [...].

---

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | [Example completed task — e.g., "Initial schema migration"] | — |
| 2 | 🔄 | [Example in-progress task — e.g., "Wire payment provider"] | — |
| 3 | ⏳ | [Example pickup-ready task — e.g., "Add account settings page"] | — |
| 4 | ⏸️ | [Example blocked task — e.g., "Backfill historical records"] | #2 |
| 5 | ❌ | [Example dropped task — e.g., "Original Stripe webhook design"] | — |

**Tally:** [N tasks total. X completed, Y in_progress, Z pending, A blocked, B dropped.] [Short narrative on current state — what just shipped, what's next.]

**Pickup-ready right now (no blockers):** [List the ⏳ task IDs and a one-line note on each.]

---

## Task descriptions

### #1 — [Subject] ✅
[2–6 lines describing what was done, when it shipped, links to relevant commits/PRs/files. Keep it terse but include enough that a cold-start session can understand what this is and why it was done.]

### #2 — [Subject] 🔄
[Current state of the in-progress work. What's done, what's left, what decisions are pending. Owner if applicable. Links to working-tree files or branch.]

### #3 — [Subject] ⏳
[Description of what needs to happen. Acceptance criteria. Estimated effort. Any context the picker-up needs.]

### #4 — [Subject] ⏸️
[Description + what unblocks it. The "Blocked by" column gives the dependency ID; this section explains *why* it's blocked and what the dependent task needs to produce before this can proceed.]

### #5 — [Subject] ❌
[Why this was dropped. Preserved as audit trail so a future session doesn't re-litigate the decision. Include the date of the drop and any artifacts that survived (e.g., research that informed adjacent work).]

---

## How to use this file

**On day one:** delete the example rows above. Add your real first three or four tasks. Don't over-design — the structure earns its keep over months, not minutes.

**Per session:**
1. Open a fresh Claude session.
2. The CLAUDE.md at your project root should already tell Claude to read this file at session start (see `templates/CLAUDE.md` in the source repo).
3. Claude will populate the session task tool from the summary table.
4. Work normally — update the session task tool as you go (`TaskUpdate` for status changes, `TaskCreate` for new work).
5. **Before the session ends:** ask Claude to mirror the task tool state back into this file. Prepend a session-journal block to `## Session journal (recent)` and refresh the `Last updated:` pointer. Commit.

**Bigger projects:** the summary table can grow to 50+ rows without ceasing to be useful. The task descriptions section provides the depth. The summary stays scannable.

**If the file gets unwieldy:** adopt the 3-file ledger documented in the **File layout** section above. Copy `templates/MERGE_PLAN_ARCHIVE.md` and `templates/MERGE_PLAN_HISTORY.md` into your repo, then move closed-task descriptions older than your archive lag (default 10 days) into the archive file and any session-journal blocks beyond the top ~5 into the history file. The summary table here stays whole; only long-form descriptions and older session narrative leave.

---

*Part of [4SYNC-CMS](https://github.com/SandmanCircles/4SYNC-CMS). MIT license. Adapt freely.*
