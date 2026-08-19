#!/usr/bin/env python3
"""
arch_build.py — answer "what ARCH build am I running?" without a network call.

WHY THIS EXISTS
  ARCH is copied, not installed, and genesis severs the link on purpose: the
  packaging moves out of root so `git init` won't mark the adopter's repo
  FSL-licensed, the config stack gets a project prefix, and the manifest is
  renamed. So there is no upstream remote, no shared history, and no path-based
  diff that finds anything. The first real adopter asked the obvious question —
  "as you update this thing, how am I going to know whether you've updated it?"
  — and the honest answer was that nothing recorded an instance's build at all.

WHAT IT IS (and is NOT)
  It COMPUTES the build identity from the machinery files on disk, right now.
  It is NOT a version string somebody remembers to bump, and that is deliberate.

  A stored stamp can lie: edit a machinery file, forget the stamp, and the
  instance now reports a build it is not running. This project has been burned by
  exactly that shape three times — the hand-maintained ledger tally (MP#39), the
  hand-copied STATUS figures (MP#42), and a published boot stat that went stale
  on every ledger write (MP#5). The rule those produced is that a fact which can
  be computed must never be transcribed. A digest over the actual bytes cannot
  drift from what is actually there, because it *is* what is actually there.

  The birth record is the one thing genuinely worth storing, because it cannot be
  recomputed: what this instance was born with. Written once at genesis, never
  updated. Comparing it to the live digest answers a different and useful
  question — has anyone changed the machinery since this instance was created?

WHAT IT DOES NOT ANSWER
  Whether you are CURRENT. That needs a comparison point this script does not
  have and will not invent — the upstream repo. Clone it and compare digests, or
  use the README's `diff -ru --brief` recipe. Reporting "you are up to date"
  from local data alone would be a lie with a checkmark on it.

MACHINERY vs. INSTANCE — the split this rests on
  Only machinery is hashed: generic, never renamed, never adopter-edited. The
  config stack, ledger, journal, naming file and task docs are the adopter's own
  asset and are deliberately absent — an "update" must never touch them, so they
  have no business in a build identity.

Usage:
  python scripts/arch_build.py                      # human-readable report
  python scripts/arch_build.py --json               # machine-readable
  python scripts/arch_build.py --dir <instance>     # another instance root
  python scripts/arch_build.py --write-birth-record # genesis writes it ONCE
"""

import argparse
import hashlib
import json
import os
import sys

