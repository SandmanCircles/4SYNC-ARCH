# 4SYNC ARCH

**A Return on Context Harness.** Persistent, multi-session, multi-agent memory for
Claude Code projects — as a drop-in filesystem, not an installation.

**AI coding sessions are goldfish.** Every context window starts cold and dies silent,
and everything a session learned — decisions, task state, naming discipline, deploy
facts — evaporates unless it's deposited somewhere the next session will actually look.

It gets worse the moment you use two surfaces:

> *You edited files in Cowork. It couldn't commit them. Your next Claude Code session
> has no idea any of it happened.*

If you have Claude Desktop, you already have both — Cowork and Claude Code, on the same
folder, with no shared memory and no handoff. Most of what's distinctive in ARCH exists
because of that collision: a queue for commits a bridge session can't make, sentinels
that catch a clipped read through a mount, a post-write check that catches one session
silently reverting another's ledger edit.

4SYNC is where the state goes — the discipline that keeps a project's context lean and
trustworthy across sessions and surfaces, so more of every window goes to the work.
More model, same subscription.

## Quickstart — three steps, no installation

1. **Drop this filesystem into your project folder** (or start from it).
2. **Tell it what your project is** — either fill in `SEED.md` and flip its flag to
   `AUTHORED`, or don't: just open a Claude session in the folder and it will
   interview you through the seed conversationally.
3. **Open a session.** It reads `CLAUDE.md` → `4SYNC.yaml`, detects the untouched
   stack, and runs **genesis** — distilling your seed into the config stack and
   archiving the seed verbatim as the project's birth record (locked read-only).
   **Genesis plays back what it understood — project name, root path, purpose,
   agents, naming — and waits for your go before writing anything.** One question,
   once, on the only run that can never happen twice: it authors the whole stack,
   sets an absolute root, flips the TEMPLATE markers, locks the seed and deletes
   its own bootstrap block, so a misread seed is far cheaper to catch here than to
   unpick afterwards. Every session after that boots oriented and deposits state
   back on close.

