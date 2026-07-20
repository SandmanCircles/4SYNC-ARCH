# 4SYNC-CMS

**The Context Management System.** Persistent, multi-session, multi-agent memory for
Claude Code projects — as a drop-in filesystem, not an installation.

Content management systems solved "many hands, one website." 4SYNC solves **"many
sessions, one project."** AI coding sessions are goldfish: every context window starts
cold and dies silent, and everything a session learned — decisions, task state, naming
discipline, deploy facts — evaporates unless it's deposited somewhere the next session
will actually look. 4SYNC is that somewhere, plus the discipline that keeps it trustworthy.

## Quickstart — three steps, no installation

1. **Drop this filesystem into your project folder** (or start from it).
2. **Tell it what your project is** — either fill in `SEED.md` and flip its flag to
   `AUTHORED`, or don't: just open a Claude session in the folder and it will
   interview you through the seed conversationally.
3. **Open a session.** It reads `CLAUDE.md` → `4SYNC.yaml`, detects the untouched
   stack, runs **genesis** — distills your seed into the config stack, archives the
   seed verbatim as the project's birth record (locked read-only) — and orients.
   Note: with an `AUTHORED` seed, genesis runs silently and automatically — opening
   the session *is* the consent; there is no confirmation step. Every session after
   that boots oriented and deposits state back on close.

No skills to install, no plugins, no per-machine setup. The entire mechanism is two
files that travel with the folder: `CLAUDE.md` (teaches any session the protocol
exists) and `4SYNC.yaml` (declares what this instance's protocol is). Users can layer
their own trigger-phrase skills on top; the system needs none.

## The shape of the system

Three pillars, with distinct authority and write discipline:

| Pillar | Files | Discipline |
|---|---|---|
| **Operational state** | `MERGE_PLAN.md` (+ `_ARCHIVE`, `_HISTORY`) | Task ledger + session journal. Append blocks, keep-N, never renumber. |
| **Identity state** | `config/` — `KERNEL` · `STATUS` · `CANON_INDEX` · `REFERENCE` · `HISTORY` | KERNEL edit-rarely · STATUS overwrite-only · INDEX pointer rows · REFERENCE on-demand · HISTORY frozen. |
| **Vocabulary state** | `NAMING_CONVENTIONS.md` | Canonical marks, retired names; loaded before external output. |

Multi-agent extras (adopt when you need them): `ABBA.md` — a bulletin board of
messages addressed by agent name; `LANDING_QUEUE.md` — commit handoffs from
surfaces that can't safely run git.

## The manifest — `4SYNC.yaml`

Every instance declares its own shape: what loads at boot (in order), what happens
at close (journal → ledger sync → STATUS overwrite → bulletin → rotate → commit),
and the genesis path for a fresh drop-in. Sessions execute the declaration. Two
integrity rules run through everything:

- **EOF sentinels** — every loader file ends with a sentinel comment; a read that
  doesn't end with it was clipped (stale mount, partial read) and gets discarded.
- **Freshness check** — before ANY ledger write, re-read the target from ground
  truth and apply small anchored edits. A session's loaded copy is a stale base;
  whole-file rewrites from it silently revert other sessions' work.

Close fires only on the user's explicit signal — a pause is not an ending, and
paused sessions resume rather than wrap.

## Hardening (optional)

- **`hooks/pre_tool_use.py`** — four structural guards (KERNEL write guard, ABBA
  format guard, sandbox-git guard, STATUS snapshot guard). Wire via
  `hooks/claude-settings.example.json`; env: `SYNC_HOOKS_MODE` = `warn` | `enforce` | `off`.
- **`scripts/rotate.py`** — ledger rotation: journal keep-N overflow to history,
  aged bulletin messages to archive. Run at close from a git-capable session.

## Provenance

Battle-tested before it was packaged: this pattern ran a real multi-agent operation
(five concurrent automated agent roles plus interactive sessions) for months, and its
disciplines — sentinels, freshness checks, mount distrust, the STATUS/journal split —
each exist because a real failure taught the lesson. Successor to `claude-code-ops`.