# ── The adopter-facing machinery inventory ───────────────────────────────────
# Every shipped machinery file — all the code, plus VERSION. Every adopter gets all
# of them, so all of them are part of the build identity.
#
# NO COUNT IN THIS SENTENCE, deliberately. It read "All 15 shipped machinery files
# (14 code files plus VERSION)" from v1.0.0 until v1.0.9 — through the 15 → 17 move
# at v1.0.8, which updated the count twelve lines below and not this one. A
# transcribed count in the file whose own docstring says "a fact which can be
# computed must never be transcribed" is the joke writing itself. The one number
# that survives is pinned by a test, where changing it is a deliberate act.
#
# THIS LIST IS DELIBERATELY LONGER THAN check_sync.py's, AND THE DIFFERENCE IS
# NOT AN OVERSIGHT — the two answer different questions and both are correct:
#
#   check_sync.py (9 files, SILO-ONLY) asks "has the silo drifted from the
#     product?" It compares two directories that both exist on the maintainer's
#     machine. It omits meter.py, actuals.py, their suites and wire_hooks.py for
#     a sound reason: the silo carries no copy of those — it runs them out of the
#     nested product folder — and a file with exactly one copy cannot drift.
#
#   arch_build.py (SHIPS TO ADOPTERS — count it from the list, do not read one
#     here; this line said 18 while the list held 22) asks "what am I running?" From an
#     adopter's position those files are machinery exactly like the other nine:
#     copied in, never renamed, replaced wholesale by an update. Omitting them
#     would produce a build identity that silently ignores files an update
#     changes. 15 → 17 at v1.0.8: this script and its suite were missing from
#     their own list, which is the same error one level in. Then 17 → 18 at
#     v1.0.9 (MP#77), for `scripts/test_wire_hooks.py` — the same omission a
#     release later, found the same way, by `release.py status` reporting fewer
#     changed machinery files than had actually changed. See the notes beside
#     them below, and `test_machinery_lists_every_paired_suite`, which closes the
#     class so there is no third.
#
# Treating those two questions as one is what left the gap. Keep both lists, keep
# them honest, and keep this comment next to whichever one you edit.
MACHINERY = [
    # The release number is part of the build, not metadata about it: two trees
    # that differ only in VERSION are different builds, and hashing it means
    # "v1.0.1, unmodified" is a single check rather than two.
    #
    # IT LIVES IN arch/ AND SHIPS THAT WAY, so this path is identical in the
    # product repo and in every adopter's instance. That is the whole point: the
    # rel key is hashed alongside the digest (see build_id), so a file that
    # genesis MOVED would hash under a different key than the same file upstream
    # and no adopter could ever match a published id. A path that never moves
    # needs no dual-location lookup and no canonical-key indirection.
    "arch/VERSION",
    "hooks/pre_tool_use.py",
    "hooks/test_pre_tool_use.py",
    "hooks/session_start.py",
    "hooks/test_session_start.py",
    "hooks/claude-settings.example.json",
    # THIS FILE IS IN ITS OWN INVENTORY, added at v1.0.8 (MP#69). It was absent
    # from v1.0.0 through v1.0.7 for no recorded reason — the comment above spends
    # eighteen lines reconciling this list against check_sync.py's and never
    # mentions the omission. By its own stated test it always belonged: copied in,
    # never renamed, replaced wholesale by an update.
    #
    # THE FAILURE IT ALLOWED, caught while cutting v1.0.8. A release changing only
    # this file moved no build id, so an adopter could skip it, compute an id that
    # MATCHED the release, and be told they were current while missing the change.
    # An identity that omits a file an update replaces confirms a currency nobody
    # has — which is the exact defect this script exists to prevent, in the script
    # that prevents it.
    #
    # Hashing itself is not circular: it digests bytes on disk, and it never reads
    # its own digest. The id is anchored to a tag for CONTENT but to the running
    # code for the INVENTORY, so ids are only ever comparable within a generation —
    # already on record from the v1.0.5 arch/ move, and this addition has the same
    # one-time effect on tags cut before it.
    "scripts/arch_build.py",
    "scripts/test_arch_build.py",
    "scripts/rotate.py",
    "scripts/test_rotate.py",
    "scripts/meter.py",
    "scripts/test_meter.py",
    "scripts/actuals.py",
    "scripts/test_actuals.py",
    "scripts/split_ledger.py",
    "scripts/test_split_ledger.py",
    # Why this file exists at all: scripts/debt.py's module docstring (SYN-087).
    "scripts/debt.py",
    "scripts/test_debt.py",
    "scripts/wire_hooks.py",
    # ADDED v1.0.9 (MP#77). The SAME omission as arch_build.py's own, one release
    # later and found the same way — `release.py status` reported 3 changed
    # machinery files against a real 4. Every other script here is paired with its
    # suite; this one was not, so a release changing only it moved no build id and
    # an adopter could skip the file, compute a MATCHING id, and be told they were
    # current.
    #
    # WHY MP#69 DID NOT CATCH IT: that row was about arch_build.py specifically and
    # fixed arch_build.py specifically, without auditing the rest of the list for
    # the same shape. And the gap did not exist when the list was written —
    # wire_hooks.py had been here for some time, but its suite was not created until
    # MP#65 (2026-08-10), and nothing adds a file here when a new suite is born. The
    # hole opened by ADDING A TEST, which is why no review of this list would have
    # found it. test_machinery_lists_every_paired_suite in test_arch_build.py now
    # fails when an entry's suite exists on disk but is absent here — the class,
    # rather than a third instance of it.
    "scripts/test_wire_hooks.py",
    # ADDED under MP#80 with its suite, in the same commit, because MP#69 and MP#77
    # were both the story of a script listed without one — and this pair was asserted
    # by test_arch_update.py before either line existed here, so the omission failed
    # a test rather than shipping.
    #
    # THIS MOVES THE INVENTORY 18 -> 20 AND RECOMPUTES EVERY PUBLISHED ID, for the
    # FOURTH time (arch/VERSION at v1.0.5, arch_build.py at v1.0.8, test_wire_hooks
    # at v1.0.9). That is now unambiguously a property of the design and not an
    # incident: a build id is anchored to a tag for file CONTENT but to the running
    # code for the INVENTORY, so ids are only ever comparable within a generation.
    # It must be stated in the release note BEFORE the cut. Adopters are unaffected —
    # each runs their own generation's arch_build.py against their own tree.
    "scripts/arch_update.py",
    "scripts/test_arch_update.py",
    # ADDED under MP#84 with its suite, same commit, same reason as the pair
    # above: MP#69 and MP#77 were both a script listed without one.
    # INVENTORY 20 -> 22, the FIFTH recompute of every published id. State it in
    # the release note BEFORE the cut, as v1.1.0 did.
    "scripts/mail.py",
    "scripts/test_mail.py",
]

