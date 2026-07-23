# [PROJECT NAME] — Session Context

> **Bookends — manifest-driven.** This instance's boot and close are declared in
> **`4SYNC.yaml`** (the instance manifest). At session start: read `4SYNC.yaml`, then execute
> its `boot:` list in order (the prose below describes the same load). When the user signals —
> in any plain-English way — that the session is ending, execute its `close:` steps in order,
> honoring `freshness_check` before any ledger write. Close fires ONLY on an explicit ending
> signal, never on silence, idle, or a pause — paused sessions resume, they don't wrap.

At the start of every session, load (in this order):

1. **`MERGE_PLAN.md`** — persistent task ledger. **Operational state.** The session task tool is session-local and does NOT survive between sessions; this file is the source of truth for task state. Populate the task tool from this file at session start; mirror changes back at session close. See the "Session protocol" section at the top of the file. *(For larger projects, long-form descriptions of older closed tasks may live in `MERGE_PLAN_ARCHIVE.md` and older session history in `MERGE_PLAN_HISTORY.md`. The summary table in `MERGE_PLAN.md` is always canonical.)*

2. **The `config/` loader stack** — **Identity state.** Read these three in order; they are small by design:
   - a. **`config/KERNEL.yaml`** — identity + front-loaded `agent_directives` + invariants + naming quickref (the always-on operating contract).
   - b. **`config/STATUS.yaml`** — current live state (overwrite snapshot: deploy versions, active focus, blockers).
   - c. **`config/CANON_INDEX.yaml`** — the map of where every deeper detail lives.
   - Pull **`config/REFERENCE.yaml`** (deep canon) **on demand** — never at session start. Never load **`config/HISTORY.md`** (frozen archive) whole.

3. **`NAMING_CONVENTIONS.md`** — brand marks, internal taxonomies, retired terms, the reasoning behind each. **Vocabulary state.** Load on demand before generating reports, documents, web assets, or external communications.

4. **`ABBA.md`** *(only if you run more than one agent/surface)* — the agent bulletin board. **Session-start inbox check.** If you are a named agent, check for any OPEN message addressed `To:` your name (or alias) and action/acknowledge it before your planned work; mark it `Status: DONE` with a one-line resolution when handled. This is a cross-agent nudge channel — NOT a replacement for the merge plan ledger.

5. **Any other persistent context files your project depends on** — architecture notes, decision logs (ADRs), open design issues. List them here in load order.

Quick reference — fill in your project's critical facts so they're available even without loading the files above:

- **Project name:** [...]
- **Current phase / status:** [...]
- **Key naming rules:** [...]
- **Retired names / superseded patterns:** [...]

---

## Close — commit choreography

`4SYNC.yaml`'s `close:` block declares the mechanical steps (`freshness_check`, `journal`, `ledger_sync`, `snapshot`, `bulletin`, `rotate`). Committing is environment-aware and isn't mechanical enough to declare in the manifest, so it's spelled out here:

- **Reconcile before write-back, always.** Another session — on the host, or a parallel run — may have written the shared ledgers since you loaded them. Before touching `MERGE_PLAN.md`, `config/STATUS.yaml`, `ABBA.md`, or `LANDING_QUEUE.md`: re-read each target **fresh** immediately before editing it, and apply your changes as **small anchored edits** onto that fresh content (prepend your one journal block, flip only your own rows, overwrite only the facts you changed, mark only your own messages) — **never a whole-file rewrite from your session-start snapshot**, which silently reverts another session's intervening edits. After each write, re-read and confirm the other session's content survived. (This is the prose behind the manifest's `freshness_check`.)
- **Git-capable, host-side:** stage the session's intended files **explicitly by path** (never `git add -A` — leave unrelated working-tree changes alone) and commit with a clear message. If you touched a sibling repo, **commit there too, in the same close** — don't leave a sibling repo with only a "note to commit later." Push only when the user asks.
- **Can't git (sandbox / mounted filesystem):** do NOT attempt git — a mount can serve stale, clipped views and commit a genuinely truncated file. Do the file write-backs per the reconcile discipline above, then queue a row in `LANDING_QUEUE.md` (per the manifest's `commit.untrusted_or_no_git` rule) listing every changed file + repo, so a host-side session lands them.

---

## The three pillars

Distinct authority, distinct write discipline:

- **`MERGE_PLAN.md`** — **operational** source of truth (task state, deploy state, blocked-by relationships). The session journal lives here — as blank-line blocks in the `## Session journal (recent)` section (newest-first, keep ~5) — never in the config files.
- **The `config/` loader stack** — **identity** source of truth. Each file has its own write mode: `KERNEL` = edit-in-place, rare (identity/doctrine); `STATUS` = **overwrite** on change (state, not a log); `CANON_INDEX` = append/edit a pointer row; `REFERENCE` = edit-in-place deep canon; `HISTORY` = frozen.
- **`NAMING_CONVENTIONS.md`** — **vocabulary** source of truth (what to call things; what not to call things).

If anything in this CLAUDE.md conflicts with any of the above, the other files win — update this CLAUDE.md to match.

> **Why a stack and not one config file?** Splitting identity state by *kind* and *write
> discipline* keeps the always-on rules (KERNEL) tiny and unmissable, lets live state (STATUS) be
> overwritten cleanly, and pushes the bulk (REFERENCE) out of every session's load path.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH).*
