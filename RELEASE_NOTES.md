# 4SYNC ARCH — Release Notes

Newest first. Each entry says what to replace, and — the part no file copy can do for you —
**exactly what to change in your manifest**, because that one file is half product shape and
half yours.

Check what you are running with `python scripts/arch_build.py` inside your instance. The
current published release is in [`llms.txt`](https://www.4sync.ai/llms.txt).

---

## How to apply any update

**Commit first.** Your own git history is the undo, and it is the only safety net in this
process. Then open a session in your instance and paste this, filling in the two paths:

```
I'm updating 4SYNC ARCH. A fresh upstream clone is at <PATH-TO-CLONE>; my instance
is this folder.

1. Read RELEASE_NOTES.md in the clone. Work through every release BETWEEN my current
   version and the one I'm moving to, oldest first — my version is in
   archive/ARCH_BUILD.txt if genesis recorded it, or run scripts/arch_build.py.
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
