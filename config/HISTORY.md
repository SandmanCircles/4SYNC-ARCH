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

ONE stream feeds HISTORY, append-only (newest first):

1. **Frozen status.** When a `STATUS.yaml` fact is superseded and the *path it
   took* is worth keeping (a completed migration, a retired HOLD), the old state
   is recorded here rather than just overwritten away.

**The aged-out session journal does NOT come here**, and this file used to claim it
did. It goes wherever the manifest's `close.journal.overflow_to` declares, which
ships as `JOURNAL_HISTORY.md`. The old text claimed that stream *and cited that very
key* while the key pointed somewhere else — so a reader who followed the citation
found two archives and no way to tell which one was live. That is the decorative-
declaration class this project's own adoption-defects table names, sitting in the
project's own files. **The manifest is the declaration; this file is not.** Corrected
2026-08-20 under SYN-096.

Keep entries verbatim and dated. Never edit history to make it tidier — its value
is being an accurate record of what was true when.

---

## [YYYY-MM-DD] — example frozen entry

> [Verbatim session-journal block or status snapshot that aged out of the live files.]
