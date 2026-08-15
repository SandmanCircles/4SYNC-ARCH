#!/usr/bin/env python3
"""Suite for arch_build.py — the "what am I running?" build identity.

Statically countable unittest methods on purpose (MP#47/D6): rotate.py verifies
suite counts against test_<name>.py, and a hand-rolled harness cannot be counted,
so it gets reported as unchecked rather than verified.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import arch_build


class TempInstance(unittest.TestCase):
    """Builds a throwaway instance root with fake machinery files."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="archbuild_")
        for rel in arch_build.MACHINERY:
            path = os.path.join(self.root, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                if rel == "arch/VERSION":
                    fh.write(b"9.9.9\n")
                else:
                    fh.write(b"content of %s\n" % rel.encode("utf-8"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _id(self, root=None):
        digests, missing = arch_build.file_digests(root or self.root)
        return arch_build.build_id(digests, missing)

    def _write(self, rel, data):
        with open(os.path.join(self.root, rel.replace("/", os.sep)), "wb") as fh:
            fh.write(data)


class TestInventory(unittest.TestCase):
    def test_inventory_is_twenty_two_files(self):
        """A PINNED COUNT, and the pin is the point: changing the inventory changes
        every published build id's recomputation, so it must be a deliberate act
        rather than a side effect. 15 → 17 at v1.0.8 (MP#69), 17 → 18 at v1.0.9
        (MP#77), 18 → 20 under MP#80 (arch_update.py and its suite), 20 → 22 under MP#84
        (mail.py and its suite). If this test
        fails, do not just update the number — read RELEASE_NOTES and say what the
        change does to the back catalogue.

        WHAT THE MP#80 CHANGE DOES TO THE BACK CATALOGUE, stated here because this
        tripwire asked: every id published before it — v1.0.9's cc7f95b66647 and
        every earlier one — now recomputes to something else when checked by code of
        this generation. That is the FOURTH occurrence, which retires the word
        'incident' for it. It is not a defect and not repairable: an id is anchored
        to the tag for file CONTENT but to the running code for the INVENTORY.
        Adopters are untouched; each runs their own generation's arch_build.py
        against their own tree. The next release note must say so BEFORE the cut."""
        self.assertEqual(len(arch_build.MACHINERY), 22)

    def test_inventory_includes_this_script_and_its_suite(self):
        """MP#69. Absent from v1.0.0 through v1.0.7 with no recorded reason. A
        release changing only arch_build.py moved no build id, so an adopter
        could skip the file, compute an id that MATCHED the release, and be told
        they were current while missing the change — an identity omitting a file
        an update replaces. Named so a future tidy-up of "the script hashing
        itself looks odd" has to read why it is there."""
        self.assertIn("scripts/arch_build.py", arch_build.MACHINERY)
        self.assertIn("scripts/test_arch_build.py", arch_build.MACHINERY)

    def test_inventory_includes_the_version_file(self):
        """A release number is part of the build, not metadata about it."""
        self.assertIn("arch/VERSION", arch_build.MACHINERY)

    def test_version_is_inventoried_at_its_shipped_path(self):
        """It ships in arch/ so the rel key is identical everywhere.

        build_id hashes `rel:digest`, so a file genesis MOVED would hash under a
        different key than the same file upstream and no adopter could match a
        published id. Pinning the bare name here would reintroduce exactly that.
        """
        self.assertNotIn("VERSION", arch_build.MACHINERY)

    def test_inventory_has_no_duplicates(self):
        self.assertEqual(len(arch_build.MACHINERY), len(set(arch_build.MACHINERY)))

    def test_inventory_covers_the_five_check_sync_omits(self):
        """The whole point of the row: these five are machinery to an adopter.

        check_sync.py legitimately omits them (the silo keeps no copy, so they
        cannot drift). An adopter-facing build id that omitted them would ignore
        five files an update replaces.
        """
        for rel in ("scripts/meter.py", "scripts/test_meter.py",
                    "scripts/actuals.py", "scripts/test_actuals.py",
                    "scripts/wire_hooks.py"):
            self.assertIn(rel, arch_build.MACHINERY)

    def test_inventory_holds_no_instance_files(self):
        """Identity/instance state must never enter a build identity."""
        joined = " ".join(arch_build.MACHINERY)
        for forbidden in ("KERNEL", "STATUS", "CANON_INDEX", "MERGE_PLAN",
                          "NAMING_CONVENTIONS", "ABBA", "REFERENCE"):
            self.assertNotIn(forbidden, joined)

    def test_inventory_paths_are_relative_and_posix(self):
        for rel in arch_build.MACHINERY:
            self.assertFalse(os.path.isabs(rel))
            self.assertNotIn("\\", rel)


class TestNormalization(unittest.TestCase):
    def test_crlf_and_lf_normalize_equal(self):
        self.assertEqual(arch_build._norm(b"a\r\nb"), arch_build._norm(b"a\nb"))

    def test_trailing_newline_is_ignored(self):
        self.assertEqual(arch_build._norm(b"a\nb\n\n"), arch_build._norm(b"a\nb"))

    def test_interior_difference_is_preserved(self):
        self.assertNotEqual(arch_build._norm(b"a\nb"), arch_build._norm(b"a\nc"))


class TestBuildId(TempInstance):
    def test_id_is_twelve_hex_chars(self):
        bid = self._id()
        self.assertEqual(len(bid), 12)
        int(bid, 16)  # raises if not hex

    def test_id_is_stable_across_calls(self):
        self.assertEqual(self._id(), self._id())

    def test_id_changes_when_content_changes(self):
        before = self._id()
        self._write("scripts/rotate.py", b"different\n")
        self.assertNotEqual(before, self._id())

    def test_id_unchanged_by_line_ending_flip(self):
        before = self._id()
        self._write("scripts/rotate.py", b"content of scripts/rotate.py\r\n")
        self.assertEqual(before, self._id())

    def test_id_changes_when_a_file_is_missing(self):
        """A partial copy must not hash the same as a complete one."""
        before = self._id()
        os.remove(os.path.join(self.root, "scripts", "meter.py"))
        self.assertNotEqual(before, self._id())

    def test_missing_file_is_reported(self):
        os.remove(os.path.join(self.root, "scripts", "meter.py"))
        digests, missing = arch_build.file_digests(self.root)
        self.assertEqual(missing, ["scripts/meter.py"])
        # DERIVED, not a literal. This read 14 beside a 15-file list and broke
        # the moment the inventory moved — a second copy of a count that the
        # list one import away already holds. TestInventory asserts the number
        # ONCE, on purpose, as the canary for an accidental inventory change;
        # everywhere else derives it, so only the deliberate assertion fails.
        self.assertEqual(len(digests), len(arch_build.MACHINERY) - 1)

    def test_two_different_missing_files_give_different_ids(self):
        """Missing files participate by NAME, not merely by count."""
        os.remove(os.path.join(self.root, "scripts", "meter.py"))
        one = self._id()
        shutil.copy(os.path.join(self.root, "scripts", "rotate.py"),
                    os.path.join(self.root, "scripts", "meter.py"))
        self._write("scripts/meter.py", b"content of scripts/meter.py\n")
        os.remove(os.path.join(self.root, "scripts", "actuals.py"))
        self.assertNotEqual(one, self._id())

    def test_all_files_present_reports_full_count(self):
        digests, missing = arch_build.file_digests(self.root)
        self.assertEqual(missing, [])
        self.assertEqual(len(digests), len(arch_build.MACHINERY))


class TestVersion(TempInstance):
    def test_version_is_read_from_the_file(self):
        self.assertEqual(arch_build.read_version(self.root), "9.9.9")

    def test_absent_version_reads_as_none(self):
        os.remove(os.path.join(self.root, "arch", "VERSION"))
        self.assertIsNone(arch_build.read_version(self.root))

    def test_empty_version_reads_as_none(self):
        self._write("arch/VERSION", b"\n")
        self.assertIsNone(arch_build.read_version(self.root))

    def test_version_bump_changes_the_build_id(self):
        """Two trees differing only in VERSION are different builds."""
        before = self._id()
        self._write("arch/VERSION", b"9.9.10\n")
        self.assertNotEqual(before, self._id())

    def test_a_root_version_is_never_read_as_a_fallback(self):
        """The deliberate non-behavior, and reversing it would be a bug.

        Falling back would print the right version while the id stayed wrong —
        the same mismatch with its only visible symptom removed.
        """
        os.remove(os.path.join(self.root, "arch", "VERSION"))
        self._write("VERSION", b"9.9.9\n")
        self.assertIsNone(arch_build.read_version(self.root))


class TestStrayRootVersion(TempInstance):
    """The one failure this layout can produce: machinery updated, VERSION not."""

    def test_a_correctly_placed_version_is_not_stray(self):
        self.assertFalse(arch_build.stray_root_version(self.root))

    def test_version_left_at_root_is_detected(self):
        os.remove(os.path.join(self.root, "arch", "VERSION"))
        self._write("VERSION", b"9.9.9\n")
        self.assertTrue(arch_build.stray_root_version(self.root))

    def test_both_copies_present_is_not_stray(self):
        """arch/ is authoritative — a leftover root copy is not an alarm."""
        self._write("VERSION", b"9.9.9\n")
        self.assertFalse(arch_build.stray_root_version(self.root))

    def test_neither_copy_present_is_not_stray(self):
        """Absent everywhere is `missing`, which already reports itself."""
        os.remove(os.path.join(self.root, "arch", "VERSION"))
        self.assertFalse(arch_build.stray_root_version(self.root))


class TestBirthRecord(TempInstance):
    def test_absent_record_reads_as_none(self):
        self.assertIsNone(arch_build.read_birth_record(self.root))

    def test_written_record_round_trips(self):
        bid = self._id()
        arch_build.write_birth_record(self.root, bid, 15, [], "9.9.9")
        rec = arch_build.read_birth_record(self.root)
        self.assertEqual(rec["build"], bid)
        self.assertEqual(rec["version"], "9.9.9")

    def test_record_lands_at_the_declared_path(self):
        arch_build.write_birth_record(self.root, self._id(), 15, [])
        self.assertTrue(os.path.exists(os.path.join(self.root, arch_build.BIRTH_RECORD)))

    def test_record_refuses_to_overwrite(self):
        """It holds the one fact that cannot be recomputed — a re-run is an error."""
        arch_build.write_birth_record(self.root, self._id(), 15, [])
        with self.assertRaises(SystemExit):
            arch_build.write_birth_record(self.root, "deadbeef0000", 15, [])

    def test_record_survives_a_machinery_change(self):
        """Born-with is a historical fact; changing machinery must not rewrite it."""
        born = self._id()
        arch_build.write_birth_record(self.root, born, 15, [], "9.9.9")
        self._write("scripts/rotate.py", b"updated\n")
        self.assertEqual(arch_build.read_birth_record(self.root)["build"], born)
        self.assertNotEqual(self._id(), born)

    def test_record_without_a_version_line_still_reads(self):
        """A record written by an older build has no version — not corruption."""
        arch_build.write_birth_record(self.root, self._id(), 15, [])
        rec = arch_build.read_birth_record(self.root)
        self.assertEqual(rec["version"], "unknown")

    def test_record_notes_files_missing_at_genesis(self):
        arch_build.write_birth_record(self.root, self._id(), 14, ["scripts/meter.py"])
        path = os.path.join(self.root, arch_build.BIRTH_RECORD)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("missing_at_genesis: scripts/meter.py", fh.read())

    def test_record_with_no_build_line_reads_as_none(self):
        path = os.path.join(self.root, arch_build.BIRTH_RECORD)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# a record with no build line\n")
        self.assertIsNone(arch_build.read_birth_record(self.root))


class TestUpdatePointer(TempInstance):
    """MP#66. The human-readable report is the one moment an updater is already
    asking "what am I running?", so it is where the update instructions get
    named. Measured, not assumed: an adopter session replaced every machinery
    file, verified byte-identity, ran the suites and reported "safe for close"
    — having never opened RELEASE_NOTES.md, where the manifest edits and the
    live wiring checks live. These pin the pointer so a later tidy-up cannot
    quietly remove it and leave the report looking complete again."""

    def _report(self):
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["arch_build.py", "--dir", self.root]
        try:
            with contextlib.redirect_stdout(buf):
                arch_build.main()
        finally:
            sys.argv = argv
        return buf.getvalue()

    def test_report_names_the_release_notes(self):
        self.assertIn("RELEASE_NOTES.md", self._report())

    def test_report_names_the_published_release_url(self):
        self.assertIn("llms.txt", self._report())

    def test_report_still_refuses_to_claim_currency(self):
        """The pointer must not turn into a currency claim. This script has no
        upstream and must never compute "you are up to date" from local data —
        that is a lie with a checkmark on it."""
        out = self._report()
        self.assertIn("not whether you are CURRENT", out)

    def test_json_output_carries_no_prose_pointer(self):
        """--json is consumed by tooling. A human-facing sentence in it would be
        a field nobody declared."""
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["arch_build.py", "--dir", self.root, "--json"]
        try:
            with contextlib.redirect_stdout(buf):
                arch_build.main()
        finally:
            sys.argv = argv
        payload = json.loads(buf.getvalue())
        self.assertNotIn("RELEASE_NOTES.md", buf.getvalue())
        self.assertIn("build", payload)


class TestMachineryInventoryIsComplete(unittest.TestCase):
    """MP#77, and it exists to close a CLASS rather than a third instance.

    Twice now a file has been missing from the inventory that decides what a build
    IS: `arch_build.py` and its own suite from v1.0.0 through v1.0.7 (MP#69), then
    `scripts/test_wire_hooks.py` one release later. Both had the same consequence —
    a release changing only the missing file moves no build id, so an adopter can
    skip it, compute an id that MATCHES the release, and be told they are current
    while missing the change.

    THE REASON REVIEWING THE LIST WOULD NEVER HAVE FOUND THE SECOND ONE: the hole
    was not created by editing this list. `wire_hooks.py` sat here correctly for
    releases; the gap opened the day MP#65 gave it its first suite, because nothing
    adds a file to the inventory when a NEW TEST is born. That is a defect of
    omission triggered by unrelated work, which is precisely the kind a test catches
    and a reader does not."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_machinery_lists_every_paired_suite(self):
        missing = []
        for rel in arch_build.MACHINERY:
            if not rel.endswith(".py"):
                continue
            directory, name = rel.rsplit("/", 1)
            if name.startswith("test_"):
                continue
            suite = "%s/test_%s" % (directory, name)
            on_disk = os.path.exists(os.path.join(self.ROOT, suite.replace("/", os.sep)))
            if on_disk and suite not in arch_build.MACHINERY:
                missing.append(suite)
        self.assertEqual(missing, [],
                         "these suites exist on disk but are absent from MACHINERY, so a "
                         "release changing only them would move no build id: %s" % missing)

    def test_every_listed_file_exists(self):
        """The mirror image. An entry naming a file that is not there hashes as
        `<path>:MISSING` — deliberate, so a partial copy cannot impersonate a
        complete one — but in THIS repo it would mean the list has drifted from the
        tree, and every adopter would inherit the phantom."""
        absent = [rel for rel in arch_build.MACHINERY
                  if not os.path.exists(os.path.join(self.ROOT, rel.replace("/", os.sep)))]
        self.assertEqual(absent, [], "MACHINERY names files this repo does not have: %s" % absent)


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_arch_build.py ═══
