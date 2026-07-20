# [PROJECT NAME] — Merge Plan Archive

Completed and dropped task descriptions, retired here per the archive lag specified at the top of `MERGE_PLAN.md`. The default lag is **10 days** — task descriptions for tasks closed more than 10 days ago live here, while open and recently-closed task descriptions stay in `MERGE_PLAN.md`.

**The canonical summary table lives in `MERGE_PLAN.md`** — all task rows including archived ones remain there. This file holds the long-form task descriptions only.

Cross-references between tasks (e.g., "MP#41 cites MP#42 findings") still resolve — task IDs are stable across both files.

---

## When to move a description here

Move a task description from `MERGE_PLAN.md` to this file when **both** are true:

1. The task is in a terminal state (`✅ completed` or `❌ dropped`).
2. It has been in that terminal state for longer than the archive lag (default: 10 days).

Tune the lag for your project. Fast-moving projects might use 7 days; slower projects might use 30. The point is to keep the main file's task-description section focused on what's currently load-bearing, while preserving the full audit trail somewhere reachable.

Always leave the summary-table row in `MERGE_PLAN.md`. Only the long-form description moves.

---

## Task descriptions (archived)

### #[ID] — [Subject] ✅
[Long-form description of the completed task. What was done, when it shipped, links to commits/PRs/files, any deferred sub-items, any audit-trail notes that future sessions will want. Keep the original wording — this is a historical record, not a summary.]

### #[ID] — [Subject] ❌
[Why this was dropped. Original scope. Audit trail. Whatever a future session will need to understand the decision and avoid re-litigating it.]

---

*Part of [4SYNC-CMS](https://github.com/SandmanCircles/4SYNC-CMS). MIT license.*
