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
   mapping and breaks the file; the guard does not catch this yet.
5. Run both test suites and scripts/arch_build.py. Tell me the new build id and whether it
   matches the release I was aiming for.

Stop and ask me if any step is ambiguous rather than guessing.
```

**Take machinery wholesale or not at all.** Cherry-picking "the useful ones" leaves you on a
build that matches no release, which nobody — including support — can then reason about.

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
this. Run the parse check yourself until it is fixed.

---

## v1.0.0

The first tagged release. It labels the state of the public repo at the moment version
numbering began — **not** the day the repo went public, since fixes landed after that and no
single release state exists from that day.

If you cloned between the repo going public and this tag, you hold an untagged intermediate
that matches no release. `arch_build.py` will report an id matching nothing published; that is
a true fact rather than a failure. Updating to a tagged release resolves it.
