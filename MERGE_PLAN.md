# [PROJECT NAME] — Merge Plan / Persistent Task Ledger

**Last updated:** YYYY-MM-DD [session label] — see **## Session journal (recent)** below.

Persistent backing store for the session task tool, which is session-local and does not
survive between sessions. This file does. **It is read at every boot; nothing else in the
ledger is.**

## Operating rules

Each rule is operative as written. The reasoning, the measurements behind the caps, and the
failures each rule exists to prevent are in **`LEDGER_GUIDE.md`** — on demand, never booted,
same rule names.

- **Boot** — read this file: journal, then table. Populate the task tool from the table.
  Open `tasks/MP-0NN.md` **only** for the task you are about to work.
- **Close** — mirror task state back to the table, prepend one block to the journal, refresh
  the `Last updated:` pointer, then run `scripts/rotate.py`.
- **Open a task** — add the table row **and** write `tasks/MP-0NN.md` in the same edit. Path
  is derived from the row ID, never written down: row `27` → `tasks/MP-027.md`.
- **Close a task** — flip the row to ✅/❌; `rotate.py` moves the document to `tasks/closed/`.
- **Table owns state, document owns substance.** Status, blocked-by and owner live in the
  table and only there. Never restate them in a document.
- **Subject cap ~120 chars.** It is a label. An over-long Subject means the row wants a
  document, not a shorter sentence.
- **Claim a row** — moving to 🔄, put your roster name + short session id in `Owner`
  (`LoCo·b2df30b8`); clear to `—` when it leaves 🔄. Before taking a row someone owns, check
  `.session_debt.tsv`: recent activity means taken and live, stale means take it.
- **Write every task document self-contained** — another agent or an unattended run must be
  able to execute it cold. Name the files, the acceptance criteria and the *why* inline.
- **Cross-midnight session** — one journal block, headed with the span (`2026-07-28/29`),
  later addenda dated inline.
- **Retrofitting an existing ledger** — `scripts/split_ledger.py --dir <root>` migrates inline
  descriptions into `tasks/`. Runs once, irreversible, dry-run by default. Read the guide first.

**Status:** ✅ completed · 🔄 in progress · ⏳ pending, pickup-ready · ⏸️ blocked (see Blocked by) · ❌ dropped, kept as audit trail

---

## Session journal (recent)

<!-- KEEP-5 RULE: newest-first, one block per DATED HEADER, cap = 5.
     At session close: PREPEND your new block here. If that makes 6 blocks, move the
     oldest (bottom) block verbatim to the top of JOURNAL_HISTORY.md. Keep the journal
     here as blocks — never re-chain it onto the one-line `**Last updated:**` pointer. -->

PRIOR — YYYY-MM-DD [session label] — [what shipped / changed / was decided / was learned].

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

**Tally:** [N] tasks total — [X] completed, [Y] in_progress, [Z] pending, [A] blocked, [B] dropped.

<!-- rotate.py DERIVES the Tally from the rows (--apply rewrites it) and checks it on
     every run — keep the exact shape above, em dash included. A hand-styled variant
     reads as drift, and the first complaint an adopter ever sees from their own
     tooling must not be about a line the template itself wrote wrong. -->

**Pickup-ready right now (no blockers):** [List the ⏳ rows as bare `#3`, `#7` — NOT `MP-003`, which is the right ID form everywhere else in ARCH but is not what this line's check counts — plus a one-line note on each. Write a *historical* mention as "row 3" so commentary stays out of the list.]

Delete the example rows on day one. `tasks/MP-001.md` ships as a worked example; delete it too.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH). Adapt freely.*
*How this file works, and why each rule above exists — `LEDGER_GUIDE.md`.*
