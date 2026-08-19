# 4SYNC ARCH

[![tests](https://github.com/SandmanCircles/4SYNC-ARCH/actions/workflows/ci.yml/badge.svg)](https://github.com/SandmanCircles/4SYNC-ARCH/actions/workflows/ci.yml)

**[4sync.ai](https://www.4sync.ai)** · [FAQ](https://www.4sync.ai/faq.html) · [Origin](https://www.4sync.ai/origin.html) · [Case study](https://www.4sync.ai/case-study.html) · [Return on Context](https://www.4sync.ai/whitepapers/return-on-context.html)

**A Return on Context Harness.** Persistent, multi-session, multi-agent memory for
Claude Code projects — as a drop-in filesystem, not an installation.

**Every context window starts cold.** Everything a session learned — decisions, task
state, naming discipline, deploy facts — evaporates unless it's deposited somewhere the
next session will actually look.

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

## Prerequisites

- **Git** — required, not optional: the instance folder must be a repository. Genesis
  asks ("has git been initiated?") and offers `git init` if it hasn't — git is the undo
  behind every close, the dirty-tree gate rotation refuses on, and the whole exit story.
- **Python 3** (3.8+, standard library only — no pip install, ever)
- **Claude Code**

See [Python](#python) and [Hardening](#hardening-optional) below for what each one is
actually used for.

## Quickstart — four steps, no installation

1. **Start in a new, empty folder — genesis makes it permanent.** Create a fresh folder
   for this project, run `git init` in it, and open your Claude session there — ARCH
   requires a repository, and genesis will ask and offer to create one if you skip
   this. **Not your Downloads or Desktop folder, and not an existing repo** — ARCH
   wants a folder of its own. Genesis writes
   that folder's absolute path into the manifest as `instance.root`, and every close
   resolves from it no matter where a later session is launched. You can move the folder
   afterwards, but you have to fix `instance.root` and the absolute hook paths in
   `.claude/settings.json` by hand — and until you do, **closes write to the old location
   and the guards stop firing.**
   **Think twice about a cloud-synced folder** (iCloud Drive, Google Drive, Dropbox,
   OneDrive). It is the same permanent choice in a second form: the sync client rewrites
   paths when it relocates or offloads a folder, and it will happily replicate a
   half-written ledger between machines while two sessions are editing it. If you want
   the same project on more than one machine, use git for that and keep the instance
   root on local disk.
2. **Drop this filesystem into that folder** (or start from it).
3. **Tell it what your project is** — either fill in `SEED.md` and flip its flag to
   `AUTHORED`, or don't: just open a Claude session in the folder and it will
   interview you through the seed conversationally.
4. **Open a session.** It reads `CLAUDE.md` → `4SYNC.yaml`, detects the untouched
   stack, and runs **genesis** — distilling your seed into the config stack and
   archiving the seed verbatim as the project's birth record (locked read-only).
   **Genesis plays back what it understood — project name, root path, purpose,
   agents, naming — and waits for your go before writing anything.** One question,
   once, on the only run that can never happen twice: it authors the whole stack,
   sets an absolute root, flips the TEMPLATE markers, locks the seed and deletes
   its own bootstrap block, so a misread seed is far cheaper to catch here than to
   unpick afterwards. Every session after that boots oriented and deposits state
   back on close.

No skills to install, no plugins — and the **protocol** needs no per-machine setup:
the entire mechanism is two files that travel with the folder, `CLAUDE.md` (teaches
any session the protocol exists) and `4SYNC.yaml` (declares what this instance's
protocol is). The optional **enforcement layer** — guards, boot receipt, session-debt
recorder — is the one per-machine part: wired once per machine by
`scripts/wire_hooks.py` (see Hardening), and a clone on a new machine starts unwired
until you do.

### Closing a session — "wrap up"

Tell the session you're done, in whatever words you'd normally use — *"wrap up,"
"let's close out," "I'm finished."* It then executes the manifest's `close:` steps
in order: re-read the shared files before writing them (`freshness_check`), prepend
one block to the session journal, mirror task state back to the ledger, overwrite the
facts that changed in `STATUS.yaml`, handle the bulletin board, and run rotation.
Committing is spelled out in `CLAUDE.md` rather than the manifest, because it depends
on whether the session can reach git.

**Nothing is installed and nothing is intercepting the phrase.** The session closes
because `CLAUDE.md` told it to read `4SYNC.yaml`, and `4SYNC.yaml` declares what
closing means. That is the whole mechanism.

Close fires **only on an explicit ending signal**. Silence, idling and pausing are not
endings — a paused session resumes, it doesn't wrap. (An unattended run has no pause:
finishing its declared task is its signal.)

If you'd rather type a slash command than a phrase, write one in *your* `.claude/` —
never in this folder — and have it do nothing but read `4SYNC.yaml` and execute
`close:`. That's a convenience for your fingers and deliberately not a dependency: the
protocol has to run for a session that has never heard of your shortcut — a scheduled
job, a teammate's machine, a different surface. Anything that only works when a
shortcut is installed has moved a load-bearing step out of the folder that travels.
Keep the manifest authoritative.

For a filled-in instance end to end — seed, the config genesis writes from it, and
one of each artifact in use — see **[`EXAMPLE.md`](EXAMPLE.md)**.

Once it is running, **[`TIPS.md`](TIPS.md)** is the short version written for you rather
than for the session — what to say, what to expect, how to answer a permission prompt,
and what to keep an eye on.

Before you commit a real project to it, **[`ADOPTING.md`](ADOPTING.md)** covers the
three questions this file does not: how to trial it in a folder you intend to delete,
how to bring in a project that already exists (including reconstructing its history
without paying for it at every boot), and how to remove it again if you decide against
it. The exit story is there on purpose — it is the last thing people ask and the first
thing worth knowing.

## The shape of the protocol

Three pillars, with distinct authority and write discipline:

| Pillar | Files | Discipline |
|---|---|---|
| **Operational state** | `MERGE_PLAN.md` (+ `_ARCHIVE`, `_HISTORY`) | Task ledger + session journal. Append blocks, keep-N, never renumber. |
| **Identity state** | `config/` — `KERNEL` · `STATUS` · `CANON_INDEX` · `REFERENCE` · `HISTORY` | KERNEL edit-rarely · STATUS **overwrite the fact, never the file** · INDEX pointer rows · REFERENCE on-demand · HISTORY frozen. |
| **Vocabulary state** | `NAMING_CONVENTIONS.md` | Canonical marks, retired names; loaded before external output. |

Multi-agent extras: `ABBA.md` — a bulletin board of messages addressed by agent
name, which also carries the **roster** of who those agents are; `LANDING_QUEUE.md`
— commit handoffs from surfaces that can't safely run git. Both ship **inert**:
set `close.bulletin.check_at_boot: true` to switch the board on, leave it `false`
and sessions never read it. Nothing to delete, and adding a surface later is two
lines. (Gate on that key, not on whether you have an `agents:` block — the meter
prices the board off `check_at_boot`, and gating the two differently makes the
manifest declare one thing while the meter measures another.)

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
  **`config/STATUS.yaml` is where this rule gets broken, and the reason is a
  word.** Its write mode is *overwrite*, which is a plausible instruction to
  rewrite the whole file — so read it as **overwrite the FACT, never the FILE**.
  Replace the one value that changed, in place. Every other shared file resists
  the mistake by shape: nothing about a prepend-and-flip-rows ledger invites a
  wholesale rewrite, and that is the only reason `MERGE_PLAN.md` survives two
  concurrent sessions while STATUS does not. **This is the single confirmed way
  to lose data in ARCH.**

### The byte cap is yours

`integrity.manifest_rules.max_bytes` is **your number.** The boring-guard reads it
out of your own manifest and enforces whatever it finds — there is no product-wide
constant, and **no release will ever ship you a value or ask you to change one.**
The cap you were born with is a starting point, not a spec.

What matters is not the size but how you move it: **a cap you raise on reflex is
not a cap.** This project's own manifest went 8192 → 10240 → 12288 → 16384 across
its life, each raise made in the same edit that hit the ceiling, and every one of
them bought new *capability* — more declarations, a genesis step, two more close
scripts. Narrative got cut instead. Raise it when a real declaration will not fit;
trim it when what is filling it is prose. Doing neither and reading the refusal as
a bug is the only wrong answer.

**A tight cap is a full budget, not an error state** — but know which kind of line
you are dropping. A line a session *executes* (a boot file, a close step) is
load-bearing and skipping it changes behaviour. A line that only *documents* is
not: `session_debt.max_age` is the live example — the hook reads that window from
its own constant and `ARCH_DEBT_MAX_AGE_DAYS`, never from the manifest, so writing
`max_age: 30d` changes nothing and omitting it costs nothing. **If bytes are
scarce, the documentary lines are the ones to leave out.**

Close fires only on the user's explicit signal — a pause is not an ending, and
paused sessions resume rather than wrap. (An unattended run has no pause: finishing
its declared task *is* its signal. It journals and deposits, but never overwrites
STATUS — a nightly job shouldn't rewrite the project's active focus.)

**Genesis renames the stack after your project.** `config/KERNEL.yaml` becomes
`config/ACMEROBOTICS_KERNEL.yaml`, and `4SYNC.yaml` itself becomes
`ACMEROBOTICS.yaml` — the prefix is your `instance.name`, uppercased, non-alphanumerics
dropped. Run two instances on one machine and this is what tells them apart: without
it every instance has a `config/KERNEL.yaml`, and a wrong-instance read looks exactly
like a right one in the tool call, the log, and the reasoning trace. Genesis updates
every reference in the same pass and sets `ARCH_MANIFEST` in `.claude/settings.json`,
which is what keeps the guards, the boot receipt, the meter and rotation pointed at the
renamed manifest. **That pin covers Claude Code sessions and nothing else** — run
`meter.py`, `rotate.py` or `actuals.py` from a plain terminal and they fall back to the
default filename and report on a manifest you no longer have. Export it in your shell
(`export ARCH_MANIFEST=ACMEROBOTICS.yaml`, or set it per-command) whenever you run the
close-time tools by hand, which is exactly how they are meant to be run. `CLAUDE.md` is never renamed — Claude Code finds it by exact name —
and neither are the root documents; this is provenance for the identity stack, not a
project-wide rename.

**Genesis also clears our packaging out of your root.** `README.md`, `EXAMPLE.md` and
`LICENSE` move into `arch/`, keeping their names — still tracked, never ignored. The
folder is already there before genesis runs, holding `arch/VERSION`, and it is called
`arch/` rather than `archive/` because nothing in it is dead: the license governs the
code, the version is the one you are running, and the packaging is still worth reading. That matters most for the license:
left at root, `LICENSE` is what GitHub reads as *your* repo's license the moment you run
`git init`, so your work would show up as FSL-licensed under our copyright notice. Moved,
it still travels with the code it covers, which is what the license actually asks for.
`.gitignore` stays at root — its entries are live runtime state, including the
gitignored session-debt file the protocol depends on.

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

## Measuring what it *actually* cost — `scripts/actuals.py`

The meter prices what your manifest **declares**. This prices what your sessions
**spent** — by reading the transcripts Claude Code already writes for every
session, whether or not you have ever looked at them.

```bash
python scripts/actuals.py --dir .
```

The two are complements and should always be read side by side, labelled:

| | `meter.py` | `actuals.py` |
|---|---|---|
| Reads | file sizes on disk | session transcripts |
| Answers | what boot *should* cost | what a session *did* cost |
| Nature | estimate (bytes ÷ 4) | measurement |
| Available | before a session runs | after |
| Attribution | **per file** | none — the API sees one prefix |

Never merge the two numbers. An estimate and a measurement that get averaged
together stop being either.

**Run it early, because the data you most want expires first.** Claude Code
prunes transcripts oldest-first (`cleanupPeriodDays`), so the sessions that
show what your project cost *before* it adopted a loader stack are exactly the
ones that disappear soonest. Extracting is cheap and permanent; the source is
not. On the instance this was built in, 66 sessions reduced to **31 KB** —
small enough to commit as evidence — against 130 MB of transcripts that can now
expire without losing anything.

```bash
python scripts/actuals.py --dir . --all --log
```

`--log` appends one row per session to `metrics/actuals_series.jsonl`, keyed on
(project, session) so re-running never duplicates. Add it to your manifest's
`close:` block. Unlike the meter it is **idempotent and self-healing** — a close
you skipped is picked up next time — but only inside the retention window.

**It reads usage integers and never message content.** This matters more than it
sounds: transcripts contain the full verbatim conversation *including tool
results*, which is where file contents and command output land. The test suite
plants a marker string in user text, assistant text, and a tool result, then
asserts it reaches no output. That is why the extract is safe to keep, safe to
commit, and safe to hand to someone else.

**Calibrate your expectations before you optimise.** On the instance this was
built in, roughly **53K tokens were already resident before a single boot file
was read** — system prompt, tool definitions, `CLAUDE.md` — stable within ~2K
across eleven sessions. That was 2.5× the entire boot stack. Once boot
completed, about 62% of resident context was harness overhead nobody can touch
and about 25% was the part the protocol controls. Your numbers will differ, but
measure them before deciding how much a leaner stack can return: it is the
ceiling on the whole exercise, and it is the honest denominator for any Return
on Context claim you make.

Two limits to state plainly. It **cannot tell you which file cost what** — only
the meter can, because the API is handed one undifferentiated prefix. And it
depends on the transcript format of the Claude Code version that wrote them; if
that changes, it reports *"no usable transcripts found"* rather than guessing.

## Hardening (optional — but not if you run more than one session at a time)

**"Optional" is true at one session and false at concurrency**, for one specific
reason: the **session-debt recorder lives inside `hooks/pre_tool_use.py`**. No
hooks means no `.session_debt.tsv`, which means the boot-time *"do you have
company?"* reading has nothing to read — so two or three sessions open on the
same instance are completely blind to each other. **The only mechanism that makes
concurrency visible is in this section.** Run one session at a time and the rest
of this is a preference; run several and wire the hooks first.

> **These guards are a collaboration protocol, not a security control.** They
> exist so a change to your project's identity documents doesn't happen
> *quietly* — not so it becomes impossible. The hook runs in the same trust
> domain as the files it guards: anything an agent can reach, an agent can
> route around. That is the working assumption, not an unpatched flaw. Git plus
> review is still the boundary for deliberate change; these exist so nothing
> reaches that review unannounced. Install them with that expectation and they
> earn their keep. See **What the guards do and don't cover** for the specifics,
> including exactly where the coverage stops and why some of it never closes.

- **`hooks/pre_tool_use.py`** — seven structural guards (KERNEL-write, ABBA-format,
  sandbox-git, STATUS-snapshot, the **boring-guard** that holds the manifest within
  its own declared `max_bytes` and keeps it declaration-only, the **root fence**
  that flags a write into a different ARCH instance than the session booted in, and the
  **STATUS stale-write guard** that stops a whole-file rewrite of STATUS to show you
  which lines currently on disk it would remove) plus
  a **session-debt recorder** that flags a session which did work but never ran an
  explicit close — surfaced at the next boot, cleared only by a real close.
  Adding domain guards of your own? See **Adding your own guards** below — not by
  appending to this file's `GUARDS` list.

  **The debt file is EVIDENCE, not protection — set your expectations there.** It
  takes no lock and prevents nothing; it records that a session was working, so
  the *next* boot can say whether anyone else is in the folder right now. Its real
  value shows up after something goes wrong: a row proving two sessions were live
  in the same minute is the only artifact that can establish it, and where there
  were no hooks there is no row and no account of what happened. Three limits travel
  with that claim. `last_activity` records **file writes only** — a session mid-
  commit, or one that has spent twenty minutes reading, looks idle — so *not live*
  means *probably idle*, never *gone*. A nested repo that is itself an ARCH
  instance keeps its **own** debt file, so a session editing both leaves a row in
  each and an ordinary close clears one. And the file is **per-machine** — local and
  gitignored — so the *do you have company?* reading is scoped to the machine you are
  on: a desktop session and a laptop session contesting the same ledgers through git
  are mutually invisible here, and the anchored-edit discipline plus git conflicts
  are the backstop across machines.

  **One dependency note, because it decides what a check can promise.** YAML
  *parse* validation in the STATUS guard requires **PyYAML**, which is not in the
  standard library and is therefore absent on a fresh Python. Without it that one
  check is skipped and the guard falls back on its EOF-sentinel and
  `last_touched` checks, which still block — so a clipped or bloated write is
  caught either way, and a structurally broken YAML file is not. Nothing else
  degrades: the manifest guard reads two known keys by regex when PyYAML is
  missing and keeps enforcing. `pip install pyyaml` if you want the parse check.
- **`hooks/session_start.py`** — a **boot receipt** injected at session start:
  which instance this is, the ordered boot stack with its measured cost, any
  missing EOF sentinel, and the session-debt reading (sessions live *right now*
  vs. sessions holding undeposited state). See **Making boot non-optional** below.
- **`scripts/rotate.py`** — ledger rotation *and* the close's arithmetic check.
  It **moves** (journal keep-N and size overflow to history, closed tasks' docs to
  `tasks/closed/`, aged bulletin messages to archive) and **derives** one line (the
  ledger `Tally`, computed from the rows rather than typed). Everything else it
  only **measures and reports, never blocking a close**: ledger and journal size,
  over-long Subject cells, prose outweighing rows, trigger-less findings, the
  "Pickup-ready" list against the ⏳ rows, and the hand-copied numbers in your
  STATUS file — manifest caps, byte counts attributed to a named path, test-suite
  counts, commit SHAs, boot cost — each checked against the thing it claims to
  describe. Run at close from a git-capable session.

  **It refuses a dirty tree, so the close is a two-step — run it between two
  commits.** `--apply` moves files and rewrites the `Tally`, and it will not do
  that on top of uncommitted work: *"git is the undo."* So the real order is
  **commit your session's edits → `rotate.py --apply` → commit what it moved**,
  or pass `--allow-dirty` if you know what you are giving up. The refusal is
  correct and stays; it reads as the tool being broken only because nobody wrote
  the order down. (If it refuses when you believe you committed, check that your
  commit actually ran — a shell alias that did not expand is the reported cause.)

  **Why one pass rewrites and the rest report.** A `Tally` is a count of rows
  sitting right there and needs no judgement, so it is derived. A STATUS number sits
  *inside* prose that carries the reasoning around it; rewriting the number would
  mangle the argument. So those are reported and you fix the sentence.

  **A claim must identify itself to be checked.** A byte figure needs a real path
  beside it, a suite count needs a real `test_<name>.py`, a short commit hash needs
  something nearby saying it is one. Anything that does not identify itself is
  treated as prose and passes in silence. That costs some coverage on purpose: a
  report that flags prose is a report people learn to ignore, and an ignored report
  is worth less than none. It also gives your STATUS file a house style — phrase a
  fact so the check can reach it. Claims about a file's *contents* are out of scope
  and always will be.

### Installing the hooks

The hooks ship inert — nothing runs until you wire them into Claude Code. Wiring is
per-checkout (the paths are machine-specific), so it lives in local settings, not the
committed repo:

```bash
python scripts/wire_hooks.py            # dry run — prints exactly what it would write
python scripts/wire_hooks.py --write    # merge it into .claude/settings.local.json
python scripts/wire_hooks.py --status   # is THIS machine wired for THIS instance?
```

**A second machine starts unwired — and nothing tracked can say so.** Wiring is
machine-local by design (absolute interpreter and hook paths, in gitignored settings),
so git-syncing an instance to another machine carries the protocol and none of the
enforcement layer: the clone boots `CLAUDE.md`-only, silently — no guards, no
session-debt recorder, and no boot receipt, which is exactly the channel that would
have announced the gap. Field-reported by an adopter running two machines. The routine
is clone → `wire_hooks.py --write` for the guards, then paste the SessionStart block
the script prints into `~/.claude/settings.json` for the receipt — `--write` never
wires the receipt, so a machine that skips the paste has guards but no banner — once
per instance root, per machine. `--status` turns "am I wired here?" into a report
with an exit code instead of an inference from silence, and it checks both halves.

It derives both paths from itself, **proves the interpreter runs before writing it**,
and merges without disturbing settings you already have. Then **reload** — open
`/hooks` once, or restart the session; a `.claude/` folder that didn't exist when the
session started isn't watched mid-session.

**If your instance is a subfolder of a larger project, it writes one level out — and
says so.** Claude Code reads settings from the root of the **git repository** (through
worktrees to the main checkout), so one file covers sessions started in any
subdirectory. Put ARCH at `myapp/ops/` and the settings belong at `myapp/.claude/`, not
`myapp/ops/.claude/`. The script resolves this with `git rev-parse --show-toplevel`,
prints the root it chose and why, and warns you when that is outside the instance. Two
cases keep the file with the instance instead: you are not in a git repository, or the
repository root is your home directory. A nested repo that is **its own** repository is
its own settings root and is left alone. *(Before v1.0.7 the script always wrote to the
instance root, so a nested layout got a settings file nothing ever read — reported as
success. If you wired a nested instance on an earlier version, re-run this and delete
the stranded `<instance>/.claude/settings.local.json`.)*

**It also wires `ARCH_MANIFEST` for you when your manifest has been renamed.** Genesis
renames the manifest per project and merges the variable into `.claude/settings.json`;
if the file Claude Code actually loads is a different one, that merge never reaches it.
The script finds the manifest by content, not by name, and fills the blank — without
overwriting a value you already set.

Prefer to do it by hand? Copy `hooks/claude-settings.example.json` →
`.claude/settings.local.json` and fix the two paths. Forward slashes work on Windows;
use the *full* Python path if bare `python` is shadowed by the Store stub.

Env knobs: `ARCH_HOOKS_MODE` = `warn` | `enforce` | `off` (start in `warn` — logs,
never blocks; flip to `enforce` after a clean stretch, and read *What the first
adoption found* below before you trust a warn log) · `ARCH_DEBT=0` disables the
session-debt recorder · `ARCH_MANIFEST` sets the manifest filename — honored by both
the boring-guard and `scripts/meter.py`, so a renamed manifest is one variable,
not two (default `4SYNC.yaml`). Add `.claude/settings.local.json` and the warn-mode
log to your `.gitignore` — they're local, not shared.

### Where to wire them — project level or user level

> **One project: wire the project. Multiple instances, or sessions launched from
> outside them: wire the user level.**

This is the decision the commands above don't make for you, and it is not
guessable from them:

- Claude Code resolves settings **once at launch, from the git-repo root of the
  start directory**. A project-level wire therefore protects only sessions
  *launched inside that project* — a session started one directory up and drilled
  down into it carries no guards at all. That is not a bug and no setting changes it.
- `~/.claude/settings.json` is **user-level, not parent-directory**. Every session
  reads it regardless of where the project lives — another drive, anywhere. That
  is what makes it the answer for multi-instance setups, and it is a common
  misreading: it looks like directory inheritance and isn't.
- The guards match on the **target path**, not the working directory, so one
  user-level wire covers every ARCH instance on the machine at once — including
  instances whose config stack is prefixed (`config/ACME_KERNEL.yaml` trips the
  KERNEL guard exactly as `config/KERNEL.yaml` does).

**Both is safe, and verified.** Claude Code **dedupes identical hook commands**
across settings sources: with a user-level and a project-level wire both live, a
guard event produces one log line, not two. So a project `.claude/settings.local.json`
can stay for its `env` values while the user-level wire does the covering. (Earlier
revisions of this file said "not both" — that was written while the behavior was
unverified, and the verification changed the advice.)

**`wire_hooks.py` writes the project level only.** The user-level wire is a
deliberate manual step: an assistant editing your global settings is a command
that would then run on every tool call in every project, and that is a decision
for a human to make by hand. Copy `hooks/claude-settings.example.json` into
`~/.claude/settings.json` and fix the paths. Two traps that cost real time: a
**named** comment key such as `"//note"` was rejected by the settings schema
(2026-07-30) — the single bare `"//"` key that `wire_hooks.py` writes is fine, so
keep comments to that one — and if you set `ARCH_HOOKS_MODE=enforce` at the user
level you have set it for **every ARCH instance on the machine**, which is usually
what you want and never what you want by accident.

### Making boot non-optional

Boot is otherwise enforced by prose: `CLAUDE.md` tells a session to read the
manifest and load the stack, and nothing checks that it did. That gap is not
hypothetical. Asked point-blank what it had loaded, one session answered:

> "I never ran the boot sequence this session. I have not loaded the KERNEL,
> STATUS, MERGE_PLAN, NAMING_CONVENTIONS, ABBA, or DEFECTS."

It had oriented on the `CLAUDE.md` files and a folder listing, and no code
noticed — because no code was watching. Wire the `SessionStart` hook and it is:

```json
"SessionStart": [
  { "hooks": [ { "type": "command",
                 "command": "\"/full/path/to/python\" \"/path/to/hooks/session_start.py\"" } ] }
]
```

`wire_hooks.py` does **not** write this block — it wires the PreToolUse guards only, so
the receipt is hand-wired. Put it at **user level** (`~/.claude/settings.json`), not
project level: the sessions that skip boot are the ones launched outside the repo, and
those never read project settings at all. Use the full interpreter path, as above —
bare `python` on Windows may be the Store stub, which sits on PATH and runs nothing.

Two modes, via `ARCH_BOOT_MODE`:

| mode | what it injects | cost |
|---|---|---|
| `announce` *(default)* | the receipt — instance, ordered boot list, measured cost, sentinel status, debt readings | a few hundred tokens |
| `inject` | the receipt **plus the contents of every boot file** | your whole boot budget, every session |

`announce` makes it impossible for a session to *not know* it was supposed to
boot. `inject` makes booting not a choice at all. Start with `announce`; reach
for `inject` when you have a surface that keeps skipping anyway. `off` disables.

It resolves the instance from cwd **strictly** — outside an ARCH instance it
prints nothing and exits 0, which matters because the placement that fixes the
launch-directory bypass is user level, where it runs for every session on the
machine. It never blocks: any error and the session proceeds without a receipt.

**The limit that must travel with it:** a **cloud** Cowork session gets no hooks
at all, so it gets no receipt either. On that surface the instrument is the probe
that caught the failure above — *ask the session to summarise what it loaded, and
to look nothing up.* Cheap, and worth running on any surface.

### Adding your own guards

The seven shipped guards are **structural** — they protect the shape of the pattern, and
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
| `hooks/pre_tool_use.py` | user (`~/.claude/settings.json`) | the 7 structural guards | replace wholesale |
| `hooks/guards_<org>.py` | project (`.claude/settings.local.json`) | your domain guards | yours, never touched |

Your file is a standalone hook, not an import of this one: same stdin/exit contract
(exit 0 allow, exit 2 block with the reason on stderr), same `ARCH_HOOKS_MODE` read,
its own `GUARDS` list. Copy this file's dispatcher as a starting point and delete the
seven guards. The arity dispatch is worth keeping — 4-arg `(tool, path, text, cmd)`
guards, 5-arg ones taking the prospective whole-file content, and 6-arg ones that also
receive the session context all work.

**Why not auto-discovery?** The obvious alternative — have the shared hook import a
`guards_local.py` from whichever instance a write resolves into — was considered and
**rejected on security grounds** (2026-08-03). A machine-wide hook that auto-imports
Python from any directory a write lands in is an arbitrary-code-execution vector:
clone a repo that ships a `config/` dir and a malicious guards file, write one file
into it, and someone else's code runs with your privileges on every write. Two
explicit hooks cost one settings entry and keep the shared hook from executing
adopter code at all.

### What the first adoption found

ARCH's first outside adoption produced five product defects. Every one was
invisible from inside the instance the product was written in — which is the
argument for treating an adoption as a **test**, not a delivery. The pattern is
worth more than the five fixes, so here it is as a checklist to run against any
guard or manifest key you add:

| Shape | What happened | The check it implies |
|---|---|---|
| **The product documented a workflow it does not support** | The hook's docstring prescribed an extension mechanism that cannot execute under the wiring the product recommends. An adopter following the docs loses their guards silently. | Every extension point named in prose needs a test proving it executes. |
| **A guard rejected the product's own format** | The bulletin guard demanded `To:` at line start; every documented example, including the shipped template, puts it inline. At `enforce` the board was unwritable. | A guard and the format it guards must share a fixture. If your template doesn't pass your guard in a test, the guard is wrong. |
| **A declaration was decorative** | `close.journal.overflow_to` was parsed and obeyed by nothing, so an instance declaring its own history file had rotation quietly scatter blocks into a second, undeclared one. | Every manifest key needs a test proving that changing it changes behavior. A key that is read but not honoured is worse than an absent one, because it is trusted. |
| **Following the advice broke the tooling** | The docs tell adopters to rename their manifest; doing so failed four tests, with no way to tell those failures from real ones. | Run the suite in an instance that has taken every piece of advice the docs give. The adoption path itself needs a test. |

**And the meta-finding, which is the one to keep:**

> **Warn mode hides the class of defect where the guard itself is wrong.**
>
> Two of those defects had been latent since the day they shipped. The hook ran
> with no `ARCH_HOOKS_MODE` set, so it defaulted to `warn`: it logged the false
> positive, **allowed the action, and the log line read exactly like a real
> catch.** It was recorded as one. The first instance to run at `enforce` hit
> both within the hour.
>
> A guard that is never enforced is never tested. **Run `enforce` early, on one
> instance, before you trust warn-mode logs as evidence of anything.**

### What the guards do and don't cover

**Shell writes are covered now — loudly, not airtightly.** Guards resolve from
the *target path*, not from the tool name, so `Set-Content config/KERNEL.yaml`
trips the same wire as `Edit`. Until 2026-08-05 it did not: the path-scoped
guards checked *which tool* before looking at *which file*, and `Bash` was not
on the list, so every one of them — including the cross-instance fence — could
be walked past with a one-liner.

**The bar is loud, not impossible.** A determined caller can obfuscate a path
past any regex, and that is explicitly not the target. The target is that
routine tooling, a helpful one-liner, and an agent taking the path of least
resistance all trip the same wire a direct edit does. These protect against
**drift and accident**, not against a determined agent. An agent working in good
faith cannot reshape your doctrine quietly; an agent that wants to route around
them can. Design accordingly.

**An asymmetry that is intended, not a shortfall.** Guards that judge a file's
*resulting content* — is it clipped, does it still parse — cannot evaluate a
shell write at all, because what a command will produce is unknowable without
running it. Reached through `Bash` those guards surface the target and say they
cannot inspect the result, rather than assert a verdict they cannot support. A
guard that cannot see the truth should stay quiet, not guess. Guards that are
pure *path* decisions — the KERNEL guard, the instance fence — reach a full
verdict either way.

**Some guards ask instead of refusing.** A guard that has found a *decision*
(editing doctrine) puts the call to you as a permission prompt carrying its own
reason; a guard that has found a *defect* (a clipped write) still blocks. One
approval, in session, no restart and no environment variable — which matters
because the old `CLAUDE_KERNEL_EDIT=1` override had to exist in the environment
that *launched* Claude Code, something the desktop app, the editor extensions
and scheduled runs give you no way to do. It still works, deprecated, and now
logs when it is honoured.

> **`ask` is ergonomics, not an authorization boundary — measured, not assumed.**
> It *requests* a prompt. Where no prompt can be shown, the ambient permission
> decision stands, and it resolves to **allow** under `acceptEdits`,
> `bypassPermissions`, `--allowedTools`, and a `permissions.allow` entry in
> `settings.json` — which is itself a file an agent can write. Confirmed working
> in interactive use; confirmed bypassable in four configurations. If you need a
> real boundary, this is not it, and no hook in this trust domain could be: the
> hook runs beside the files it guards, so whatever can edit them can edit it.

And a limit that no hook can close: these fire on **agent tool calls**. If you
open `KERNEL.yaml` in your own editor, nothing fires — no log, no prompt, no
record. Git catches it at commit; nothing catches it before.

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

**Running the suites.** All six are `unittest`, standard library, no test runner to
install. Two commands cover everything:

```bash
python -m unittest discover -s scripts -t scripts -p "test_*.py"
```

```bash
python -m unittest discover -s hooks -t hooks -p "test_*.py"
```

Any single suite also runs on its own — `python scripts/test_rotate.py`. If you prefer
`pytest` it collects all six, but nothing here needs it, and a green run under plain
`unittest` is the supported answer. *(Until 2026-08-05 `test_split_ledger.py` was a
hand-rolled harness rather than a `unittest` suite, so `pytest` collected 0 tests from
it and exited 5 — a passing suite that reported as a failure to anyone running one
command across all six. Converted; see MP#47/D6.)*

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
- **A guard fired on an edit you meant to make.** That's the design. Under `enforce`,
  a guard that found a *decision* asks — approve the prompt and the call proceeds, once.
  A guard that found a *defect* (a clipped or unparseable write) blocks, and the fix is
  the write, not the guard. `warn` mode logs and allows everything. The legacy
  `CLAUDE_KERNEL_EDIT=1` override still works and is deprecated; prefer the prompt.
- **A guard fired on a shell command that was only reading.** It shouldn't — write
  intent is required, so `cat`/`grep`/`Get-Content` stay silent. If a read did trip
  one, that's a bug worth reporting rather than a setting to change.
- **Don't cite `/status`** — it's an interactive terminal panel and isn't available in
  every environment. Probe the hooks empirically instead: make a trivial write and check
  whether `.session_debt.tsv` gained a row.

</details>

## Staying current — "am I running the latest machinery?"

> **Updating? Read [`RELEASE_NOTES.md`](RELEASE_NOTES.md) first.** It lists what to replace
> per release, states the manifest change each one needs, and carries a paste-able prompt
> that walks a session through the whole update — machinery, manifest, parse check, suites.

**From v1.1.0 there IS an update command, and it covers exactly one of the three
buckets below.** Clone the release you want and run the CLONE's updater against your
instance — yours is older and does not know about the release it is applying:

```bash
python <PATH-TO-CLONE>/scripts/arch_update.py --from <PATH-TO-CLONE> --dir . --expect <build-id>
```

Dry run by default; `--apply` writes. It copies only the machinery inventory and
**refuses to write any path outside it**, verifies the clone against `--expect` before
writing anything, and recomputes your build id afterwards to prove the update landed.

It then prints **what copying did not do**: the `**Manifest:` and `**By hand:` lines
from every release between your version and the clone's, oldest first, read from the
clone's `RELEASE_NOTES.md`. Those are the steps nothing else reports — a copy alone is
self-evidencing, since the build id either matches or does not, while a missed manifest
edit or an unmoved file is silent until something else breaks.

**It cannot do the other two buckets, and that limit is structural rather than an
oversight.** ARCH is copied, not installed: genesis renames your stack, rewrites the
manifest with your `instance.root`, and moves this README and the license out of your
root so `git init` doesn't mark your project FSL-licensed. So for your instance files
and your manifest there is no upstream to diff against, the filenames no longer line
up, and most of the content is *yours*. Machinery is the one bucket where none of that
is true — generic, never renamed, replaced wholesale — which is why it is the one
bucket a tool can own.

So sort the files into three buckets, because only one of them is ever "updated":

- **Machinery** — `hooks/pre_tool_use.py`, `hooks/session_start.py`, their two suites,
  and `scripts/*.py` with theirs, **listed canonically in `scripts/arch_build.py` —
  ask it rather than counting, since the inventory grows.** Generic, never renamed,
  not meant to be edited by you. **Updating means replacing the file**; byte-identical
  is the correct outcome, and `arch_update.py` does exactly this bucket.
- **Your instance** — the `config/` stack, the ledger, naming conventions, journal,
  task documents. **Never touched by an update, ever.** These are the asset you
  adopted ARCH to accumulate.
- **The manifest** — the awkward one. It carries your `instance.root` and your
  project's name, but its `boot:` / `close:` / `integrity:` *structure* is product
  shape. A new close step has to be **merged in by hand**; it cannot be copied over.
  **[`RELEASE_NOTES.md`](RELEASE_NOTES.md) states the manifest change for every
  release**, and carries a paste-able prompt that walks a session through applying
  an update — machinery, manifest, parse check, suites. That is the answer to this
  category: precise per-release instructions, not a migration tool.

To check the first bucket today, clone fresh to a scratch path and compare:

```bash
git clone https://github.com/SandmanCircles/4SYNC-ARCH /tmp/arch-latest
diff -ru --brief /tmp/arch-latest/hooks ./hooks
diff -ru --brief /tmp/arch-latest/scripts ./scripts
```

Differences in machinery files are updates you have not taken. Differences anywhere
else are yours and should be left alone. Re-run both suites after replacing anything.

### What build am I running?

```bash
python scripts/arch_build.py          # human-readable
python scripts/arch_build.py --json   # machine-readable
```

It prints a short id computed from the machinery files **on disk right now**, plus
what your instance was born with if genesis recorded it (`arch/BUILD.txt`).
Line-ending and final-newline differences are normalized away, so the id doesn't
change just because a file was checked out on a different platform.

**It is computed, never transcribed, and that is the design.** A stored version
string can lie — edit a machinery file, forget to bump it, and the instance reports
a build it isn't running. The birth record is the one thing worth storing, because
it's the only claim here that *can't* be recomputed: it's written once at genesis
and never updated, so comparing it to the live id tells you whether anyone has
changed the machinery since your instance was created.

**It does not tell you whether you are current**, and it won't pretend to. That
needs a comparison point, and the cheapest one is published: **fetch
<https://www.4sync.ai/llms.txt>** and compare its `build:` line to your id. One
request, no clone, no git. If they differ you are either behind or your machinery
has been edited locally — clone the repo and use the `diff` recipe above to find
out which. "You are up to date" computed from local data alone would be a lie with
a checkmark on it, which is why this asks you to fetch something.

`sync_version` is the *protocol* version and is a different thing; don't overload it.

**The two inventories differ on purpose.** `arch_build.py` hashes every machinery
file — **ask it rather than counting, since the list grows.** `scripts/check_sync.py`
— a maintainer-side tool that does not ship here — declares a shorter one. They
answer different questions: `check_sync` asks *has the silo drifted from the
product*, comparing two directories that both exist on the maintainer's machine, and
correctly omits `meter.py`, `actuals.py`, their suites and `wire_hooks.py`, because
the silo keeps no second copy of those and a file with one copy cannot drift. From
your position they are all machinery alike: copied in, never renamed, replaced
wholesale by an update. Treating those two questions as one is what left the gap
this section closes.

*(This paragraph published **15** and **seventeen** as live figures against a real
22, having been written when both were true and never revisited — the fourth
transcribed count in this project to go stale in prose no checker reaches. The
numbers are gone rather than corrected, which is the only fix that holds.)*

**The count went 15 → 17 at v1.0.8**, when `arch_build.py` and its suite turned out to
be missing from their own inventory. A release changing only that file moved no build
id, so you could have skipped it, computed an id that matched the release, and been
told you were current while missing the change. Adding them means **every id published
before v1.0.8 now recomputes to something else** — permanent, not a defect, and the
same one-time effect the `arch/VERSION` move had at v1.0.5. Compare against the current
release; older ids were verified when they shipped and cannot be re-derived by code of
a later generation.

## Getting help — `SUPPORT.md`

`SUPPORT.md` (moved to `arch/SUPPORT.md` by genesis) holds a prompt you paste
into a session running inside your own instance. It produces one read-only report
describing that instance — its layout, its measurements from `meter.py` / `actuals.py` /
`rotate.py`, its protocol health, and which machinery build it is running per
`arch_build.py` above.

**Run it for yourself first.** It is a health check assembled from tooling already in
this repo that almost nobody thinks to run in one pass, and it ends by asking your own
session what it would fix first. That answer is usually worth the five minutes whether
or not anyone else ever sees it.

If you want a second opinion, mail the report to **arch@4sync.ai**. **Sending is free** —
it costs nothing and commits you to nothing. What comes back depends on what is in the
report; we aim to reply within 24 hours, and nothing is billed unless you agree to it
first.

The prompt changes nothing, reports shape and measurements rather than content — no
source, no credentials, no customer names — and ends by showing you the finished report
and telling you nothing has been sent. Read it before you mail it; it describes your
instance.

## Provenance

This pattern was extracted from a working system, not designed for release. It ran
4 SHIELD's own multi-agent operation before it was packaged — five declared agent roles
plus interactive sessions, on one project. Its disciplines — sentinels, freshness
checks, mount distrust, the STATUS/journal split — each exist because a real failure
taught the lesson.

What is dated rather than asserted: this repo's own instance has run the protocol since
its genesis on 2026-07-20, and a second project adopted it on 2026-08-03. For numbers,
run them yourself — `scripts/meter.py --dir . --json` reports what boot costs on your
stack, and `scripts/actuals.py` reports what your sessions actually cost, read out of
Claude Code's own transcripts rather than ours.

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
