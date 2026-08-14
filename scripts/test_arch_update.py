#!/usr/bin/env python3
"""Tests for arch_update.py — MP#80.

THE TESTS THAT MATTER HERE ARE THE REFUSALS. This is the first shipped tool whose
whole purpose is overwriting files in someone else's project, and the property that
makes its blast radius smaller than `rotate.py`'s is not that it copies correctly —
it is that it CANNOT write anything outside the machinery inventory. A happy-path
test proves the feature; the refusal tests prove the boundary, and the boundary is
the reason the tool was allowed to exist.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_build          # noqa: E402
import arch_update         # noqa: E402


def _write(root, rel, text):
    path = os.path.join(root, rel.replace("/", os.sep))
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _read(root, rel):
    with open(os.path.join(root, rel.replace("/", os.sep)), encoding="utf-8") as fh:
        return fh.read()


class UpdateCase(unittest.TestCase):
    """A source clone and a destination instance, both real trees on disk."""

    def _trees(self, src_body="NEW", dst_body="OLD"):
        # realpath: macOS hands back a symlinked temp dir, which broke three tests
        # in test_wire_hooks.py on every macOS box and nowhere else (MP#78).
        root = os.path.realpath(tempfile.mkdtemp(prefix="arch-upd-"))
        self.addCleanup(shutil.rmtree, root, True)
        src, dst = os.path.join(root, "clone"), os.path.join(root, "instance")
        for rel in arch_build.MACHINERY:
            _write(src, rel, src_body + " " + rel + "\n")
            _write(dst, rel, dst_body + " " + rel + "\n")
        return src, dst

    def _run(self, src, dst, **kw):
        return arch_update.update(source=src, dest=dst, **kw)


class TestDryRunIsTheDefault(UpdateCase):

    def test_dry_run_changes_nothing_on_disk(self):
        src, dst = self._trees()
        before = {rel: _read(dst, rel) for rel in arch_build.MACHINERY}
        report = self._run(src, dst)                       # no apply=True
        self.assertTrue(report.would_change)
        for rel, text in before.items():
            self.assertEqual(_read(dst, rel), text, "dry run wrote %s" % rel)

    def test_apply_copies_and_the_ids_then_match(self):
        src, dst = self._trees()
        report = self._run(src, dst, apply=True)
        self.assertTrue(report.applied)
        self.assertEqual(report.source_build_id, report.result_build_id)
        for rel in arch_build.MACHINERY:
            self.assertEqual(_read(dst, rel), _read(src, rel))


class TestIdempotence(UpdateCase):

    def test_a_second_apply_is_a_no_op(self):
        src, dst = self._trees()
        self._run(src, dst, apply=True)
        second = self._run(src, dst, apply=True)
        self.assertFalse(second.would_change)
        self.assertEqual(second.changed, [])


class TestItRefusesToWriteOutsideTheInventory(UpdateCase):
    """The precondition. Everything else about this tool is negotiable."""

    def test_a_non_machinery_file_in_the_clone_is_not_copied(self):
        src, dst = self._trees()
        _write(src, "README.md", "upstream readme\n")
        _write(src, "config/KERNEL.yaml", "UPSTREAM IDENTITY — must never land\n")
        self._run(src, dst, apply=True)
        self.assertFalse(os.path.exists(os.path.join(dst, "README.md")))
        self.assertFalse(os.path.exists(os.path.join(dst, "config", "KERNEL.yaml")))

    def test_the_instance_side_of_the_split_is_untouched(self):
        """config/, the ledger and tasks/ are the adopter's own authored state.
        Every release note says do not touch them; this asserts the tool cannot."""
        src, dst = self._trees()
        _write(dst, "config/KERNEL.yaml", "MY IDENTITY\n")
        _write(dst, "MERGE_PLAN.md", "MY LEDGER\n")
        _write(dst, "tasks/MP-001.md", "MY TASK\n")
        self._run(src, dst, apply=True)
        self.assertEqual(_read(dst, "config/KERNEL.yaml"), "MY IDENTITY\n")
        self.assertEqual(_read(dst, "MERGE_PLAN.md"), "MY LEDGER\n")
        self.assertEqual(_read(dst, "tasks/MP-001.md"), "MY TASK\n")

    def test_a_path_escaping_the_destination_is_refused(self):
        """MACHINERY is a constant today, so this cannot happen by input — it is a
        guard against a future edit to that list, checked rather than assumed."""
        src, dst = self._trees()
        with self.assertRaises(arch_update.RefusedWrite):
            arch_update._target(dst, "../outside.py")


class TestItVerifiesRatherThanTrusts(UpdateCase):

    def test_expect_mismatch_refuses_BEFORE_writing(self):
        """Verify the source first. A clone that is not what the adopter thinks it
        is must not be copied and then reported wrong — by then it is on disk."""
        src, dst = self._trees()
        before = _read(dst, "arch/VERSION")
        with self.assertRaises(arch_update.BuildMismatch):
            self._run(src, dst, apply=True, expect="0" * 12)
        self.assertEqual(_read(dst, "arch/VERSION"), before, "wrote before verifying")

    def test_expect_matching_the_source_proceeds(self):
        src, dst = self._trees()
        digests, missing = arch_build.file_digests(src)
        real = arch_build.build_id(digests, missing)
        report = self._run(src, dst, apply=True, expect=real)
        self.assertTrue(report.applied)
        self.assertEqual(report.result_build_id, real)

    def test_a_clone_missing_machinery_is_refused(self):
        """A partial clone would otherwise be copied faithfully, producing an
        instance that matches no release — the failure arch_build exists to catch."""
        src, dst = self._trees()
        os.remove(os.path.join(src, "scripts", "rotate.py"))
        with self.assertRaises(arch_update.IncompleteSource):
            self._run(src, dst, apply=True)


class TestTheInventoryListsThisScriptAndItsSuite(unittest.TestCase):
    """MP#69 and MP#77 were both a script added without its suite in MACHINERY, and
    MP#80's own criteria demand this land in the same commit. Asserted, not promised."""

    def test_both_files_are_in_the_inventory(self):
        self.assertIn("scripts/arch_update.py", arch_build.MACHINERY)
        self.assertIn("scripts/test_arch_update.py", arch_build.MACHINERY)


if __name__ == "__main__":
    unittest.main()
