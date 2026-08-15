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


class TestSourceAndDestMustDiffer(UpdateCase):
    """SECOND-PASS AUDIT FIND. The documented command is `python
    <CLONE>/scripts/arch_update.py --from <CLONE> --dir .` — and --dir DEFAULTS to
    the tree the script lives in, which for the clone's updater IS the clone. Drop
    the one flag and the tool compares the clone with itself and prints "already
    current, nothing to do": a success message, about an instance it never looked
    at. A false pass sitting one omitted flag away from the happy path."""

    def test_updating_a_tree_from_itself_is_refused(self):
        src, _ = self._trees()
        with self.assertRaises(arch_update.RefusedWrite):
            arch_update.update(src, src)

    def test_the_refusal_names_the_missing_flag(self):
        src, _ = self._trees()
        try:
            arch_update.update(src, src)
        except arch_update.RefusedWrite as exc:
            self.assertIn("--dir", str(exc))
        else:
            self.fail("no refusal")

    def test_a_real_pair_is_untouched_by_the_guard(self):
        src, dst = self._trees()
        report = arch_update.update(src, dst, apply=True)
        self.assertEqual(report.source_build_id, report.result_build_id)


class TestTheInventoryListsThisScriptAndItsSuite(unittest.TestCase):
    """MP#69 and MP#77 were both a script added without its suite in MACHINERY, and
    MP#80's own criteria demand this land in the same commit. Asserted, not promised."""

    def test_both_files_are_in_the_inventory(self):
        self.assertIn("scripts/arch_update.py", arch_build.MACHINERY)
        self.assertIn("scripts/test_arch_update.py", arch_build.MACHINERY)


NOTES_FIXTURE = """# 4SYNC ARCH — Release Notes

## How to apply any update

Prose that is not a release and must not be parsed as one.

---

## v1.1.0

**Machinery: replace all six changed files** — this line is the copy, which the tool
already did.

**Manifest: one optional addition.** Declare `close.snapshot.overflow_to`.

**By hand: nothing.**

Trailing prose after the blank line, which is not part of the block.

---

## v1.0.5

**Machinery: replace two files.**

**By hand:** move `VERSION` to `arch/VERSION`; a copy left at root hashes as
MISSING and the instance then matches no release at all.

---

## v1.0.4

**Machinery: one file.**

**Manifest: nothing to change.**
"""


class TestReleaseNoteParsing(unittest.TestCase):
    """The parser the silo's cut gate imports rather than re-implementing."""

    def test_only_release_headings_open_a_section(self):
        versions = [v for v, _ in arch_update.release_sections(NOTES_FIXTURE)]
        self.assertEqual(versions, ["1.1.0", "1.0.5", "1.0.4"])

    def test_a_non_release_heading_closes_the_section_it_follows(self):
        text = "## v1.0.1\n\n**By hand: nothing.**\n\n## Appendix\n\n**By hand:** no.\n"
        sections = dict(arch_update.release_sections(text))
        self.assertNotIn("no.", sections["1.0.1"])

    def test_a_block_is_the_lead_plus_its_continuation_lines(self):
        body = dict(arch_update.release_sections(NOTES_FIXTURE))["1.0.5"]
        block = arch_update.by_hand(body)
        self.assertEqual(len(block), 2)
        self.assertTrue(block[0].startswith("**By hand:**"))
        self.assertIn("matches no release at all", block[1])

    def test_a_block_stops_at_the_blank_line(self):
        body = dict(arch_update.release_sections(NOTES_FIXTURE))["1.1.0"]
        block = arch_update.by_hand(body)
        self.assertEqual(block, ["**By hand: nothing.**"])

    def test_nothing_written_is_none_not_an_empty_block(self):
        body = dict(arch_update.release_sections(NOTES_FIXTURE))["1.0.4"]
        self.assertIsNone(arch_update.by_hand(body))

    def test_the_range_excludes_what_you_have_and_includes_what_you_want(self):
        picked = [v for v, _ in arch_update.steps_between(NOTES_FIXTURE, "1.0.4", "1.1.0")]
        self.assertEqual(picked, ["1.0.5", "1.1.0"])

    def test_the_range_is_oldest_first_whatever_the_file_order(self):
        picked = [v for v, _ in arch_update.steps_between(NOTES_FIXTURE, "1.0.0", "1.1.0")]
        self.assertEqual(picked, sorted(picked, key=arch_update.semver))

    def test_a_source_no_newer_than_the_instance_yields_nothing(self):
        self.assertEqual(arch_update.steps_between(NOTES_FIXTURE, "1.1.0", "1.1.0"), [])