# Where genesis records what the instance was born with. Under arch/ because
# that is where genesis already puts the packaging — the birth record belongs
# with the birth certificate, not in config/ (which is identity state, prefixed
# at genesis, and edited for the life of the project).
#
# The folder is arch/ and NOT archive/ because everything in it is live: the
# license that governs the code, the version the instance runs, the packaging an
# adopter still reads. "Archive" says dead, and a folder whose name misdescribes
# its contents at first contact teaches the reader to skip it. Names inside are
# bare — arch/README.md, not arch/ARCH_README.md — since the prefix existed only
# to disambiguate these files AT ROOT, and the folder now does that job.
BIRTH_RECORD = os.path.join("arch", "BUILD.txt")


def _norm(data):
    """Normalize cross-clone artifacts that are not meaningful content change.

    Same normalization check_sync.py applies, and for the same reason: two git
    checkouts can differ in line-ending policy (core.autocrlf) and final-newline
    handling without a human having changed anything. A build id that changed
    when a file was checked out on a different platform would report a difference
    nobody made, which is worse than useless — it trains the reader to ignore it.
    """
    return data.replace(b"\r\n", b"\n").rstrip()


def file_digests(root):
    """Return (digests, missing) for the machinery inventory under root."""
    digests, missing = {}, []
    for rel in MACHINERY:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(path):
            missing.append(rel)
            continue
        with open(path, "rb") as fh:
            digests[rel] = hashlib.sha256(_norm(fh.read())).hexdigest()
    return digests, missing


def build_id(digests, missing):
    """A short, stable id over the whole inventory.

    Missing files participate by name. An instance short a machinery file is a
    DIFFERENT build from one that has it, and must not hash the same — silently
    ignoring an absent file is how a partial copy passes as complete.
    """
    lines = ["%s:%s" % (rel, digests[rel]) for rel in sorted(digests)]
    lines += ["%s:MISSING" % rel for rel in sorted(missing)]
    combined = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:12]


def read_version(root):
    """The declared release number, or None.

    This one IS transcribed, and that is correct — a release number is a fact we
    declare, not one that can be derived from bytes. The digest is the part that
    must never be transcribed. Do not conflate them: the version says what we
    published, the digest says what is actually on this disk.
    """
    path = os.path.join(root, "arch", "VERSION")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    return line or None


def stray_root_version(root):
    """True when VERSION sits at root and arch/VERSION does not exist.

    The single failure this layout can produce, and it is mute without a name.
    An instance that copies in new machinery but leaves its old root VERSION
    behind hashes `arch/VERSION:MISSING`, computes an id no published one will
    ever match, and reads as permanently not-current — while the file it needs
    is sitting right there, one directory up. NEVER FALL BACK TO THE ROOT COPY
    to paper over it: reading it would make the version print correctly while
    the id stayed wrong, which is the same mismatch with its only symptom
    removed. Diagnose loudly, fix nothing.
    """
    return (not os.path.exists(os.path.join(root, "arch", "VERSION"))
            and os.path.exists(os.path.join(root, "VERSION")))


def read_birth_record(root):
    """Return {'build':…, 'version':…} as genesis recorded them, or None.

    Missing keys come back as None rather than raising — a record written by an
    older build legitimately has no version line, and that is not corruption.
    """
    path = os.path.join(root, BIRTH_RECORD)
    if not os.path.exists(path):
        return None
    out = {"build": None, "version": None}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            for key in ("build", "version"):
                if line.startswith(key + ":"):
                    out[key] = line.split(":", 1)[1].strip() or None
    return out if out["build"] else None


