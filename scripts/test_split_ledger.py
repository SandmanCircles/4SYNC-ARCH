#!/usr/bin/env python3
"""
Stdlib unittest suite for scripts/split_ledger.py — the one-time ledger migration.

Follows test_meter.py / test_actuals.py's pattern: no network, no third-party
deps, imports the module from the same scripts/ directory. Run either way:

  python -m unittest test_split_ledger     # from the scripts/ dir
  python scripts/test_split_ledger.py      # from the repo root

CONVERTED FROM A HAND-ROLLED HARNESS 2026-08-05 (MP#47/D6). It was the only one
of the six suites that was not unittest-based, so `pytest` collected 0 tests and
exited 5 — which reads as a failure — while its five siblings collected normally.
An adopter running one command across all six got a red result from the suite
that was passing. Two things fall out of the conversion, both wanted: the checks
are now statically countable, so rotate.py's suite check can verify the claim
instead of reporting `not statically countable` (the 35-assertions-from-32-call-
sites special case is gone — the four heading forms that shared a loop are four
methods now), and the count stays 35, so no published figure moves.

THE CASES THAT MATTER ARE THE REFUSALS. This script restructures a whole ledger
once, irreversibly; a bug that drops content has no second run to catch it. So
the suite spends most of its weight proving the script declines to run on the
shapes that would produce a silent partial migration — above all the real one it
was rewritten for: description blocks sitting ABOVE the `## Task descriptions`
heading, which the heading-bound scan skipped without a word.
"""

import io
import os
import shutil
import sys
import tempfile
import contextlib
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_ledger as S  # noqa: E402


def run(root, *argv):
    """Invoke main() in a temp root; return (exit_code, stdout+stderr)."""
    buf = io.StringIO()
    argv = ["split_ledger.py", "--dir", root] + list(argv)
    old = sys.argv
    sys.argv = argv
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            S.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        if isinstance(e.code, str):
            buf.write(e.code)
    finally:
        sys.argv = old
    return code, buf.getvalue()


LEDGER = """# Ledger

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | Done thing | — |
| 2 | ⏳ | Open thing | — |
{extra_rows}
### #1 — Done thing ✅

Body of one.

### #2 — Open thing ⏳

Body of two.
{extra}
---

*Pattern from 4SYNC ARCH — this silo is patient zero.*
"""

PLAIN = LEDGER.format(extra="", extra_rows="")