No skills to install, no plugins, no per-machine setup. The entire mechanism is two
files that travel with the folder: `CLAUDE.md` (teaches any session the protocol
exists) and `4SYNC.yaml` (declares what this instance's protocol is). Users can layer
their own trigger-phrase skills on top; the system needs none.

## The shape of the protocol

Three pillars, with distinct authority and write discipline:

| Pillar | Files | Discipline |
|---|---|---|
| **Operational state** | `MERGE_PLAN.md` (+ `_ARCHIVE`, `_HISTORY`) | Task ledger + session journal. Append blocks, keep-N, never renumber. |
| **Identity state** | `config/` — `KERNEL` · `STATUS` · `CANON_INDEX` · `REFERENCE` · `HISTORY` | KERNEL edit-rarely · STATUS overwrite-only · INDEX pointer rows · REFERENCE on-demand · HISTORY frozen. |
| **Vocabulary state** | `NAMING_CONVENTIONS.md` | Canonical marks, retired names; loaded before external output. |

Multi-agent extras: `ABBA.md` — a bulletin board of messages addressed by agent
name, which also carries the **roster** of who those agents are; `LANDING_QUEUE.md`
— commit handoffs from surfaces that can't safely run git. Both ship **inert**:
declare an `agents:` block in the manifest to switch them on, omit it and sessions
never read them. Nothing to delete, and adding a surface later is two lines.

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
paused sessions resume rather than wrap. (An unattended run has no pause: finishing
its declared task *is* its signal. It journals and deposits, but never overwrites
STATUS — a nightly job shouldn't rewrite the project's active focus.)

**Trim to taste.** `session_debt`, `agents`, `naming_check`, `rotate`, `meter`, and
the `bulletin` step are each optional — delete any block you don't use and the
protocol still runs. Genesis prunes for you: it drops the blocks your seed didn't
ask for, and deletes its own `bootstrap:` section once it has run, since it can
never fire again.

## Measuring it — `scripts/meter.py`

ARCH's claim is Return on Context, so the boot cost is the number that has to be
real. The meter reads your manifest's load lists and prices them:

```bash
python scripts/meter.py --dir .
```

It reports what boot loads, what is deferred, and the share kept out of every
session's window. Token counts are estimates (bytes ÷ 4), not tokenizer output —
useful for trend and proportion, not for billing.

**Start the series on day one.** `--log` appends one row per run to
`metrics/roc_series.jsonl`:

```bash
python scripts/meter.py --dir . --log --note "after the ledger split"
```

Each row carries the timestamp, the commit, the totals, and **the byte size of
every file in the stack**. The per-file breakdown is the point: the useful
question later is never *did boot grow* but *which file grew* — and that is
unrecoverable from a total after the fact. Add it to your manifest's `close:`
block so it runs at every wrap.

Two properties worth knowing before you rely on it. It **only ever appends** —
a measurement series has no undo, so a run you didn't log is gone for good and
a writer that could truncate would destroy the only copy. And it is **JSONL, not
CSV**, because the boot stack itself changes over time — files get added, split,
deferred, renamed — and fixed columns would break at exactly the moment the
series became interesting.

## Hardening (optional)

- **`hooks/pre_tool_use.py`** — six structural guards (KERNEL-write, ABBA-format,
  sandbox-git, STATUS-snapshot, the **boring-guard** that holds the manifest within
  its own declared `max_bytes` and keeps it declaration-only, and the **root fence**
  that flags a write into a different ARCH instance than the session booted in) plus
  a **session-debt recorder** that flags a session which did work but never ran an
  explicit close — surfaced at the next boot, cleared only by a real close.
  Adding domain guards of your own? See **Adding your own guards** below — not by
  appending to this file's `GUARDS` list.
- **`scripts/rotate.py`** — ledger rotation: journal keep-N overflow to history,
  aged bulletin messages to archive. Run at close from a git-capable session.

### Installing the hooks

The hooks ship inert — nothing runs until you wire them into Claude Code. Wiring is
per-checkout (the paths are machine-specific), so it lives in local settings, not the
committed repo:

```bash
python scripts/wire_hooks.py            # dry run — prints exactly what it would write
python scripts/wire_hooks.py --write    # merge it into .claude/settings.local.json
```

It derives both paths from itself, **proves the interpreter runs before writing it**,
and merges without disturbing settings you already have. Then **reload** — open
`/hooks` once, or restart the session; a `.claude/` folder that didn't exist when the
session started isn't watched mid-session.

Prefer to do it by hand? Copy `hooks/claude-settings.example.json` →
`.claude/settings.local.json` and fix the two paths. Forward slashes work on Windows;
use the *full* Python path if bare `python` is shadowed by the Store stub.

Env knobs: `ARCH_HOOKS_MODE` = `warn` | `enforce` | `off` (start in `warn` — logs,
never blocks; flip to `enforce` after a clean stretch) · `ARCH_DEBT=0` disables the
session-debt recorder · `ARCH_MANIFEST` sets the manifest filename — honored by both
the boring-guard and `scripts/meter.py`, so a renamed manifest is one variable,
not two (default `4SYNC.yaml`). Add `.claude/settings.local.json` and the warn-mode
log to your `.gitignore` — they're local, not shared.

### Adding your own guards

The six shipped guards are **structural** — they protect the shape of the pattern, and
nothing in them is specific to any product or brand. Most adopters eventually want
domain guards too: don't leak this brand name, don't revive that retired concept,
don't edit this protected prompt.

**Do not add them by appending to the `GUARDS` list in your copy of
`hooks/pre_tool_use.py`.** Wire the shipped hook at **user level**
(`~/.claude/settings.json`) and the copy that executes is the shared one, not your
instance's — an appended guard silently never runs, while your settings still say
`enforce` and the log still fills with structural catches. Wire it only at project
level and the append does run, but you lose it every time you upgrade this file, and
you keep the launch-directory bypass that the user-level wire exists to close.

**Use two hooks with disjoint guard sets.** Both are `PreToolUse`; both read
`ARCH_HOOKS_MODE`, so one setting governs both and no guard fires twice:

| | wired at | holds | upgrades |
|---|---|---|---|
| `hooks/pre_tool_use.py` | user (`~/.claude/settings.json`) | the 6 structural guards | replace wholesale |
| `hooks/guards_<org>.py` | project (`.claude/settings.local.json`) | your domain guards | yours, never touched |

Your file is a standalone hook, not an import of this one: same stdin/exit contract
(exit 0 allow, exit 2 block with the reason on stderr), same `ARCH_HOOKS_MODE` read,
its own `GUARDS` list. Copy this file's dispatcher as a starting point and delete the
six guards. The arity dispatch is worth keeping — 4-arg `(tool, path, text, cmd)`
guards and 5-arg ones taking the prospective whole-file content both work.

**Why not auto-discovery?** The obvious alternative — have the shared hook import a
`guards_local.py` from whichever instance a write resolves into — was considered and
**rejected on security grounds** (2026-08-03). A machine-wide hook that auto-imports
Python from any directory a write lands in is an arbitrary-code-execution vector:
clone a repo that ships a `config/` dir and a malicious guards file, write one file
into it, and someone else's code runs with your privileges on every write. Two
explicit hooks cost one settings entry and keep the shared hook from executing
adopter code at all.

### What the guards do and don't cover

Hooks and the session-debt recorder fire on **host-side execution** only. A Cowork
session running in the cloud makes its tool calls there and delivers the bytes to your
disk as a file transfer — there is no local tool event for a local hook to intercept.
That isn't a misconfiguration and no setting fixes it. (If you want the guards in the
loop, start the task with **"On your computer"** in the desktop app's *Run this task*
picker.)

