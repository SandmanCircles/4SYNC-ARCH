#!/usr/bin/env python3
"""
arch_update.py — apply an ARCH release to this instance, and prove it landed.

WHY THIS EXISTS

`arch_build.py` answers "what am I running?" by computation. Applying an update was
the one step still done by hand: copy N files from a clone, against a prose list in
RELEASE_NOTES.md, for your particular starting version. That is a transcribed
procedure in a project whose whole claim is that a fact which can be computed must
never be transcribed.

It is also the step that LOOKS dangerous to a safety mechanism, and correctly so. An
adopter reported a bulk `cp` loop being blocked by their own harness as a risky mass
overwrite, and fell back to copying files one at a time. A recursive copy over an
instance root IS one wrong path away from destroying `config/`, the ledger, or
`tasks/` — the state the adopter authored and nobody else has a copy of.

WHAT MAKES THIS SAFE ENOUGH TO SHIP

Not that it copies carefully. That it CANNOT write outside the machinery inventory.
Every target is resolved and checked for containment before anything is written, and
the inventory is a fixed list of files that came from us — every one recoverable by
re-copying from a clone. Contrast the two writers that already ship: `rotate.py`
rewrites your ledger and moves your task documents, and `split_ledger.py` performs an
irreversible migration of a file you wrote. This tool touches none of that, by
construction rather than by care.

It follows the convention those two established and invents no new one: DRY RUN BY
DEFAULT, `--apply` to write.

ORDER OF OPERATIONS, and the order is the point:

  1. verify the SOURCE is a complete machinery tree      -> refuse if not
  2. verify the source's build id against --expect       -> refuse BEFORE writing
  3. copy only inventory files that actually differ      -> idempotent
  4. recompute THIS instance's build id afterwards       -> prove it landed
  5. print what copying did NOT do, for your two versions

Verifying the source after copying would report a bad clone once it was already on
disk. The check is worth nothing at that point.

Usage:
    python scripts/arch_update.py --from ../4SYNC-ARCH-clone
    python scripts/arch_update.py --from ../clone --expect cc7f95b66647 --apply

Exit status is 0 when the instance ends up matching the source, 1 otherwise — so it
can gate a script. Nothing here makes a network call.
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_build


# ── What copying cannot do (MP#82) ───────────────────────────────────────────
#
# Copying is self-evidencing: the build id moves, and it either matches the source
# or it does not. THE STEPS THAT ARE NOT COPYING ARE THE DANGEROUS ONES, because
# nothing reports them. The live example is the v1.0.5 `arch/VERSION` move — a copy
# alone leaves the old `VERSION` at the instance root, `arch_build.py` then hashes
# `arch/VERSION:MISSING`, and the instance matches NO release at all. The symptom is
# a currency check that says nothing useful, which is the failure `arch_build.py`
# exists to prevent.
#
# So a release note carries a `**By hand:` lead in the same shape as the
# `**Machinery:` and `**Manifest:` leads beside it, and this prints the ones between
# the instance's version and the source's. The silo's `release.py` refuses to cut a
# release whose note has no such lead — writing "nothing" is the point, because an
# absent line and a forgotten one are indistinguishable.
#
# READ FROM THE CLONE, NEVER FROM THE INSTANCE, and this is the load-bearing detail:
# the instance's copy of the notes is older than the release being applied and cannot
# contain its note. The clone is current by definition. It is also why an adopter
# should run the CLONE's `arch_update.py` rather than their own.
#
# GENERATED, NEVER TRANSCRIBED. The note is the single source; nothing anywhere keeps
# a second list of steps. That is the rule MP#81 paid for twice, and it is why the
# silo's cut gate imports this parser instead of writing its own — one definition of
# what a `By hand:` block is, so the gate and the updater cannot disagree.

NOTES = "RELEASE_NOTES.md"
# The two leads that describe work a copy cannot perform. `**Machinery:` is
# deliberately absent: that one IS the copy, and this tool has already done it and
# proved it with a build id. A manifest change is by-hand work with its own
# established lead, so printing `By hand:` alone would answer "nothing" to an
# adopter who still has a `close:` step to merge.
LEADS = ("Manifest:", "By hand:")
_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s*$")


def semver(version):
    """(major, minor, patch), or None when it is not a release number."""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (version or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def release_sections(text):
    """[(version, body)] for every `## v<semver>` heading, in file order."""
    sections, version, body = [], None, []
    for line in text.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            if version:
                sections.append((version, "\n".join(body)))
            version, body = heading.group(1), []
        elif version and line.startswith("## "):
            sections.append((version, "\n".join(body)))     # a non-release heading ends it
            version, body = None, []
        elif version:
            body.append(line)
    if version:
        sections.append((version, "\n".join(body)))
    return sections


def lead_block(body, lead):
    """The `**<lead>` block as a list of lines, or None if the note carries none.

    A block is the lead line plus the lines under it up to the first blank one — the
    shape the leads already use, so this reads what an author already writes rather
    than asking for a format nobody would remember to follow.
    """
    lines = body.splitlines()
    marker = "**" + lead
    for index, line in enumerate(lines):
        if line.strip().startswith(marker):
            block = [line.strip()]
            for following in lines[index + 1:]:
                if not following.strip():
                    break
                block.append(following.strip())
            return block
    return None


def by_hand(body):
    """The `**By hand:` block — the lead the silo's cut gate requires."""
    return lead_block(body, "By hand:")


