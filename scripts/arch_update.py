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
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_build


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
    src_digests, src_missing = arch_build.file_digests(source)
    if src_missing:
        raise IncompleteSource(
            "source is missing %d machinery file(s): %s"
            % (len(src_missing), ", ".join(sorted(src_missing))))

    report = Report()
    report.source_build_id = arch_build.build_id(src_digests, src_missing)

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


def _render(report, dest, apply):
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
        out.append("NOT DONE FOR YOU: read RELEASE_NOTES.md for any step beyond "
                   "copying — copying machinery is not the whole update.")
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

    for line in _render(report, args.dir, args.apply):
        print(line)
    if args.apply and report.result_build_id != report.source_build_id:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