That surface isn't unprotected — it's protected by the **protocol** rather than the
hooks: `LANDING_QUEUE.md` for the handoff it can't commit, EOF sentinels for the reads
it can't trust, the freshness gate and post-write check for the ledger it shares. Those
are executed by a session reading the manifest, so they hold everywhere. The hooks are
local reinforcement; the protocol is the load-bearing layer. Design accordingly.

### Python

**The protocol needs no Python.** Boot, close, and genesis are a session reading
`4SYNC.yaml` and editing markdown and YAML — nothing shells out. Python is only for the
optional hardening, and without it you lose exactly three things: the guards, automatic
ledger rotation (do it by hand — the close steps describe it), and the boot-cost number.

When you do have it: **3.8+**, standard library only. No pip install, ever. PyYAML is
used *if present* and every script falls back to a hand-rolled parser without it.

<details>
<summary><b>Troubleshooting</b> — the failures that actually happen</summary>

- **The hook never fires.** Three usual causes, in order of likelihood: (1) settings
  resolve at launch from the git-repo root, so a session started somewhere else — a
  parent folder, a different repo — never reads them; (2) you created `.claude/` mid-session
  and didn't reload; (3) the interpreter path doesn't execute. `scripts/wire_hooks.py`
  rules out (3) by testing before it writes.
- **On Windows, bare `python` may be the Microsoft Store stub.** It sits on PATH and
  does not run scripts. Always wire the full interpreter path.
- **A read that doesn't end with its `# ═══ EOF … ═══` sentinel was clipped.** Discard
  it and re-read host-side. Never write on top of a bad read — that is how one session
  silently reverts another's work.
- **A guard fired on an edit you meant to make.** That's the design. `warn` mode logs
  and allows; the KERNEL guard takes `CLAUDE_KERNEL_EDIT=1` for deliberate edits.
- **Don't cite `/status`** — it's an interactive terminal panel and isn't available in
  every environment. Probe the hooks empirically instead: make a trivial write and check
  whether `.session_debt.tsv` gained a row.

</details>

## Provenance

Battle-tested before it was packaged: this pattern ran a real multi-agent operation
(five concurrent automated agent roles plus interactive sessions) for months, and its
disciplines — sentinels, freshness checks, mount distrust, the STATUS/journal split —
each exist because a real failure taught the lesson.

## License

Source-available under the [Functional Source License, Version 1.1, ALv2 Future
License](LICENSE) (`FSL-1.1-ALv2`) — © 4 SHIELD LLC.

Use it, modify it, self-host it, build on it, run it inside your company, and use it
in professional services you provide to others. The single restriction is **Competing
Use**: you may not offer 4SYNC ARCH — or a substantially similar substitute — as a
commercial product or service. Each version converts to Apache 2.0 two years after
its release.

Not an OSI-approved open-source license, and deliberately so. The `4SYNC`, `ARCH`, and
`4 SHIELD` marks are not licensed with the software.