def steps_between(text, have, want):
    """[(version, [(lead, block_or_None), ...])] for releases after `have`, up to `want`.

    OLDEST FIRST: an adopter crossing three releases performs the by-hand steps in the
    order they were released, and v1.0.5's `arch/VERSION` move has to happen before a
    later note can assume it did.
    """
    low, high = semver(have), semver(want)
    picked = []
    for version, body in release_sections(text):
        key = semver(version)
        if key is None or (low and key <= low) or (high and key > high):
            continue
        picked.append((version, [(lead, lead_block(body, lead)) for lead in LEADS]))
    picked.sort(key=lambda pair: semver(pair[0]))
    return picked


_UNSET = object()


def beyond_copying(source, dest, have=_UNSET):
    """Display lines naming what this update does not do for you.

    `have` is the instance's version BEFORE the copy when the caller knows it
    (Report.version_before); reading it from disk after an --apply reads the number
    the update just wrote, which spans nothing.
    """
    if have is _UNSET:
        have = arch_build.read_version(dest)
    want = arch_build.read_version(source)
    path = os.path.join(source, NOTES)
    if not os.path.exists(path):
        return ["BEYOND COPYING — the clone has no %s. Read the release's notes "
                "before" % NOTES,
                "  calling this update done: copying machinery is not the whole update."]
    if semver(have) is None:
        return ["BEYOND COPYING — this instance declares no release number "
                "(arch/VERSION), so",
                "  the releases that apply to it cannot be worked out here. Read %s"
                % NOTES,
                "  in the clone, from your build id forward."]
    with open(path, "r", encoding="utf-8") as fh:
        steps = steps_between(fh.read(), have, want)
    span = "v%s -> v%s" % (have, want)
    if not steps:
        return ["BEYOND COPYING — %s: no releases between them." % span]
    out = ["BEYOND COPYING — %s, from %s in the clone:" % (span, NOTES)]
    silent = []
    for version, blocks in steps:
        printed = [line for _lead, block in blocks if block for line in block]
        if printed:
            out.append("")
            out.append("  v%s" % version)
            out.extend("    %s" % line for line in printed)
        if dict(blocks).get("By hand:") is None:
            silent.append("v" + version)
    if silent:
        # Silence would read as "nothing to do", and that is a different claim: a
        # note written before the convention existed says neither. Named once as a
        # list rather than repeated per release — a paragraph restated five times is
        # one an adopter scrolls past, which is the failure mode it exists to avoid.
        out.append("")
        out.append("  NO `By hand:` LINE IN %s — those notes predate the convention,"
                   % ", ".join(silent))
        out.append("  so read their sections yourself. Absence is not a promise of "
                   "nothing.")
    return out


class RefusedWrite(Exception):
    """A write target resolved outside the destination's machinery set."""


class IncompleteSource(Exception):
    """The source is missing machinery files — it is not a whole ARCH tree."""


class BuildMismatch(Exception):
    """The source did not match --expect, or the result did not match the source."""


class Report(object):
    def __init__(self):
        self.changed = []          # [(rel, 'update'|'add')]
        self.unchanged = []
        self.applied = False
        self.source_build_id = None
        self.result_build_id = None
        # The instance's version BEFORE anything was copied. arch/VERSION is itself
        # machinery, so after --apply the file on disk already says the new number —
        # and a beyond-copying report computed from disk at render time would span
        # "new -> new" and print nothing, on exactly the run that performed the
        # update. The dry run showed the steps; the apply swallowed them.
        self.version_before = None

    @property
    def would_change(self):
        return bool(self.changed)


def _target(dest, rel):
    """Resolve `rel` under `dest`, refusing anything that escapes it.

    MACHINERY is a constant, so no input reaches this — which is exactly why it is
    checked rather than assumed. The list is edited by hand (see MP#81), and the day
    a `../` lands in it should be the day this raises, not the day it writes.
    """
    if rel not in arch_build.MACHINERY:
        raise RefusedWrite("%s is not in the machinery inventory" % rel)
    root = os.path.realpath(dest)
    path = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
    if path != root and not path.startswith(root + os.sep):
        raise RefusedWrite("%s resolves outside %s" % (rel, root))
    return path


