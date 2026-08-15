# HISTORY — frozen journal archive

<!--
═══════════════════════════════════════════════════════════════════════════════
HISTORY.md — frozen archive. NOT a live loader file.

WRITE MODE: frozen. Nothing here has live force. This is where superseded status,
old session-history entries, and prior author notes go to rest when they age out
of the live files — so the live files stay small without losing the audit trail.

DO NOT load this file whole at session start. Pull a specific entry only if you
need the historical "why" behind something. The live operating rules are in
KERNEL; current state is in STATUS; deep canon is in REFERENCE.

This is one of five loader-stack files. See CANON_INDEX.yaml for the map.
═══════════════════════════════════════════════════════════════════════════════
-->

## How this file fills up

Two streams feed HISTORY, both append-only (newest first):

1. **Aged-out session journal.** Your task ledger (`MERGE_PLAN.md`) keeps only the
   most recent ~5 session entries inline. When a newer entry pushes an older one
   past that window, the oldest entry moves here verbatim. (The move happens at
   close — see the manifest's `close.journal.overflow_to`.)
2. **Frozen status.** When a `STATUS.yaml` fact is superseded and the *path it
   took* is worth keeping (a completed migration, a retired HOLD), the old state
   is recorded here rather than just overwritten away.

Keep entries verbatim and dated. Never edit history to make it tidier — its value
is being an accurate record of what was true when.

---

## [YYYY-MM-DD] — example frozen entry

> [Verbatim session-journal block or status snapshot that aged out of the live files.]
