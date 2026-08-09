# [PROJECT NAME] — Session Context

> **Bookends — manifest-driven.** This instance's boot and close are declared in
> **`4SYNC.yaml`** (the instance manifest). At session start: read `4SYNC.yaml`, then execute
> its `boot:` list in order (the prose below describes the same load). When the user signals —
> in any plain-English way — that the session is ending, execute its `close:` steps in order,
> honoring `freshness_check` before any ledger write. Close fires ONLY on an explicit ending
> signal, never on silence, idle, or a pause — paused sessions resume, they don't wrap.

At the start of every session, load (in this order):

**Identity before state before ledger.** The ledger loads LAST of the four, changed 2026-08-06: KERNEL declares itself FIRST ACTION, so loading the task ledger ahead of it meant every session met the journal before the operating contract — and the ledger is the biggest, most volatile file in the stack. If you are tempted to put `MERGE_PLAN.md` back at the top because it reads like the natural starting point, that is exactly the move this note exists to stop.

1. **The `config/` loader stack** — **Identity state.** Read these three in order; they are small by design:
   - a. **`config/KERNEL.yaml`** — identity + front-loaded `agent_directives` + invariants + naming quickref (the always-on operating contract).
   - b. **`config/STATUS.yaml`** — current live state (overwrite snapshot: deploy versions, active focus, blockers).
   - c. **`config/CANON_INDEX.yaml`** — the map of where every deeper detail lives.
   - Pull **`config/REFERENCE.yaml`** (deep canon) **on demand** — never at session start. Never load **`config/HISTORY.md`** (frozen archive) whole.
   - **These filenames carry a project prefix after genesis.** Genesis renames the stack to `config/<PROJECT>_KERNEL.yaml`, `config/<PROJECT>_STATUS.yaml` and so on, and the manifest to `<PROJECT>.yaml`, so two instances on one machine never share a stack filename and a wrong-instance read is visible rather than invisible. The names above are the pre-genesis ones — **the manifest's `boot:` list is always current, so read that, not this.**

2. **`MERGE_PLAN.md`** — persistent task ledger. **Operational state.** The session task tool is session-local and does NOT survive between sessions; this file is the source of truth for task state. Populate the task tool from this file at session start; mirror changes back at session close. See the "Session protocol" section at the top of the file. *(This file holds the summary table + journal ONLY — it is always canonical for task state. Each task's long form lives at `tasks/MP-0NN.md`, path derived from the row ID, loaded **on demand** and never at boot; closed tasks' docs move to `tasks/closed/`. Older session history is in `JOURNAL_HISTORY.md`.)* **Open only the task doc you are about to work** — reading them all defeats the split.

3. **`NAMING_CONVENTIONS.md`** — brand marks, internal taxonomies, retired terms, the reasoning behind each. **Vocabulary state.** Load on demand before generating reports, documents, web assets, or external communications.

4. **`ABBA.md`** *(only if `4SYNC.yaml` declares `close.bulletin.check_at_boot: true` — otherwise the board is inert; skip it)* — the agent bulletin board. **Session-start inbox check.** Resolve which agent you are from its **Roster** (`declared` → `ARCH_AGENT` → shell → ask), then check for any OPEN message addressed `To:` your name or alias — matched case-insensitively — and action/acknowledge it before your planned work; mark it `Status: DONE` with a one-line resolution when handled.
   - **SCAN THE HEADERS — do not read the file.** Grep the `### [n] … To: … Status:` lines and open only the bodies addressed to you. The board is *addressed*; reading it whole makes you pay for every other agent's un-drained inbox (measured on a real board: 90% of the read was waste). On a header-count mismatch, fall back to a full read **and say so** — a header that drifted out of format is an invisible miss, which is worse than an expensive read.
   - This is a cross-agent nudge channel — NOT a replacement for the merge plan ledger.

5. **`.session_debt.tsv`** — **do you have company?** Read it at boot and give the rows **two readings**, not one. A row whose `last_activity` is within `4SYNC.yaml`'s `session_debt.live_within` is a session **working right now**: *"⚠ N other session(s) LIVE in this instance; shared ledgers are contested."* An older row is what the tracker was originally for: *"N session(s) holding undeposited state."* Same file, same rows, opposite meanings — one says *be careful now*, the other says *someone forgot*. Pair it with the ledger's `Owner` column: Owner says **who** holds a row, this file says **whether they are still here**.
   - **Evidence, not protection — and it warns rather than blocks.** No locking, and it prevents nothing. A session simply knows it has company *before* it starts editing shared ledgers. Concurrency is normal and works; what fails is doing it unknowingly. Its other value is after the fact: a row proving two sessions were live in the same minute is the only artifact that can establish it, and with no hooks there is no row and no account of what happened.
   - **`last_activity` is not activity.** The hook's `WRITE_TOOLS` excludes `Bash`, so a row stops moving at the last *file write* — every git command after that is invisible, and a session that has been reading or thinking looks idle. Treat "not live" as *probably* idle, never as *gone*.

6. **Any other persistent context files your project depends on** — architecture notes, decision logs (ADRs), open design issues. List them here in load order.

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
- **`rotate.py --apply` goes BETWEEN two commits.** It refuses a dirty tree by design — *"git is the undo"* — and it then moves closed-task documents and rewrites the `Tally`, which are themselves changes to commit. So the order is **commit your edits → run rotate → commit its moves**, or pass `--allow-dirty`. Nothing is broken when it refuses; the order simply was not written down.
- **Can't git (sandbox / mounted filesystem):** do NOT attempt git — a mount can serve stale, clipped views and commit a genuinely truncated file. Do the file write-backs per the reconcile discipline above, then queue a row in `LANDING_QUEUE.md` (per the manifest's `commit.untrusted_or_no_git` rule) listing every changed file + repo, so a host-side session lands them.

---

## The three pillars

Distinct authority, distinct write discipline:

- **`MERGE_PLAN.md`** — **operational** source of truth (task state, deploy state, blocked-by relationships). The session journal lives here — as blank-line blocks in the `## Session journal (recent)` section (newest-first, keep ~5) — never in the config files.
- **The `config/` loader stack** — **identity** source of truth. Each file has its own write mode: `KERNEL` = edit-in-place, rare (identity/doctrine); `STATUS` = **overwrite the fact, never the file** (state, not a log — replace the one value that changed, in place; a whole-file rewrite from your session-start copy is the single confirmed way to lose data here); `CANON_INDEX` = append/edit a pointer row; `REFERENCE` = edit-in-place deep canon; `HISTORY` = frozen.
- **`NAMING_CONVENTIONS.md`** — **vocabulary** source of truth (what to call things; what not to call things).

If anything in this CLAUDE.md conflicts with any of the above, the other files win — update this CLAUDE.md to match.

> **Why a stack and not one config file?** Splitting identity state by *kind* and *write
> discipline* keeps the always-on rules (KERNEL) tiny and unmissable, lets live state (STATUS) be
> overwritten cleanly, and pushes the bulk (REFERENCE) out of every session's load path.

---

*Part of [4SYNC ARCH](https://github.com/SandmanCircles/4SYNC-ARCH).*