class TestBeyondCopying(UpdateCase):
    """MP#82. The half of an update that a copy cannot do and nothing else reports."""

    def _versioned(self, instance_version, clone_version, notes=NOTES_FIXTURE,
                   instance_notes=None):
        src, dst = self._trees()
        _write(src, "arch/VERSION", clone_version + "\n")
        _write(dst, "arch/VERSION", instance_version + "\n")
        _write(src, arch_update.NOTES, notes)
        if instance_notes is not None:
            _write(dst, arch_update.NOTES, instance_notes)
        return src, dst

    def test_it_prints_the_by_hand_steps_for_the_releases_in_between(self):
        src, dst = self._versioned("1.0.4", "1.1.0")
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("v1.0.5", out)
        self.assertIn("move `VERSION` to `arch/VERSION`", out)
        self.assertIn("**By hand: nothing.**", out)

    def test_it_does_not_print_a_release_the_instance_already_has(self):
        src, dst = self._versioned("1.0.5", "1.1.0")
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertNotIn("move `VERSION` to `arch/VERSION`", out)

    def test_manifest_work_is_named_too_so_nothing_reads_as_nothing(self):
        # `By hand: nothing.` on a release that also says `Manifest: one optional
        # addition` would be a true line producing a false impression.
        src, dst = self._versioned("1.0.5", "1.1.0")
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("Manifest: one optional addition", out)

    def test_the_notes_are_read_from_the_clone_not_the_instance(self):
        # THE LOAD-BEARING DETAIL: the instance's copy is older than the release
        # being applied and cannot contain its note.
        stale = NOTES_FIXTURE.replace("move `VERSION` to `arch/VERSION`", "DECOY")
        src, dst = self._versioned("1.0.4", "1.1.0", instance_notes=stale)
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("move `VERSION` to `arch/VERSION`", out)
        self.assertNotIn("DECOY", out)

    def test_a_note_predating_the_convention_says_so_rather_than_nothing(self):
        # DECIDED 2026-08-14 (MP#82): the back catalogue is not backfilled, because
        # the population that would traverse it is provably zero. So silence must
        # never be rendered as "nothing to do" — they are different claims.
        src, dst = self._versioned("1.0.3", "1.0.4")
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("NO `By hand:` LINE IN v1.0.4", out)
        self.assertIn("predate the convention", out)

    def test_the_silent_releases_are_named_once_not_repeated(self):
        """Five identical paragraphs is a paragraph nobody reads."""
        src, dst = self._versioned("1.0.0", "1.1.0")
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertEqual(out.count("predate the convention"), 1)
        self.assertIn("v1.0.4", out.split("NO `By hand:` LINE IN")[1])

    def test_an_instance_with_no_version_is_told_so_not_guessed_at(self):
        src, dst = self._trees()
        _write(src, "arch/VERSION", "1.1.0\n")
        _write(src, arch_update.NOTES, NOTES_FIXTURE)
        os.remove(os.path.join(dst, "arch", "VERSION"))
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("no release number", out)

    def test_a_clone_without_notes_still_says_copying_is_not_the_whole_update(self):
        src, dst = self._versioned("1.0.4", "1.1.0")
        os.remove(os.path.join(src, arch_update.NOTES))
        out = "\n".join(arch_update.beyond_copying(src, dst))
        self.assertIn("not the whole update", out)

    def test_the_render_carries_it_in_both_modes(self):
        src, dst = self._versioned("1.0.4", "1.1.0")
        for apply in (False, True):
            report = arch_update.update(src, dst, apply=apply)
            out = "\n".join(arch_update._render(report, src, dst, apply))
            self.assertIn("BEYOND COPYING", out)

    def test_apply_still_prints_the_steps_not_an_empty_span(self):
        """THE AUDIT FIND. arch/VERSION is machinery, so --apply copies it BEFORE
        the render — and beyond_copying then read the instance's version as already
        current, printing "no releases between them" on exactly the run that just
        performed the update. The dry run showed the steps; the apply swallowed
        them. The both-modes test above passed throughout because it asserted the
        header, not the steps — a checker blind to its own failure, again."""
        src, dst = self._versioned("1.0.4", "1.1.0")
        report = arch_update.update(src, dst, apply=True)
        self.assertEqual(arch_build.read_version(dst), "1.1.0",
                         "fixture must have copied VERSION for this test to bite")
        out = "\n".join(arch_update._render(report, src, dst, True))
        self.assertIn("v1.0.4 -> v1.1.0", out)
        self.assertIn("**By hand: nothing.**", out)
        self.assertNotIn("no releases between them", out)


class TestAgainstTheRealReleaseNotes(unittest.TestCase):
    """Against the shipped file, not a fixture — the parser has to survive the real
    document, which carries prose headings, code fences and ten releases."""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), arch_update.NOTES)
        if not os.path.exists(path):
            self.skipTest("RELEASE_NOTES.md is not beside this suite")
        with open(path, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_every_release_in_the_file_parses_as_a_version(self):
        sections = arch_update.release_sections(self.text)
        self.assertTrue(sections)
        for version, _ in sections:
            self.assertIsNotNone(arch_update.semver(version))

    def test_a_pre_v1_0_5_instance_is_told_about_v1_0_5_one_way_or_the_other(self):
        # The criterion from the row: it is TOLD. With the back catalogue not
        # backfilled, being told means being pointed at that section by name —
        # never an empty report that reads as "you are done".
        steps = arch_update.steps_between(self.text, "1.0.4", "1.0.5")
        self.assertEqual([v for v, _ in steps], ["1.0.5"])


if __name__ == "__main__":
    unittest.main()
