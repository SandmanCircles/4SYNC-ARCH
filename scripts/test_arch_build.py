#!/usr/bin/env python3
"""Suite for arch_build.py — the "what am I running?" build identity.

Statically countable unittest methods on purpose (MP#47/D6): rotate.py verifies
suite counts against test_<name>.py, and a hand-rolled harness cannot be counted,
so it gets reported as unchecked rather than verified.
"""

import os
import shutil
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
    def test_inventory_is_fourteen_files(self):
        self.assertEqual(len(arch_build.MACHINERY), 14)

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
        self.assertEqual(len(digests), 13)

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


class TestBirthRecord(TempInstance):
    def test_absent_record_reads_as_none(self):
        self.assertIsNone(arch_build.read_birth_record(self.root))

    def test_written_record_round_trips(self):
        bid = self._id()
        arch_build.write_birth_record(self.root, bid, 14, [])
        self.assertEqual(arch_build.read_birth_record(self.root), bid)

    def test_record_lands_at_the_declared_path(self):
        arch_build.write_birth_record(self.root, self._id(), 14, [])
        self.assertTrue(os.path.exists(os.path.join(self.root, arch_build.BIRTH_RECORD)))

    def test_record_refuses_to_overwrite(self):
        """It holds the one fact that cannot be recomputed — a re-run is an error."""
        arch_build.write_birth_record(self.root, self._id(), 14, [])
        with self.assertRaises(SystemExit):
            arch_build.write_birth_record(self.root, "deadbeef0000", 14, [])

    def test_record_survives_a_machinery_change(self):
        """Born-with is a historical fact; changing machinery must not rewrite it."""
        born = self._id()
        arch_build.write_birth_record(self.root, born, 14, [])
        self._write("scripts/rotate.py", b"updated\n")
        self.assertEqual(arch_build.read_birth_record(self.root), born)
        self.assertNotEqual(self._id(), born)

    def test_record_notes_files_missing_at_genesis(self):
        arch_build.write_birth_record(self.root, self._id(), 13, ["scripts/meter.py"])
        path = os.path.join(self.root, arch_build.BIRTH_RECORD)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("missing_at_genesis: scripts/meter.py", fh.read())

    def test_record_with_no_build_line_reads_as_none(self):
        path = os.path.join(self.root, arch_build.BIRTH_RECORD)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# a record with no build line\n")
        self.assertIsNone(arch_build.read_birth_record(self.root))


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_arch_build.py ═══