def write_birth_record(root, bid, count, missing, version=None):
    """Write the born-with record. Refuses to overwrite an existing one.

    Genesis runs once and this record is the only claim in the instance that
    cannot be recomputed. Overwriting it would destroy the single fact it exists
    to hold, so a re-run is an error rather than a silent no-op.
    """
    path = os.path.join(root, BIRTH_RECORD)
    if os.path.exists(path):
        raise SystemExit(
            "arch_build: %s already exists — refusing to overwrite.\n"
            "  This records what the instance was BORN with and is written once, at\n"
            "  genesis. It is not a running version and must not be refreshed on an\n"
            "  update; the live digest already tells you what you are running." % BIRTH_RECORD
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = (
        "# ARCH birth record — written once at genesis, never updated.\n"
        "# What this instance was created with. The build you are RUNNING is\n"
        "# computed live: python scripts/arch_build.py\n"
        "version: %s\n"
        "build: %s\n"
        "files: %d\n"
        "missing_at_genesis: %s\n"
    ) % (version or "unknown", bid, count,
         ",".join(sorted(missing)) if missing else "none")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows cp1252 → utf-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.abspath(os.path.join(here, "..")),
                    help="instance root (default: this script's repo root)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--write-birth-record", action="store_true",
                    help="genesis only: record what this instance was born with")
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    digests, missing = file_digests(root)
    bid = build_id(digests, missing)
    version = read_version(root)
    born = read_birth_record(root)
    stray = stray_root_version(root)

    if args.write_birth_record:
        path = write_birth_record(root, bid, len(digests), missing, version)
        print("arch_build: birth record written — %s (ARCH %s, build %s)"
              % (path, version or "version unknown", bid))
        return

    if args.json:
        print(json.dumps({
            "version": version,
            "build": bid,
            "files_present": len(digests),
            "files_expected": len(MACHINERY),
            "missing": missing,
            "born_version": (born or {}).get("version"),
            "born_build": (born or {}).get("build"),
            "modified_since_genesis": (born is not None and born["build"] != bid),
            "stray_root_version": stray,
            "digests": digests,
        }, indent=2, sort_keys=True))
        return

    # Version first: it is the thing a human can act on. The digest is the
    # tamper check and belongs underneath it, not in the headline.
    headline = "ARCH %s" % (("v" + version) if version else "— version not declared")
    if born is None:
        print(headline)
    elif born["build"] == bid:
        print("%s — machinery unmodified since genesis" % headline)
    else:
        print("%s — MACHINERY MODIFIED since genesis" % headline)
    print("  build %s · %d/%d machinery files" % (bid, len(digests), len(MACHINERY)))

    if stray:
        print("  ! VERSION is at your root; ARCH reads it from arch/VERSION.")
        print("    You took new machinery without moving it. Until you do, this id")
        print("    hashes arch/VERSION as MISSING and will never match a published")
        print("    one — you will read as not-current forever. Fix: move VERSION")
        print("    into arch/. Nothing else changes.")
    if missing:
        print("  ! missing: %s" % ", ".join(missing))
        print("    A missing machinery file changes the build id by design — a partial")
        print("    copy is a different build, not the same one with a gap.")
    if born is None:
        print("  born with: no record (instance predates the birth record, or genesis")
        print("             did not write one) — not an error, just unknown.")
    elif born["build"] != bid:
        print("  born with: ARCH %s, build %s" % (born["version"] or "unknown", born["build"]))
        print("    Expected if you have updated; worth a look if you have not.")
    print()
    print("  This says what you are RUNNING, not whether you are CURRENT. Being")
    print("  current needs the upstream repo as a comparison point — clone it and")
    print("  compare this id, or use the README's diff recipe.")
    # MP#66. This is the ONE moment an updater is already asking the question, so
    # it is where the pointer belongs. Measured, not assumed: an adopter session
    # replaced every machinery file, verified byte-identity, ran the suites and
    # reported "safe for close" — having never opened RELEASE_NOTES.md, which is
    # where the manifest edits and the live wiring checks live. Copying is
    # self-evidencing and reading is not, so a session optimising for a
    # demonstrable result does the demonstrable half. Naming the file here costs
    # two lines and asserts nothing this script cannot support.
    print("    Published release: https://www.4sync.ai/llms.txt — one fetch, no clone.")
    print("    Taking an update? RELEASE_NOTES.md carries what each release needs")
    print("    done BEYOND copying files. Copying machinery is not the whole update.")


if __name__ == "__main__":
    main()
# ═══ EOF arch_build.py ═══