def update(source, dest, apply=False, expect=None):
    """Compare, optionally copy, and verify. Returns a Report; raises on refusal."""
    # THE DOCUMENTED COMMAND IS THE CLONE'S UPDATER, and --dir defaults to the tree
    # this script lives in — which, run from the clone, IS the clone. Drop the one
    # flag and the tool compares the clone with itself and prints "already current,
    # nothing to do": a success message about an instance it never looked at. A
    # false pass one omitted flag off the happy path is refused, not reported.
    if os.path.realpath(source) == os.path.realpath(dest):
        raise RefusedWrite(
            "source and destination are the same tree (%s). You are probably "
            "running the clone's updater without --dir — pass --dir <your instance>."
            % os.path.realpath(source))
    src_digests, src_missing = arch_build.file_digests(source)
    if src_missing:
        raise IncompleteSource(
            "source is missing %d machinery file(s): %s"
            % (len(src_missing), ", ".join(sorted(src_missing))))

    report = Report()
    report.source_build_id = arch_build.build_id(src_digests, src_missing)
    report.version_before = arch_build.read_version(dest)

    # STEP 2 — before any write. A clone that is not what the adopter believes it is
    # must be rejected while the instance is still untouched.
    if expect and report.source_build_id != expect:
        raise BuildMismatch(
            "source is build %s, but --expect said %s — nothing was written"
            % (report.source_build_id, expect))

    dst_digests, _ = arch_build.file_digests(dest)
    for rel in arch_build.MACHINERY:
        if rel not in dst_digests:
            report.changed.append((rel, "add"))
        elif dst_digests[rel] != src_digests[rel]:
            report.changed.append((rel, "update"))
        else:
            report.unchanged.append(rel)

    if apply:
        for rel, _kind in report.changed:
            path = _target(dest, rel)          # containment check, every file
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            shutil.copyfile(os.path.join(source, rel.replace("/", os.sep)), path)
        report.applied = True

    # STEP 4 — recompute rather than assume. Copying without verifying is the
    # procedure this tool replaces.
    res_digests, res_missing = arch_build.file_digests(dest)
    report.result_build_id = arch_build.build_id(res_digests, res_missing)
    return report


def _render(report, source, dest, apply):
    out = []
    out.append("source build   %s" % report.source_build_id)
    if not report.changed:
        out.append("instance       %s — already current, nothing to do"
                   % report.result_build_id)
        return out
    for rel, kind in report.changed:
        out.append("  %-6s %s" % (kind, rel))
    out.append("%d file(s) differ, %d already current"
               % (len(report.changed), len(report.unchanged)))
    if not apply:
        out.append("instance       %s (unchanged)" % report.result_build_id)
        out.append("mode: dry-run — pass --apply to write")
    else:
        out.append("instance       %s" % report.result_build_id)
        if report.result_build_id == report.source_build_id:
            out.append("VERIFIED: this instance now computes the source build id.")
        else:
            out.append("MISMATCH: expected %s, computed %s. The copy did not produce "
                       "the source build." % (report.source_build_id,
                                              report.result_build_id))
        out.append("Machinery only. Your config/, ledger and tasks/ were not read "
                   "or written.")
    # Printed in BOTH modes. A dry run is the preview of the whole update, and the
    # steps a copy cannot do are the half worth previewing — knowing them before
    # `--apply` is strictly better than being told afterwards.
    out.append("")
    out.extend(beyond_copying(source, dest, have=report.version_before))
    return out


def main():
    # Windows consoles default to cp1252; the em dash and tick/cross glyphs in the
    # output would arrive mangled or raise on print. Reconfigure early. Matches the
    # form already in meter.py, actuals.py, arch_build.py and wire_hooks.py — this
    # was the house pattern in four scripts and absent from five (MP#84).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Apply an ARCH release to this instance, machinery only.")
    ap.add_argument("--from", dest="source", required=True,
                    help="path to a clone of the release you are applying")
    ap.add_argument("--dir", default=os.path.abspath(os.path.join(here, "..")),
                    help="the instance to update (default: this one)")
    ap.add_argument("--expect", help="build id the source must compute, checked "
                                     "before anything is written")
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this is a dry run")
    args = ap.parse_args()

    try:
        report = update(args.source, args.dir, apply=args.apply, expect=args.expect)
    except (IncompleteSource, BuildMismatch, RefusedWrite) as exc:
        sys.stderr.write("arch_update: REFUSED — %s\n" % exc)
        return 1

    for line in _render(report, args.source, args.dir, args.apply):
        print(line)
    if args.apply and report.result_build_id != report.source_build_id:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
