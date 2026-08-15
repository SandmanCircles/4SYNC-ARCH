# 4SYNC ARCH — Release Notes

Newest first. Each entry says what to replace, and — the part no file copy can do for you —
**exactly what to change in your manifest**, because that one file is half product shape and
half yours.

Check what you are running with `python scripts/arch_build.py` inside your instance. The
current published release is in [`llms.txt`](https://www.4sync.ai/llms.txt).

---

## How to apply any update

**Commit first.** Your own git history is the undo, and it is the only safety net in this
process.

**From v1.1.0 onward, use the tool.** Clone the release you are moving to, then run the CLONE's
updater against your instance — yours is older and does not know about the release it is applying:

```
python <PATH-TO-CLONE>/scripts/arch_update.py --from <PATH-TO-CLONE> --dir . --expect <build-id>
```

Dry run by default; `--apply` writes. It touches only the machinery inventory and refuses
everything outside it, so your `config/` stack, ledger and task documents are not reachable by it.

**It also prints what copying did not do** — the `**Manifest:` and `**By hand:` lines from every
release between your version and the one you are applying, oldest first, read from the CLONE's
copy of this file. **Copying machinery is not the whole update**, and those two leads are where
the rest of it lives. From v1.1.1 a release cannot be cut without a `**By hand:` line, so
"nothing" is stated rather than left out; releases before that predate the convention and the
tool names them instead of implying there is nothing to do.

**If your instance predates v1.1.0**, or you would rather do it yourself, open a session in your
instance and paste this, filling in the two paths:

```
I'm updating 4SYNC ARCH. A fresh upstream clone is at <PATH-TO-CLONE>; my instance
is this folder.

1. Read RELEASE_NOTES.md in the clone. Work through every release BETWEEN my current
   version and the one I'm moving to, oldest first — my version is in
   arch/BUILD.txt (archive/ARCH_BUILD.txt if my instance predates v1.0.5) if
   genesis recorded it, or run scripts/arch_build.py.
2. Replace the machinery files each release lists, byte for byte, from the clone.
   Do NOT touch my config/ stack, my ledger, my naming file, or my task documents —
   those are mine and no update modifies them.
3. Apply the manifest changes each release describes to MY manifest, as small anchored
   edits. My manifest carries my instance.root and my project's name, so it can never be
   copied over. Show me a diff before writing anything.
4. Re-read my manifest afterwards and confirm it still PARSES as YAML and still ends with
   its EOF sentinel. A stray ": " inside a plain scalar silently turns a line into a
   mapping and breaks the file. The guard catches this from the release AFTER v1.0.1
   onward; on v1.0.1 and earlier it does not, so run the check yourself regardless —
   the version you are moving FROM is the one whose guard is running during the edit.
5. Run both test suites and scripts/arch_build.py. Tell me the new build id and whether it
   matches the release I was aiming for.

Stop and ask me if any step is ambiguous rather than guessing.
```

**Take machinery wholesale or not at all.** Cherry-picking "the useful ones" leaves you on a
build that matches no release, which nobody — including support — can then reason about.

---

## v1.1.1

*The complete release: cross-project mail, an update that tells you what copying did not do, and
the last hardcoded names taken out of the two scripts that write your files.*

**Read this before you compare ids — the machinery list grew, and every id published before this
release now recomputes to something else.** `scripts/mail.py` and its suite join the inventory, so
it goes **20 → 22**. This is the **fifth** time (`arch/VERSION` at v1.0.5, `arch_build.py` at
v1.0.8, `test_wire_hooks.py` at v1.0.9, `arch_update.py` at v1.1.0), and it is said here *before*
the cut rather than explained afterwards. A build id is anchored to a tag for file CONTENT but to
the RUNNING CODE for the INVENTORY, so ids are only ever comparable within a generation. **Compare
against the current release and disregard older ids.** Your own instance is unaffected — you run
your generation's `arch_build.py` against your own tree.

**Machinery: replace all thirteen changed files** — `hooks/pre_tool_use.py`,
`hooks/session_start.py`, `hooks/test_pre_tool_use.py`, `scripts/arch_build.py`,
`scripts/arch_update.py`, `scripts/rotate.py`, `scripts/split_ledger.py`, the two new
`scripts/mail.py` + `scripts/test_mail.py`, and the four matching `test_*.py` files.

**Manifest: two additions, both optional.**

```yaml
mail:                      # cross-project mail. Omit it and nothing changes.
  name: YOURNAME           # what other instances address you as
  peers: [../other-instance]   # each peer declares its own name; a peer that has
                               # not opted in is reported as such, not as absent

close:
  tasks:
    prefix: MP             # `derived` takes the first three letters of instance.name
                           # (4SYNC -> tasks/SYN-083.md), so a human running two
                           # instances can hear whose row is whose. NOTHING IS RENAMED
                           # when you switch: MP-0NN.md resolves permanently and a
                           # mixed folder is the steady state, not a migration.
```

**By hand:** create `inbox/` and `outbox/` in your instance root if you want mail —
`arch_update.py` copies machinery only, and it is not machinery. `mail.py` cannot deliver into a
folder that does not exist, and **a missing folder looks exactly like no mail**, which is the one
silent failure this feature has. `git` does not track an empty directory, so add a `.gitkeep` to
each.

**What the update tool now tells you.** `arch_update.py` prints the `**Manifest:` and `**By hand:`
lines from every release between your version and the clone's, oldest first, read from **the
clone's** copy of this file — yours predates the release and cannot contain its note. Copying is
self-evidencing, because the build id either matches or does not; the steps that are *not* copying
are the ones nothing reports, and the live example is v1.0.5's `arch/VERSION` move, where a copy
alone leaves an instance matching no release at all. From this release a release cannot be cut
without a `**By hand:` line — "nothing" is written rather than left out, because an absent line and
a forgotten one are indistinguishable. **Notes before v1.1.1 predate the convention, and the tool
names them as such rather than implying there is nothing to do.**

**`rotate.py` and `split_ledger.py` now read the names your manifest declares.** Both carried
`MERGE_PLAN.md`, `ABBA.md` and `MP-0NN.md` as string literals while the manifest declared the
ledger in three separate keys — so an instance that renamed its ledger had a close operating on a
file no manifest names. Every default is unchanged, `split_ledger.py` imports the resolvers rather
than keeping a second copy, and `rotate.py` prints a `names:` line at every run so a mistyped key
is visible instead of silently falling back.

**Also:** the manifest cap now excludes the `bootstrap:` block genesis deletes, so a fresh clone is
no longer measured against instructions it discards on first use; `mail.py` finds a peer's manifest
by discovery, because genesis renames it and its name is unknowable from outside; and every shipped
script reconfigures its console to UTF-8, after a `pre_tool_use.py` refusal reached a user through
a Windows permission prompt with a replacement character in it — where the message *is* the
mechanism.

---

## v1.1.0

*A minor bump rather than v1.0.10, deliberately. This release adds a tool rather than fixing a
defect, which is what the middle number is for — and `1.0.10` would have been the first two-digit
component this project has ever produced. `check_sync` sorts semver numerically on purpose
(a lexical sort puts `v1.0.10` before `v1.0.9`), but that is one parser being careful, and the
release you least want mis-sorted is the tenth.*

**Machinery: replace all six changed files** — `scripts/arch_build.py`, `scripts/arch_update.py`,
`scripts/rotate.py`, and the three matching `test_*.py` files.

**Manifest: one optional addition.** Under `close.snapshot`, you may declare where an
over-threshold field goes:

```yaml
  snapshot:
    file: config/STATUS.yaml
    overflow_to: [FINDINGS.md, config/KERNEL.yaml, tasks/closed/]
```

Undeclared is fine and changes nothing — you get generic advice instead of your own destinations.
**These are OUR paths; yours are yours.** Nothing guesses a destination for you, on purpose: routing
your content somewhere you never declared is the failure this exists to prevent.

**By hand: nothing.**

---

**There is now a tool that applies a release for you, and this is the last update you have to do
by hand.**

```
python <PATH-TO-CLONE>/scripts/arch_update.py --from <PATH-TO-CLONE> --dir . --expect <build-id>
```

**Run the CLONE's copy, not your own** — yours predates this release and does not have the tool.
It is a dry run by default; add `--apply` to write. It copies only the machinery inventory and
**refuses to write anything outside it**, so your `config/` stack, your ledger and your task
documents cannot be touched by it. It verifies the clone against `--expect` *before* writing
anything, and recomputes your build id afterwards to prove the update landed.

It has been verified end to end against a real v1.0.8 tree. It has not yet been run by anyone
outside this project — you are its first outside user, and the dry run is there for that reason.

---

**Read this before you compare ids — the machinery list grew again, and every id published before
this release now recomputes to something else.** `scripts/arch_update.py` and its suite join the
inventory, so it goes **18 → 20**. This is the **fourth** time (`arch/VERSION` at v1.0.5,
`arch_build.py` at v1.0.8, `test_wire_hooks.py` at v1.0.9), which retires the word "incident" for
it: a build id is anchored to a tag for file CONTENT but to the RUNNING CODE for the INVENTORY, so
ids are only ever comparable within a generation. **Compare against the current release and
disregard older ids.** Your own instance is unaffected — you run your generation's `arch_build.py`
against your own tree.

**`rotate.py` stopped telling you to "cut it".** When a boot file went over its threshold, the
report said *"Cut it"* and named no destination — so trimming collapsed into deleting, and did,
losing content that existed nowhere else. It now says **TRIM IT BY MOVING, NOT DELETING** and names
your declared destinations if you have any.

---

## v1.0.9

*Written ahead of the cut on purpose — `release.py` refuses a cut without it, and the inventory
change below is the kind that must be stated before it ships rather than discovered afterwards.
Nothing is tagged or published until someone runs the cut deliberately.*

*(The heading is bare `## v1.0.9` because the gate matches it by EXACT equality. It first read
`## v1.0.9 — PREPARED, NOT CUT`, which was honest and refused the cut — the status marker belongs
in the body, not in the string a checker keys on.)*

**Machinery: replace all twelve changed files** — `hooks/pre_tool_use.py`,
`hooks/session_start.py`, `scripts/arch_build.py`, `scripts/meter.py`, `scripts/rotate.py`,
`scripts/wire_hooks.py`, and the six matching `test_*.py` files.

**Manifest: nothing to change.**

**Read this before you compare ids — the machinery list grew again, and every id published before
this release now recomputes to something else.** `scripts/test_wire_hooks.py` was missing from
`MACHINERY`, so the inventory goes 17 → 18. **v1.0.8's note said this was "the second and, we
expect, last time." That expectation was wrong, and this is the third.** It is the same one-time
effect as the `arch/VERSION` move at v1.0.5 and the `arch_build.py` addition at v1.0.8: a build id
is anchored to a tag for file CONTENT but to the RUNNING CODE for the INVENTORY, so ids are only
ever comparable within a generation. **Compare against the current release and disregard older
ids.** Your own instance is unaffected — you run your generation's `arch_build.py` against your
own tree.

**What is different this time is that the class is closed, not just the instance.**
`test_machinery_lists_every_paired_suite` now fails when a machinery entry's suite exists on disk
but is absent from the inventory. Both previous occurrences were found by a human noticing a
number was wrong; a fourth would fail the suite instead.

**The bug that most affects you is `g5`, and it has been inert on Linux and macOS since it
shipped.** The manifest guard's date-origin check opened a lowercased path. On a case-insensitive
filesystem that succeeds; on a case-sensitive one it raises whenever the path has a capital in it —
and the shipped manifest is `4SYNC.yaml`. So every date refusal degraded to the hedged "it may
pre-date your edit" message instead of telling you who introduced the date. The guard still
blocked; only the attribution was lost. Fixed.

**Also in this release:**

- **A crashing guard is now logged instead of silently skipped.** A guard that raised was passed
  over with no log line, so a check that never ran was indistinguishable from a check that passed.
  The tool call still proceeds — that part was deliberate — but it is no longer silent.
- **The manifest is read at any indent.** Six lookups anchored on exactly two spaces. If your
  manifest is indented with four spaces or tabs — any YAML formatter, most editor defaults — those
  lookups silently returned their built-in defaults: a declared `journal.max_bytes` of 16384 was
  governed by 12288, and a bulletin declaring `check_at_boot: true` read as no bulletin. **If you
  ever reindented your manifest, this is the release that starts honouring it.**
- **A relative `file_path` now resolves against the session's working directory**, not the hook
  process's. Payloads seen in practice are absolute, so this is hardening rather than a fix.
- **The suite passes on macOS.** Three `TestSettingsRoot` tests failed on every macOS box and
  nowhere else: `git rev-parse --show-toplevel` returns the canonical path, `tempfile.mkdtemp()`
  returns `/var/folders/...`, and `/var` is a symlink to `/private/var`. **The shipped code was
  always right** — the test fixture compared against a path macOS never returns. If you run macOS
  and wondered why three tests failed on a clean clone, this is why, and it was never your install.
- **CI**: the suite now runs on Linux, macOS and Windows across three Python versions, with
  PyYAML-absent as the default configuration, plus a leg that deliberately symlinks the temp
  directory so the macOS failure above cannot come back unnoticed. The g5 bug shipped through two
  releases behind a suite that already contained the failing tests, on a machine that could not run
  them.

---

## v1.0.8

**Machinery: replace `scripts/rotate.py` and `scripts/arch_build.py`, and their two test
files.**

**Manifest: nothing to change.**

**Read this before you compare ids — the machinery list grew, and every id published before
this release now recomputes to something else.** `arch_build.py` and its own suite were
missing from `MACHINERY`, from v1.0.0 through v1.0.7, for no recorded reason. Adding them takes
the inventory 15 → 17. That is permanent, it is not a defect, and it is not repairable: a build
id is anchored to a tag for file CONTENT but to the RUNNING CODE for the INVENTORY, so ids are
only ever comparable within a generation. This is the second and, we expect, last time —
the `arch/VERSION` move did the same thing at v1.0.5. **Compare against the current release
and disregard older ids.**

**Why it mattered, because it is the reason to take this one:** a release that changed only
`arch_build.py` moved no build id. So you could have skipped that file, computed an id that
**matched the release**, and been told you were current while missing the change. An identity
that omits a file an update replaces confirms a currency you do not have — in the script whose
entire job is preventing exactly that. From v1.0.8 the id covers every file an update replaces,
which is the first release where *"take machinery wholesale or not at all"* is actually backed
by the number.

**New: your ledger's status legend now defines the tally's vocabulary.** If your ledger uses a
status mark beyond the shipped five, **declare it in your own status legend and `rotate.py`
will count it.** Both legend forms are read — the prose `**Status:** ✅ completed · …` line and
a `| Symbol | Status | Meaning |` table — so nothing in your ledger has to change shape. An
**undeclared** mark still reports and blocks the Tally rewrite, unchanged and deliberate: a
total that silently omits rows is worse than a stale one. **If you added no marks, nothing
changes** — your Tally line is byte-identical to every previous run.

Found on a real instance: two rows in a sixth status made every one of them `unknown`, which
blocked the rewrite, which left a hand-maintained count stranded with the mechanism that
repairs it switched off.

**What changed:**

- **`scripts/rotate.py` — status marks are read from the ledger's own legend.** The five
  shipped marks remain the base and the legend only adds; a legend entry reusing a base symbol
  or name is dropped rather than merged, so a second spelling of one status cannot
  double-count. A legend needs at least three entries to be read as one, so a stray
  symbol-headed table row in a journal block cannot mint a status.
- **`scripts/arch_build.py` — the report now names the update instructions.** It points at
  `RELEASE_NOTES.md` and at the published `llms.txt`, at the one moment you are already asking
  what you are running. It still refuses to tell you whether you are *current*: that needs an
  upstream comparison point this script does not have and will not invent.
- **`scripts/arch_build.py` — `MACHINERY` 15 → 17**, as above.
- **`README.md` gains *"The byte cap is yours."*** `integrity.manifest_rules.max_bytes` is
  your number: the guard reads it from your manifest, and **no release will ever ship you a
  value or ask you to change one.** The section carries the discipline that makes a cap worth
  having — raise it only when a real declaration will not fit, trim prose otherwise — and the
  distinction to use when bytes are scarce: a line a session *executes* is load-bearing, a line
  that only *documents* is the first to drop.
- **`RELEASE_NOTES.md` — v1.0.4's optional manifest line gained one addendum:** if it does not
  fit, leave it out, and do not read the refusal as being behind on a release.

**Suites:** `rotate` 176 → 188, `arch_build` 35 → 40. 372 under `scripts/`, 157 under `hooks/`,
green with and without PyYAML.

---

## v1.0.7

**Machinery: replace `hooks/pre_tool_use.py` and `scripts/wire_hooks.py`, and their two
test files.** Unlike v1.0.6, a hook that actually runs did change this time.

**Manifest: nothing to change.**

**Check this one thing — run it, don't estimate it.** From inside your instance:

```bash
git rev-parse --show-toplevel
```

If that prints your instance folder, or fails because you are not in a git repository,
this release changes nothing about your wiring and there is nothing to do.

If it prints a folder **above** your instance — ARCH lives in a subfolder of a larger
repository — then your settings were written to `<instance>/.claude/settings.local.json`,
which Claude Code never reads. Claude Code resolves settings to the root of the git
repository. Re-run `python scripts/wire_hooks.py --write` (it now targets the right
place and tells you which root it chose), then **delete the old
`<instance>/.claude/settings.local.json`.** Deleting it is the part worth doing rather
than skipping: a settings file sitting where you expect one is the reason this went
unnoticed in the first place.

**What changed:**

- **`wire_hooks.py` resolves where Claude Code actually reads settings, instead of
  assuming the instance root.** It uses `git rev-parse --show-toplevel`, prints the root
  it chose and why, and warns when that is outside the instance. Two cases keep the file
  with the instance: you are not in a git repository, or the repository root is your home
  directory. A nested repository that is *its own* repository — the shape of a vendored
  or embedded checkout — is its own settings root and is left alone.
  - Previously it always wrote to the instance root and reported success. For a nested
    layout that produced a settings file nothing loads: no guards, no boot receipt, and
    a file on disk saying otherwise.
- **`wire_hooks.py` sets `ARCH_MANIFEST` when your manifest has been renamed.** Genesis
  renames the manifest per project and merges the variable into `.claude/settings.json`;
  if the file Claude Code loads is a different one, that merge never reaches it. The
  script now finds the manifest by content rather than by name and fills the blank,
  without overwriting a value you already set.
- **The session-debt recorder no longer treats any folder with a `config/` directory as
  an ARCH instance.** It now requires a loader-stack KERNEL inside that directory.
  Nothing to do — this is a behaviour change with no migration.
  - What it fixes: Laravel, Symfony, Drupal and others put `config/` at the project root.
    With the hooks wired at user level, as this project recommends, every session working
    in such a project — including sessions with no connection to ARCH — wrote a
    `.session_debt.tsv` at that project's root. The file carries session ids and absolute
    local paths, and nothing in those repositories gitignores it.
  - Write fencing is deliberately unchanged and still treats a bare `config/` as an
    instance. Fencing and recording want different answers to "is this an instance?":
    over-identifying costs a declined write you can approve, while under the recorder it
    costs a file in somebody else's repository.

---

## v1.0.6

**Machinery: replace `scripts/wire_hooks.py` and `hooks/claude-settings.example.json`, and
add the new `scripts/test_wire_hooks.py`.** Nothing under `hooks/` that actually runs
changed. No `VERSION` move this time — if you took v1.0.5, `arch/VERSION` is already where
it belongs.

**Manifest: nothing to change.**

**Check this one thing if you wired from the example file rather than the README:** the
example was missing its `SessionStart` block entirely, so a settings file copied from it
wires the guards and **not** the boot receipt. Open your `settings.json` and look for
`SessionStart`. If it is absent, you have been running without the receipt — no boot
verification, no session-debt reading at boot, and since v1.0.5 no boot-growth report either.
The README always carried the block; the example never did, and the two disagreed.

**What changed:**

- **`wire_hooks.py` now prints the `SessionStart` block for you, with your real paths
  already filled in.** It still does not write it, and that is deliberate rather than
  unfinished: this script writes **project-level** `.claude/settings.local.json`, while the
  receipt belongs at **user level**. The sessions that skip boot are the ones launched
  *outside* your instance, and those never read project settings at all — so wiring the
  receipt at project level would put it exactly where it is least needed while letting you
  believe you were covered.
  - What it removes is the step that actually goes wrong: hand-substituting an interpreter
    path and a hook path into a template full of `/full/path/to/python`. The script has
    already verified your interpreter by executing it, so it prints both paths filled in,
    ready to paste.
  - `~/.claude/settings.json` stays yours. Its contents run on **every tool call in every
    project on the machine**, which is too much reach for a script to claim on your behalf.
- **The example settings file gained the `SessionStart` block** and now says **seven**
  structural guards rather than six — the seventh, STATUS-stale-write, has shipped since
  v1.0.1 and this file never mentioned it.
- **`scripts/test_wire_hooks.py` is new — 19 tests, the first suite this script has ever
  had.** It was the only script in the product without one, and it was also the one shipping
  half its wiring. Those two facts arriving together is a place to look, not a coincidence.
- **Spelling:** "licence" → "license" in `README.md` and one `arch_build.py` string.

> **One gap recorded rather than quietly shipped.** `scripts/test_wire_hooks.py` ships, but
> it is **not** in the hashed machinery inventory — every other script is paired with its
> test there; this one was the exception only because no test existed until now. So if that
> file goes missing or gets edited in your instance, **the build id will not notice.** Adding
> it is a one-line change with an outsized side effect — changing the inventory retroactively
> changes what every past tag computes — so it is deliberately held for a release that is
> already moving the inventory. Until then: the build id tells you your *running* machinery
> matches a release. It does not vouch for that one test file.

---

## v1.0.5

**Machinery: replace `hooks/session_start.py`, `hooks/test_session_start.py`,
`scripts/rotate.py` and `scripts/test_rotate.py` — and MOVE your `VERSION` file from your
instance root to `arch/VERSION`.** The move is the only step in this release that needs your
hands. Everything else is additive.

> **Move it, do not copy it.** A `VERSION` left behind at the root while machinery updates is
> the one failure this layout can produce, and it is quiet: `arch_build.py` folds
> `arch/VERSION: MISSING` into the id, so you get a build that matches no release and a
> currency check that cannot tell you why. `stray_root_version()` now names that exact case
> if you hit it. Why the file moved at all: the rel key is hashed alongside the digest, so a
> file at a different path hashes under a different key — one fixed location is what lets any
> instance match a published id at all.

**Manifest: nothing to change.** No new keys, no edits.

**What changed:**

- **The boot receipt now tells you which boot file grew, and it tells you on arrival.**
  `meter.py --log` has been appending per-file boot sizes to `metrics/roc_series.jsonl` at
  every close since it shipped, and nothing had ever read that series at boot. Now the
  receipt compares against the last logged close and names the files that grew. **The point
  is the timing, not the measurement:** on the instance this was built from, a *close-time*
  size report fired at every close for five days, named the right file and prescribed the
  right fix, while that file grew 72% — because a warning delivered to a session that is
  trying to finish loses to finishing. The same sentence at boot reaches a session with the
  whole session still ahead of it.
  - Two gates, both must trip: **≥1,024 B and ≥10%**. A small file that doubled is not news
    and a large file drifting by a line is not either. `ARCH_BOOT_GROWTH_PCT` overrides.
  - **Silent if you have never run `meter.py --log`** — no series, no baseline, no alarm.
  - A **scanned** bulletin is excluded from the comparison. It has no comparable baseline:
    the meter logs its scan estimate while the receipt sees the whole file, and comparing
    the two reported a 1,135% jump on a file nobody had touched. That was found on the
    feature's first live run, and no dry run could have surfaced it.

- **`rotate.py` flags STATUS entries whose ledger references are all closed rows.** A closed
  task's outcome is still *true*, so it survives every staleness check ever written and sits
  in your boot path forever. This asks the other question. It scans **every** top-level list
  field rather than one field by name, so a `blockers:` entry whose task closed is caught
  too.
  - Only `MP#<n>`-style references count. A bare `#22` is prose, not a citation.
  - An entry citing **nothing** is never flagged, and an entry citing a mix of open and
    closed rows is left alone. Silence is not evidence; this check would rather miss than
    accuse. Reported, never blocking.

- **The STATUS template now carries the test that governs it:** *if the fact would still be
  true a year from now, it is not state.* It belongs in `FINDINGS.md` (with a `Trigger:` and
  an `Exit:`), in your KERNEL invariants, or in history. Written into the `in_flight:` block
  itself so you meet it before your first entry rather than after the file has grown.

- **Tests 160 → 176 (`rotate`) and 37 → 51 (`session_start`)**, green with and without
  PyYAML.

> **One consequence recorded rather than quietly shipped.** Moving `VERSION` changed the
> machinery *inventory*, and build ids are anchored to a tag for file CONTENT but to the
> running code for the INVENTORY. So every tag cut before this one now recomputes to
> something other than the id it published — `v1.0.4` was verified at release and no longer
> reproduces. Nothing about those releases changed; the question being asked of them did.
> **The general form, which outlives this instance: a published pair can only be re-verified
> by code of its own generation.** If you are updating from any earlier release, verify
> against *this* one and disregard the older ids.

---

## v1.0.4

**Machinery: replace `hooks/pre_tool_use.py` and `hooks/test_pre_tool_use.py`, plus root
`VERSION`.** Nothing under `scripts/` changed. This is the guard hook, so it is the file
every session on your machine loads — take it wholesale, not in part.

**Manifest: one optional line, and read the caveat under it.** Add `max_age: 14d` to your
`session_debt:` block, and if you want the wording, update `at_close: clear_own_row` to say
it clears every debt file under the instance root.

> **The manifest line is DOCUMENTATION, not configuration — and you should know that before
> you edit it expecting something to happen.** The hook reads the window from its own
> constant and from `ARCH_DEBT_MAX_AGE_DAYS`; it does not parse the manifest for this. So
> writing `max_age: 30d` changes nothing. To actually move the window, set the environment
> variable. This is the same shape as a known open issue where the manifest declares the
> ledger filename three ways and two scripts ignore all three, and it is recorded here
> rather than quietly shipped.
>
> **If it does not fit, leave it out.** An instance on a tight `max_bytes` should skip this
> line rather than spend its last bytes on one that is read by nothing — and should not read
> the refusal as being behind on a release. The cap is your number, never ours; see the
> README's *"The byte cap is yours."*

**What changed:**

- **Session-debt rows now age out.** Rows whose `last_activity` is older than fourteen days
  drop themselves the next time the hook writes the file. Set `ARCH_DEBT_MAX_AGE_DAYS` to
  change the window, or `0` to disable ageing entirely.
- **Why this needed fixing at all:** nothing had ever removed a row from a debt file. Not
  the hook, not close, not any script. On the instance where this was found the file had
  reached thirteen rows going back three weeks, none of them actionable — whether that work
  landed is a question git already answers. **A boot warning nobody can act on is one a
  reader learns to scroll past, which is the failure the debt tracker's own documentation
  names.** If your file has grown, that is the same defect and this release stops it.
- **`clear_own_row` at close now means every debt file under the instance root.** If you
  keep a nested repo inside your instance, that repo is itself an instance to the hook —
  writes there record against *its* debt file, while close was only ever clearing the
  booted one. Sessions that touched both left a row behind every time.
- **Two deliberate non-behaviors, both of which would be bugs if reversed.** A row with an
  unparseable timestamp is **kept**, never dropped — dropping on a parse failure would
  silently delete the thing the file exists to preserve. And a session's **own** row is
  never aged, because the recorder only fires on file writes, so a long-running session
  carries a stale `last_activity` and would otherwise delete its own row mid-work.
- **Tests 94 → 99** in the hook suite, green with and without PyYAML.

**Nothing here is urgent if your debt file is short.** The age-out is housekeeping; the
guards are unchanged in what they block.

---

## v1.0.3

**Machinery: replace root `VERSION` only.** Nothing under `hooks/` or `scripts/` changed in this
release — not one byte. If you are on v1.0.2 and you only care about machinery, you are already
current and can stop reading here.

**Manifest: no change required for an existing instance.** The only edit is inside the
`bootstrap:` block, which genesis deletes when it runs — it archives one more packaging file, and
a stale comment about the guard count was corrected. New adoptions only.

**One NEW file, and it is the point of this release: `ADOPTING.md`.** Copy it in if you want it.
It is documentation, not machinery — nothing depends on it and nothing breaks if you skip it.
It covers the three questions the README does not: trialing ARCH in a folder you intend to
delete, adopting a project that already exists (including reconstructing its history without
paying for it at every boot), and removing ARCH again if you decide against it.

**Why this is a release at all, since no machinery moved.** A docs-only change is otherwise
**invisible to the currency check** — the build id is computed from machinery, so an instance
sitting on v1.0.2 would compute a matching id, be told it was current, and be missing a shipped
document. Bumping `VERSION` is what makes the release visible, and it works precisely because
`VERSION` is itself part of the hashed inventory: two trees that differ only in `VERSION` are
different builds. **If you take nothing else from this release, take `VERSION` — otherwise your
instance will keep reporting v1.0.2 forever.**

**What changed:**

- **`ADOPTING.md` is new** — see above. The load-bearing distinction in it, for anyone about to
  set up a trial: *disposable* and *unreachable* are not the same property. A trial folder should
  be one you delete afterwards, not a scratchpad a second session cannot open — because state
  surviving from one session to the next is the only thing a trial can actually demonstrate, and
  a folder nothing else can reach cannot demonstrate it.
- **`TIPS.md` rewritten**, with a comparison of what a session on your machine can do versus one
  in a cloud container: git, hooks, the debt row, and how work gets handed back. Same content,
  written to be read rather than scanned.
- **`README.md`** points at `ADOPTING.md`.
- **The manifest's genesis packaging step** archives `ADOPTING.md` alongside the rest, and its
  guard-count comment now reads seven rather than six — `g7` shipped in v1.0.2 and the comment
  was not updated with it.

---

## v1.0.2

**Machinery:** replace all files under `hooks/` and `scripts/`, plus root `VERSION`.

**Manifest: no change required for an existing instance.** The only edit is inside the
`bootstrap:` block, which genesis deletes when it runs — it archives two more packaging files.
New adoptions only.

**One NEW file, and it is optional: `LEDGER_GUIDE.md`.** Copy it in if you want it. It is the
reasoning behind the ledger's rules, and it pairs with a *template* change described below that
**this update does not make to your files** — so on its own it is reference material, not a
dependency. Nothing breaks if you skip it.

**Read this part carefully, because it is the first release where the template and your
instance genuinely diverge.** `MERGE_PLAN.md`, `config/KERNEL.yaml` and `config/REFERENCE.yaml`
were all restructured in this release — **for new instances.** Yours are *your files*, in the
instance bucket, and **no update touches them, including this one.** If you want the same
benefit you restructure your own copies deliberately, at a time of your choosing; the guide and
the shipped template show you the shape. Doing nothing is a valid choice and costs you only the
startup tokens you are already paying.

**What changed:**

- **`g7` — a new guard.** A whole-file `Write` to `config/STATUS.yaml` now ASKS before it lands
  and names what it would remove; anchored `Edit`/`MultiEdit` never reach it. STATUS declares
  overwrite mode, and a session that reads "overwrite" at the *file* level rewrites the whole
  snapshot from its session-start copy, silently reverting anything another session wrote in
  between. It is the only confirmed way to lose data in ARCH. It asks rather than blocks because
  whether a whole-file write is deliberate or a stale-base revert depends on what its author had
  read, which no guard can see.
- **`g5` tells "PyYAML is absent" apart from "your YAML is broken."** One `except` covered both,
  so a manifest write that broke the YAML was allowed through silently. The refusal now names the
  line and column.
- **`g5`'s date refusal says whose date it is.** One dated comment write-locks the manifest for
  everybody afterwards, so the author of a refused write is usually not the author of the
  offending line. It now distinguishes a date your write introduces from one already sitting in
  the file, and names the line either way.
- **`rotate.py` checks the manifest AT REST** — parse and `declaration_only` — because a guard
  on the door says nothing about what is already in the room. It also reports the ledger's
  prose share against a stated threshold rather than as a bare number, and alerts when boot cost
  grows more than 15% since the last logged close.
- **`TIPS.md`** — adopter-facing day-to-day guidance. Not machinery, not in the boot path.

**The template's startup cost dropped ~20%** (12,129 → 9,655 tokens on a fresh instance) by
moving read-once teaching text out of the files read at every boot. Again: that lands for new
instances, not for yours.

---

## v1.0.1

**Machinery:** replace all files under `hooks/` and `scripts/`, plus the new root `VERSION`.
New in this release: `scripts/arch_build.py` and `scripts/test_arch_build.py`.

**Manifest: no change required for an existing instance.** The only manifest edit in this
release adds a genesis step (`arch_build.py --write-birth-record`), and it lives inside the
`bootstrap:` block — which genesis deletes when it runs. If your instance already exists, that
block is gone and there is nothing to apply. It affects new adoptions only.

*A consequence worth knowing rather than discovering:* your instance therefore has no
`archive/ARCH_BUILD.txt`, and `arch_build.py` will say so. That is correct and not an error —
the birth record can only be written once, at genesis, and yours already happened. Do not
hand-write one; a fabricated birth record is worse than an absent one, because it asserts a
history that did not occur.

**What changed:**

- `scripts/arch_build.py` — answers "what build am I running?" from the machinery bytes on
  disk, with no network call. Reports version first, digest underneath as a tamper check.
- `config/STATUS.yaml` write mode restated as **overwrite the FACT, never the FILE**. This is
  the single confirmed way to lose data in ARCH: a session that reads "overwrite" at the file
  level and rewrites the whole snapshot from its session-start copy silently reverts anything
  another session wrote in between. Same correction in the README and `CLAUDE.md`.
- Session-debt file documented as **evidence, not protection** — it takes no lock and prevents
  nothing; its value is a row proving two sessions were live at once.
- `SUPPORT.md` now asks for the build id rather than a hand-rolled clone-and-diff.
- American spelling throughout (`licence` → `license`, `authorisation` → `authorization`,
  `artefact` → `artifact`, `behaviour` → `behavior`).

**Known issue, not fixed in this release:** the manifest guard (`g5`) treats a YAML *parse
failure* the same as *PyYAML not installed* and falls back to a regex scan, so a manifest write
that breaks the YAML is allowed through. Step 4 of the update prompt above exists because of
this. Run the parse check yourself. **Fixed in the next release** — g5 now tells
"PyYAML is absent" apart from "your YAML is broken" and refuses the second, naming the
line and column.

---

## v1.0.0

The first tagged release. It labels the state of the public repo at the moment version
numbering began — **not** the day the repo went public, since fixes landed after that and no
single release state exists from that day.

If you cloned between the repo going public and this tag, you hold an untagged intermediate
that matches no release. `arch_build.py` will report an id matching nothing published; that is
a true fact rather than a failure. Updating to a tagged release resolves it.
