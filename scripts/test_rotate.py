#!/usr/bin/env python3
"""
Stdlib unittest suite for rotate.py — ledger rotation.

Mirrors test_meter.py's shape: no network, no third-party deps, imports rotate
from the same scripts/ directory. Run either way:

  python -m unittest test_rotate           # from the scripts/ dir
  python scripts/test_rotate.py            # from the repo root

Focus: split_journal's block accounting. The KEEP-N instruction comment that ships
at the top of the journal section is an instruction, not an entry — counting it
made a section holding exactly `keep` real blocks look over-cap, so every close
rotated one legitimate block out. The comment must also SURVIVE a rotation, since
rotate_journal rebuilds the ledger from `before + blocks`.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotate  # noqa: E402


KEEP_COMMENT = """<!-- KEEP-5 RULE: newest-first, blank-line-separated blocks, cap = 5.
     At session close: PREPEND your new block here. If that makes 6 blocks, move the
     oldest (bottom) block verbatim to the top of JOURNAL_HISTORY.md. -->"""


def ledger(blocks, comment=True, trailer="\n---\n\n## Summary table\n"):
    """Build a ledger with `blocks` dated journal entries, optionally preceded by
    the KEEP-N instruction comment, and closed by a section-ending rule."""
    body = "\n".join("2026-07-%02d [agent] — entry %d body text." % (10 + i, i) + "\n"
                     for i in range(len(blocks)))
    parts = ["# Ledger\n", "\n## Session journal (recent)\n", "\n"]
    if comment:
        parts.append(KEEP_COMMENT + "\n\n")
    parts.append(body)
    parts.append(trailer)
    return "".join(parts)


class TestSplitJournalCounting(unittest.TestCase):
    def test_comment_is_not_counted_as_a_block(self):
        text = ledger(range(5), comment=True)
        before, blocks, after = rotate.split_journal(text)
        self.assertEqual(len(blocks), 5, "the KEEP-N comment must not count as an entry")

    def test_same_count_with_and_without_the_comment(self):
        with_c = rotate.split_journal(ledger(range(3), comment=True))[1]
        without = rotate.split_journal(ledger(range(3), comment=False))[1]
        self.assertEqual(len(with_c), len(without))

    def test_comment_moves_into_before_not_into_the_void(self):
        before, blocks, after = rotate.split_journal(ledger(range(2), comment=True))
        self.assertIn("KEEP-5 RULE", before)
        self.assertFalse(any("KEEP-5 RULE" in b for b in blocks))

    def test_blocks_are_newest_first(self):
        _, blocks, _ = rotate.split_journal(ledger(range(3), comment=True))
        self.assertTrue(blocks[0].strip().startswith("2026-07-10"))

    def test_missing_section_returns_none(self):
        self.assertIsNone(rotate.split_journal("# Ledger\n\nno journal here\n"))


class TestRotateJournalBehaviour(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_test_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")
        self.history = os.path.join(self.root, "JOURNAL_HISTORY.md")
        with open(self.history, "w", encoding="utf-8", newline="") as fh:
            fh.write("# History\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def test_exactly_at_cap_moves_nothing(self):
        """The regression this suite exists for: 5 real blocks + the comment used to
        read as 6 and rotate a legitimate entry out."""
        self._write(ledger(range(5), comment=True))
        moved = rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        self.assertEqual(moved, [])

    def test_over_cap_moves_only_the_overflow(self):
        self._write(ledger(range(6), comment=True))
        moved = rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        self.assertEqual(len(moved), 1)
        self.assertIn("entry 5", moved[0])

    def test_comment_survives_an_applied_rotation(self):
        self._write(ledger(range(6), comment=True))
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        with open(self.ledger, encoding="utf-8") as fh:
            after = fh.read()
        self.assertIn("KEEP-5 RULE", after)
        self.assertIn("## Summary table", after)

    def test_dry_run_writes_nothing(self):
        text = ledger(range(6), comment=True)
        self._write(text)
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=False)
        with open(self.ledger, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text)

    def _structured_history(self):
        """The shipped history file: title, prose, a how-to section, an entries
        section, and a footer. Not an append log."""
        with open(self.history, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Project — Session Journal History\n\n"
                     "Explanatory prose that must stay directly under the title.\n\n"
                     "---\n\n## When to roll an entry here\n\nRules.\n\n---\n\n"
                     + rotate.HIST_SECTION + "\n\n"
                     "2026-01-01 [older] — a pre-existing entry.\n\n"
                     "---\n\n*Footer line — must stay last.*\n")

    def test_moved_entry_lands_under_the_entries_section(self):
        """The regression: entries were inserted after line 1, burying them above
        the file's own explanation."""
        self._structured_history()
        self._write(ledger(range(6), comment=True))
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        h = open(self.history, encoding="utf-8").read()
        self.assertLess(h.index("Explanatory prose"), h.index("entry 5"),
                        "prose must stay above the entries")
        self.assertLess(h.index(rotate.HIST_SECTION), h.index("entry 5"))

    def test_moved_entry_stays_above_the_footer(self):
        self._structured_history()
        self._write(ledger(range(6), comment=True))
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        h = open(self.history, encoding="utf-8").read()
        self.assertLess(h.index("entry 5"), h.index("*Footer line"))

    def test_newest_entry_lands_above_older_ones(self):
        self._structured_history()
        self._write(ledger(range(6), comment=True))
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        h = open(self.history, encoding="utf-8").read()
        self.assertLess(h.index("entry 5"), h.index("2026-01-01 [older]"),
                        "history is newest-first")

    def test_history_without_the_section_falls_back_to_append(self):
        with open(self.history, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Bare history\n\nNo entries section here.\n")
        self._write(ledger(range(6), comment=True))
        rotate.rotate_journal(self.ledger, self.history, keep=5, apply_=True)
        self.assertIn("entry 5", open(self.history, encoding="utf-8").read())


TASK_LEDGER = """# Ledger

## Session journal (recent)

2026-07-30 [agent] — a block.

---

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | Closed thing | — |
| 2 | ⏳ | Still open | — |
| 4 | ❌ | Dropped thing | — |
| 27 | 🔄 | In progress, two digits | — |

## Something after
Must survive untouched.
"""


class TestTaskDocs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_tasks_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(TASK_LEDGER)
        self.tasks = os.path.join(self.root, "tasks")
        self.closed = os.path.join(self.tasks, "closed")
        os.makedirs(self.closed)
        for tid in (1, 2, 4, 27):
            self._doc(tid, f"body of task {tid}")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _doc(self, tid, body, closed=False):
        d = self.closed if closed else self.tasks
        p = os.path.join(d, rotate.doc_name(tid))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"# MP#{tid} — subject\n\n{body}\n")
        return p

    def _apply(self):
        return rotate.rotate_task_docs(self.root, self.ledger, apply_=True)

    def test_doc_name_is_zero_padded(self):
        self.assertEqual(rotate.doc_name(1), "MP-001.md")
        self.assertEqual(rotate.doc_name(27), "MP-027.md")
        self.assertEqual(rotate.doc_name(117), "MP-117.md")

    def test_terminal_rows_move_and_open_rows_stay(self):
        moved, missing = self._apply()
        ids = sorted(t for t, _, _, _ in moved)
        self.assertEqual(ids, [1, 4], "✅ and ❌ are terminal; ⏳ and 🔄 are not")
        self.assertEqual(missing, [])
        self.assertTrue(os.path.exists(os.path.join(self.closed, "MP-001.md")))
        self.assertTrue(os.path.exists(os.path.join(self.closed, "MP-004.md")))
        self.assertTrue(os.path.exists(os.path.join(self.tasks, "MP-002.md")))
        self.assertTrue(os.path.exists(os.path.join(self.tasks, "MP-027.md")))

    def test_source_is_removed_and_content_is_verbatim(self):
        self._apply()
        self.assertFalse(os.path.exists(os.path.join(self.tasks, "MP-001.md")))
        with open(os.path.join(self.closed, "MP-001.md"), encoding="utf-8") as fh:
            self.assertIn("body of task 1", fh.read())

    def test_open_row_with_no_document_is_reported(self):
        """The pointer-integrity gate: an open row with no doc is unexecutable."""
        os.remove(os.path.join(self.tasks, "MP-002.md"))
        _, missing = self._apply()
        self.assertEqual([t for t, _, _ in missing], [2])
        self.assertFalse(missing[0][2], "not in closed/ either — genuinely absent")

    def test_reopened_task_is_reported_distinctly(self):
        """Row flipped back to open while its doc still sits in closed/."""
        os.remove(os.path.join(self.tasks, "MP-002.md"))
        self._doc(2, "reopened body", closed=True)
        _, missing = self._apply()
        self.assertEqual([t for t, _, _ in missing], [2])
        self.assertTrue(missing[0][2], "must flag that the doc is in closed/, not missing")

    def test_status_comes_from_the_table_not_the_document(self):
        """The source-of-truth split. A doc claiming it is closed changes nothing;
        only the table row decides."""
        self._doc(2, "This task is ✅ completed and done, honestly.")
        moved, _ = self._apply()
        self.assertNotIn(2, [t for t, _, _, _ in moved])

    def test_dry_run_moves_nothing(self):
        rotate.rotate_task_docs(self.root, self.ledger, apply_=False)
        self.assertTrue(os.path.exists(os.path.join(self.tasks, "MP-001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.closed, "MP-001.md")))

    def test_idempotent(self):
        self._apply()
        moved, missing = self._apply()
        self.assertEqual(moved, [])
        self.assertEqual(missing, [])

    def test_ledger_is_never_modified(self):
        """This pass moves files. It must not touch the ledger at all."""
        self._apply()
        with open(self.ledger, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), TASK_LEDGER)

    def test_missing_summary_table_is_skipped_not_guessed(self):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Ledger\n\nno table here\n")
        self.assertEqual(rotate.rotate_task_docs(self.root, self.ledger, apply_=True), ([], []))
        self.assertTrue(os.path.exists(os.path.join(self.tasks, "MP-001.md")))

    def test_parse_table_ignores_rows_outside_the_table(self):
        rows = rotate.parse_summary_table(TASK_LEDGER)
        self.assertEqual(sorted(rows), [1, 2, 4, 27])


class TestSizeReport(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_size_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def test_reports_total_and_journal_bytes(self):
        self._write(ledger(range(3), comment=True))
        total, jbytes = rotate.report_sizes(self.root, self.ledger, journal_max=999999)
        self.assertGreater(total, jbytes)
        self.assertGreater(jbytes, 0)

    def test_journal_bytes_exclude_the_keep_comment(self):
        """The comment is an instruction; charging the journal for it would make
        the cap fire on prose the session did not write."""
        _, with_c = rotate.report_sizes(self.root, self._w(ledger(range(3), comment=True)), 999999)
        _, without = rotate.report_sizes(self.root, self._w(ledger(range(3), comment=False)), 999999)
        self.assertEqual(with_c, without)

    def _w(self, text):
        self._write(text)
        return self.ledger

    def test_over_cap_is_flagged_but_does_not_raise(self):
        self._write(ledger(range(5), comment=True))
        total, jbytes = rotate.report_sizes(self.root, self.ledger, journal_max=1)
        self.assertGreater(jbytes, 1)   # reports; never blocks

    def test_manifest_max_bytes_is_read(self):
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("close:\n  journal:\n    keep: 5\n    max_bytes: 4096\n")
        self.assertEqual(rotate.manifest_journal_max(self.root), 4096)

    def test_manifest_absent_falls_back_to_default(self):
        self.assertEqual(rotate.manifest_journal_max(self.root), rotate.JOURNAL_MAX_DEFAULT)


SUBJ_LEDGER = """# Ledger

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | Short label | — |
| 2 | ⏳ | {long} | — |

---

*footer*
"""


class TestSubjectReport(unittest.TestCase):
    """MP#29(a): once long form moved to tasks/, the TABLE became the boot cost
    and nothing bounded it. These prove the number gets reported and that it
    never blocks — a session mid-write must still be able to record a row."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_subj_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, long_subject):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(SUBJ_LEDGER.format(long=long_subject))
        return self.ledger

    def test_flags_only_the_over_cap_row(self):
        self._write("x" * 300)
        subjects, over = rotate.report_subjects(self.ledger, subject_max=120)
        self.assertEqual(sorted(subjects), [1, 2])
        self.assertEqual([tid for _n, tid in over], [2])

    def test_within_cap_reports_nothing(self):
        self._write("still a label")
        _subjects, over = rotate.report_subjects(self.ledger, subject_max=120)
        self.assertEqual(over, [])

    def test_boundary_is_exclusive(self):
        """Exactly `subject_max` is fine; one more is not — otherwise the cap
        reported in the message and the cap enforced would differ by one."""
        self._write("y" * 120)
        self.assertEqual(rotate.report_subjects(self.ledger, 120)[1], [])
        self._write("y" * 121)
        self.assertEqual(len(rotate.report_subjects(self.ledger, 121 - 1)[1]), 1)

    def test_reports_but_never_raises(self):
        self._write("z" * 5000)
        subjects, over = rotate.report_subjects(self.ledger, subject_max=10)
        self.assertEqual(len(over), 2)          # both rows now over
        self.assertTrue(all(isinstance(s, str) for s in subjects.values()))

    def test_missing_table_is_skipped_not_fatal(self):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Ledger\n\nno table here.\n")
        self.assertEqual(rotate.report_subjects(self.ledger, 120), ({}, []))

    def test_table_bound_stops_at_description_headings(self):
        """An unmigrated ledger keeps '### #NNN' blocks in the summary-table
        section. A '## '-only bound scans them for '| N |' rows and can invent a
        row that does not exist — after which rotate.py fails every close
        demanding a document for it."""
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write("# L\n\n## Summary table\n\n| ID | Status | Subject | B |\n"
                     "|---|---|---|---|\n| 1 | ✅ | real row | — |\n\n"
                     "### #1 — real row ✅\n\nA quoted table follows:\n\n"
                     "| 999 | ✅ | phantom row from a description | — |\n")
        subjects, _over = rotate.report_subjects(self.ledger, 120)
        self.assertEqual(sorted(subjects), [1], "phantom row leaked past the bound")
        self.assertEqual(sorted(rotate.parse_summary_table(rotate.read(self.ledger))), [1])


FINDINGS_DOC = """\
# Findings

## Protocol — read before adding an entry

Rules live here.

### Entry format

```
### title
Trigger: when
Exit: how it leaves
```

## Findings

### Good entry
Trigger: something greppable happens
The finding.
Exit: hook rule

### Bad entry
No trigger line at all, so nothing can ever grep for it.
Exit: retire
"""


class TestFindingsReport(unittest.TestCase):
    """MP#28: the file earns its seventh-surface place only if the rules are
    enforced rather than aspirational. This is that enforcement."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_find_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text, name=rotate.FINDINGS_FILENAME):
        with open(os.path.join(self.root, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def test_absent_file_is_not_an_error(self):
        self.assertEqual(rotate.report_findings(self.root), (0, []))

    def test_flags_only_the_entry_missing_a_trigger(self):
        self._write(FINDINGS_DOC)
        nbytes, missing = rotate.report_findings(self.root)
        self.assertGreater(nbytes, 0)
        self.assertEqual(missing, ["Bad entry"])

    def test_protocol_section_is_not_scanned(self):
        """`### Entry format` documents the format using the same markup. Counting
        it as a trigger-less finding is a false positive, and a reporting-only
        check does not survive training people to ignore it."""
        self._write(FINDINGS_DOC)
        _n, missing = rotate.report_findings(self.root)
        self.assertNotIn("Entry format", missing)

    def test_all_entries_with_triggers_report_clean(self):
        self._write("# F\n\n## Findings\n\n### A\nTrigger: x\nbody\nExit: retire\n")
        self.assertEqual(rotate.report_findings(self.root)[1], [])

    def test_missing_findings_heading_scans_whole_file(self):
        """A file that has not grown a `## Findings` section yet must still be
        checked, not silently skipped."""
        self._write("# F\n\n### A\nno trigger here\n")
        self.assertEqual(rotate.report_findings(self.root)[1], ["A"])


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_rotate.py ═══
