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
# All 15 shipped machinery files (14 code files plus VERSION). Every adopter gets all of them, so all of them
# are part of the build identity.
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
#   arch_build.py (15 files, SHIPS TO ADOPTERS) asks "what am I running?" From an
#     adopter's position those five are machinery exactly like the other nine:
#     copied in, never renamed, replaced wholesale by an update. Omitting them
#     would produce a build identity that silently ignores five files an update
#     changes.
#
# Treating those two questions as one is what left the gap. Keep both lists, keep
# them honest, and keep this comment next to whichever one you edit.
MACHINERY = [
    # The release number is part of the build, not metadata about it: two trees
    # that differ only in VERSION are different builds, and hashing it means
    # "v1.0.1, unmodified" is a single check rather than two.
    "VERSION",
    "hooks/pre_tool_use.py",
    "hooks/test_pre_tool_use.py",
    "hooks/session_start.py",
    "hooks/test_session_start.py",
    "hooks/claude-settings.example.json",
    "scripts/rotate.py",
    "scripts/test_rotate.py",
    "scripts/meter.py",
    "scripts/test_meter.py",
    "scripts/actuals.py",
    "scripts/test_actuals.py",
    "scripts/split_ledger.py",
    "scripts/test_split_ledger.py",
    "scripts/wire_hooks.py",
]

# Where genesis records what the instance was born with. Under archive/ because
# that is where genesis already puts the packaging — the birth record belongs
# with the birth certificate, not in config/ (which is identity state, prefixed
# at genesis, and edited for the life of the project).
BIRTH_RECORD = os.path.join("archive", "ARCH_BUILD.txt")


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
    path = os.path.join(root, "VERSION")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    return line or None


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


if __name__ == "__main__":
    main()
# ═══ EOF arch_build.py ═══