def make(tmp, extra="", extra_rows="", ledger=None):
    root = tempfile.mkdtemp(dir=tmp)
    text = ledger if ledger is not None else LEDGER.format(extra=extra, extra_rows=extra_rows)
    with open(os.path.join(root, "MERGE_PLAN.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return root


class TempRoot(unittest.TestCase):
    """Base: a scratch dir torn down after each test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


INTERLEAVED = (
    "# L\n\n## Summary table\n\n| ID | Status | Subject | B |\n|---|---|---|---|\n"
    "| 1 | ✅ | a | — |\n| 2 | ✅ | b | — |\n\n"
    "### #1 — a ✅\n\nabove the heading.\n\n"
    "## Task descriptions\n\n### #2 — b ✅\n\nbelow the heading.\n")

SUBSECTION = "### #32-original-design-context (historical)\n\nb\n"


class TestCollectDescriptions(unittest.TestCase):
    """The defect this script was rewritten for."""

    def test_collects_blocks_above_the_heading(self):
        got = S.collect_descriptions(INTERLEAVED)
        self.assertEqual(sorted(t for t, *_ in got), [1, 2])

    def test_body_captured_for_the_above_heading_block(self):
        got = S.collect_descriptions(INTERLEAVED)
        self.assertTrue(any(t == 1 and "above the heading." in b for t, _s, b, *_ in got))

    def test_heading_with_no_dash_still_parses(self):
        got = S.collect_descriptions("### #7 Subject here\n\nbody\n")
        self.assertEqual([t for t, *_ in got], [7])

    def test_trailing_status_mark_stripped_from_subject(self):
        self.assertEqual(S.collect_descriptions("### #7 — Subject ✅\n\nb\n")[0][1], "Subject")

    # The id had no word boundary after it, so a SUB-SECTION heading parsed as a
    # second description for the same task — and the refusal that followed named a
    # duplicate the ledger did not contain, in a file the operator never suspected.
    # Unfixable by inspection: `grep '^### #32'` returns one hit.
    def test_sub_section_heading_is_not_a_description(self):
        self.assertEqual(S.collect_descriptions(SUBSECTION), [])

    def test_underscored_sub_section_is_not_a_description(self):
        self.assertEqual(S.collect_descriptions("### #32_notes\n\nb\n"), [])

    def _form(self, form):
        self.assertEqual([t for t, *_ in S.collect_descriptions(form + "\n\nb\n")], [32], form)

    def test_em_dash_form_still_parses(self):
        self._form("### #32 — Subject ✅")

    def test_colon_form_still_parses(self):
        self._form("### #32: Subject")

    def test_bare_space_form_still_parses(self):
        self._form("### #32 Subject")

    def test_double_dash_form_still_parses(self):
        self._form("### #32 -- Subject")


class TestSubSectionBlastRadius(TempRoot):
    """One sub-section heading used to refuse a whole migration."""

    def test_sub_section_heading_no_longer_forges_a_duplicate(self):
        root = make(self.tmp, extra="\n### #2-original-design-context (historical)\n\nold notes.\n")
        code, out = run(root, "--dir", root)
        self.assertEqual(code, 0, out[:160])
        self.assertNotIn("TWO descriptions", out)


class TestParseTable(unittest.TestCase):

    def test_statuses_read_from_the_table_not_the_heading_emoji(self):
        self.assertEqual(S.parse_table(PLAIN), {1: "completed", 2: "pending"})

    def test_no_summary_table_returns_none(self):
        self.assertIsNone(S.parse_table("# nothing\n"))


class TestRefusals(TempRoot):
    """Each must exit non-zero and write nothing."""

    def test_duplicate_description_id_is_fatal(self):
        root = make(self.tmp, extra="\n### #2 — dupe ⏳\n\nsecond body.\n")
        code, out = run(root, "--apply")
        self.assertNotEqual(code, 0)
        self.assertIn("TWO descriptions", out)

    def test_duplicate_creates_no_tasks_dir(self):
        root = make(self.tmp, extra="\n### #2 — dupe ⏳\n\nsecond body.\n")
        run(root, "--apply")
        self.assertFalse(os.path.exists(os.path.join(root, "tasks")))

    def test_description_with_no_table_row_is_fatal(self):
        root = make(self.tmp, extra="\n### #9 — orphan ⏳\n\nno row for this.\n")
        code, out = run(root, "--apply")
        self.assertNotEqual(code, 0)
        self.assertIn("NO table row", out)

    def test_open_row_with_no_description_is_fatal(self):
        root = make(self.tmp, extra_rows="| 3 | ⏳ | Undocumented open row | — |\n")
        code, out = run(root, "--apply")
        self.assertNotEqual(code, 0)
        self.assertIn("OPEN row", out)

    def test_terminal_row_with_no_description_is_not_fatal(self):
        root = make(self.tmp, extra_rows="| 4 | ✅ | Closed, never documented | — |\n")
        code, out = run(root)
        self.assertEqual(code, 0, out[:200])
        self.assertIn("terminal rows with no description: 1", out)

    def test_ledger_with_no_final_newline_is_fatal(self):
        root = make(self.tmp, ledger=PLAIN.rstrip("\n"))
        code, out = run(root, "--apply")
        self.assertNotEqual(code, 0)
        self.assertIn("TRUNCATED", out)

    def test_no_final_newline_override_lets_it_through(self):
        root = make(self.tmp, ledger=PLAIN.rstrip("\n"))
        code, out = run(root, "--apply", "--allow-no-final-newline")
        self.assertEqual(code, 0, out[:160])


class TestDryRun(TempRoot):

    def setUp(self):
        super().setUp()
        self.root = make(self.tmp)
        self.code, self.out = run(self.root)

    def test_dry_run_exits_zero(self):
        self.assertEqual(self.code, 0, self.out[:160])

    def test_dry_run_creates_no_tasks_dir(self):
        self.assertFalse(os.path.exists(os.path.join(self.root, "tasks")))

    def test_dry_run_leaves_the_ledger_byte_identical(self):
        self.assertEqual(S.read(os.path.join(self.root, "MERGE_PLAN.md")), PLAIN)


class TestApply(TempRoot):

    def setUp(self):
        super().setUp()
        self.root = make(self.tmp)
        self.code, self.out = run(self.root, "--apply")
        self.live = os.path.join(self.root, "tasks", "MP-002.md")
        self.closed = os.path.join(self.root, "tasks", "closed", "MP-001.md")
        self.new = S.read(os.path.join(self.root, "MERGE_PLAN.md"))

    def test_apply_exits_zero(self):
        self.assertEqual(self.code, 0, self.out[:200])

    def test_terminal_rows_doc_goes_to_closed(self):
        self.assertTrue(os.path.exists(self.closed))

    def test_open_rows_doc_goes_to_tasks(self):
        self.assertTrue(os.path.exists(self.live))

    def test_doc_carries_the_body(self):
        self.assertIn("Body of two.", S.read(self.live))

    def test_doc_header_names_the_row(self):
        self.assertTrue(S.read(self.live).startswith("# MP#2 — Open thing"))

    def test_descriptions_removed_from_the_ledger(self):
        self.assertNotIn("Body of two.", self.new)

    def test_summary_table_survives(self):
        self.assertIn("| 2 | ⏳ | Open thing", self.new)

    def test_footer_survives_and_is_last(self):
        self.assertTrue(
            self.new.rstrip().endswith("*Pattern from 4SYNC ARCH — this silo is patient zero.*"),
            repr(self.new[-80:]))

    def test_no_dangling_task_descriptions_heading(self):
        self.assertNotIn("## Task descriptions", self.new)

    def test_ledger_got_smaller(self):
        self.assertLess(len(self.new), len(PLAIN))


EMPTIED = ("# L\n\n## Summary table\n\n| ID | Status | Subject | B |\n|---|---|---|---|\n"
           "| 1 | ✅ | a | — |\n\n---\n\n## Task descriptions\n\n### #1 — a ✅\n\nbody.\n")


class TestEmptiedHeading(TempRoot):
    """An emptied '## Task descriptions' heading is dropped, not left dangling."""

    def setUp(self):
        super().setUp()
        self.root = make(self.tmp, ledger=EMPTIED)
        self.code, self.out = run(self.root, "--apply")
        self.new = S.read(os.path.join(self.root, "MERGE_PLAN.md"))

    def test_heading_removed_once_emptied(self):
        self.assertEqual(self.code, 0, self.out[:200])
        self.assertNotIn("## Task descriptions", self.new)

    def test_table_still_intact(self):
        self.assertIn("| 1 | ✅ | a | — |", self.new)


if __name__ == "__main__":
    S._utf8_stdout()
    unittest.main(verbosity=2)
# ═══ EOF test_split_ledger.py ═══
