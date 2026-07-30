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
     oldest (bottom) block verbatim to the top of MERGE_PLAN_HISTORY.md. -->"""


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
        self.history = os.path.join(self.root, "MERGE_PLAN_HISTORY.md")
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


DESC_LEDGER = """# Ledger

## Session journal (recent)

2026-07-28 [agent] — a block.

---

## Summary table

| ID | Status | Subject | Blocked by |
|---|---|---|---|
| 1 | ✅ | Old closed thing | — |
| 2 | ⏳ | Still open | — |
| 3 | ✅ | Closed yesterday | — |
| 4 | ❌ | Dropped long ago | — |
| 5 | ✅ | Closed but undated | — |

## Task descriptions

### #1 — Old closed thing ✅
Completed 2020-01-01. Long historical detail that no session needs at boot.

### #2 — Still open ⏳
Open work. Mentions 2020-01-01 in passing, which must NOT archive it.

### #3 — Closed yesterday ✅
Implemented 2099-01-01 (a future date stands in for "recent"). Keep me.

### #4 — Dropped long ago ❌
Dropped 2020-02-02. Terminal via ❌, so it archives too.

### #5 — Closed but undated ✅
No date anywhere in this body at all.

## Something after
Must survive untouched.
"""


class TestDescriptionArchive(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_desc_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")
        self.archive = os.path.join(self.root, "MERGE_PLAN_ARCHIVE.md")
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(DESC_LEDGER)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _apply(self):
        return rotate.rotate_descriptions(self.ledger, self.archive, age_days=10, apply_=True)

    def test_only_terminal_and_aged_move(self):
        moved = [h for _, _, h in self._apply()]
        self.assertTrue(any("#1" in h for h in moved))
        self.assertTrue(any("#4" in h for h in moved), "❌ dropped is terminal too")
        self.assertFalse(any("#2" in h for h in moved), "open task must never archive")
        self.assertFalse(any("#3" in h for h in moved), "recently closed must stay")
        self.assertFalse(any("#5" in h for h in moved), "undated must be skipped, not guessed")

    def test_open_task_with_an_old_date_is_not_archived(self):
        """The regression that would hurt most: a live task mentioning an old date."""
        self._apply()
        with open(self.ledger, encoding="utf-8") as fh:
            after = fh.read()
        self.assertIn("### #2 — Still open", after)

    def test_summary_table_rows_never_move(self):
        self._apply()
        with open(self.ledger, encoding="utf-8") as fh:
            after = fh.read()
        for row in ("| 1 |", "| 2 |", "| 3 |", "| 4 |", "| 5 |"):
            self.assertIn(row, after, "the table is canonical — rows stay")

    def test_moved_text_lands_in_archive_verbatim(self):
        self._apply()
        with open(self.archive, encoding="utf-8") as fh:
            arch = fh.read()
        self.assertIn("Long historical detail", arch)
        self.assertIn("Dropped 2020-02-02", arch)

    def test_surrounding_sections_survive(self):
        self._apply()
        with open(self.ledger, encoding="utf-8") as fh:
            after = fh.read()
        self.assertIn("## Something after", after)
        self.assertIn("Must survive untouched", after)
        self.assertIn("## Session journal (recent)", after)

    def test_dry_run_writes_nothing(self):
        rotate.rotate_descriptions(self.ledger, self.archive, age_days=10, apply_=False)
        with open(self.ledger, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), DESC_LEDGER)
        self.assertFalse(os.path.exists(self.archive))

    def test_idempotent(self):
        self._apply()
        second = self._apply()
        self.assertEqual(second, [], "nothing left to move on a second pass")

    def test_close_date_prefers_the_completion_verb(self):
        block = "### #9 — x ✅\nCompleted 2026-07-20. Supersedes the 2019-01-01 design.\n"
        self.assertEqual(rotate.description_close_date(block).strftime("%Y-%m-%d"), "2026-07-20")

    def test_close_date_none_when_absent(self):
        self.assertIsNone(rotate.description_close_date("### #9 — x ✅\nno dates here\n"))

    def test_split_ignores_headings_outside_the_section(self):
        blocks = rotate.split_descriptions(DESC_LEDGER)
        self.assertEqual(len(blocks), 5)

    def test_lands_under_the_section_heading_not_after_the_footer(self):
        """An archive with a footer must not have entries appended past it."""
        with open(self.archive, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Archive\n\nBlurb.\n\n## Task descriptions (archived)\n\n"
                     "---\n\n*Footer line — must stay last.*\n")
        self._apply()
        with open(self.archive, encoding="utf-8") as fh:
            arch = fh.read()
        self.assertLess(arch.index("### #1 —"), arch.index("*Footer line"),
                        "entries must sit above the footer")
        self.assertLess(arch.index("## Task descriptions (archived)"), arch.index("### #1 —"))

    def test_appends_when_no_section_heading_exists(self):
        with open(self.archive, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Archive\n\nNo section heading here.\n")
        self._apply()
        with open(self.archive, encoding="utf-8") as fh:
            self.assertIn("### #1 —", fh.read())


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_rotate.py ═══
