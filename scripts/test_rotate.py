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

import builtins
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotate  # noqa: E402


KEEP_COMMENT = """<!-- KEEP-5 RULE: newest-first, blank-line-separated blocks, cap = 5.
     At session close: PREPEND your new block here. If that makes 6 blocks, move the
     oldest (bottom) block verbatim to the top of JOURNAL_HISTORY.md. -->"""


class ManifestEnvCase(unittest.TestCase):
    """Pin ARCH_MANIFEST to the fixture's own manifest name for the whole test.

    These fixtures write a manifest literally named `4SYNC.yaml`, then call code
    that resolves `os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml"`. Inheriting an
    ambient value aims that lookup at a file the fixture never wrote, so the reader
    finds nothing and returns its default.

    The bite: MP#20 tells adopters to rename their manifest off the colliding
    `4SYNC.yaml`, which sets exactly this variable — so every adopter who followed
    the product's own advice broke their suite, with no way to tell those failures
    from real ones. A test must not depend on the environment it happens to run in."""

    MANIFEST_NAME = "4SYNC.yaml"

    def setUp(self):
        super().setUp()
        prev = os.environ.get("ARCH_MANIFEST")
        os.environ["ARCH_MANIFEST"] = self.MANIFEST_NAME

        def restore():
            if prev is None:
                os.environ.pop("ARCH_MANIFEST", None)
            else:
                os.environ["ARCH_MANIFEST"] = prev

        self.addCleanup(restore)


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


class TestSizeReport(ManifestEnvCase):
    def setUp(self):
        super().setUp()
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

    def test_manifest_env_is_pinned_to_the_fixture(self):
        """Locks the isolation itself: drop ManifestEnvCase and this fails loudly
        instead of the whole suite failing only on machines that set the var."""
        self.assertEqual(os.environ.get("ARCH_MANIFEST"), "4SYNC.yaml")

    def test_manifest_max_bytes_is_read(self):
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("close:\n  journal:\n    keep: 5\n    max_bytes: 4096\n")
        self.assertEqual(rotate.manifest_journal_max(self.root), 4096)

    def test_manifest_absent_falls_back_to_default(self):
        self.assertEqual(rotate.manifest_journal_max(self.root), rotate.JOURNAL_MAX_DEFAULT)


OVERFLOW_MANIFEST = """\
sync_version: "1.0"

close:
  journal:
    file: MERGE_PLAN.md
    keep: 5
    max_bytes: 16384
    overflow_to: MERGE_PLAN_HISTORY.md
"""


class TestJournalOverflowTarget(ManifestEnvCase):
    """The overflow target was HARDCODED to JOURNAL_HISTORY.md while the manifest
    declared close.journal.overflow_to — so an instance with its own history file
    had rotation quietly scatter journal blocks into a second file it never
    declared, and nothing errored. A manifest key that is parsed but not obeyed is
    worse than an absent one, because it is trusted."""

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="rotate_overflow_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _manifest(self, text, name="4SYNC.yaml"):
        with open(os.path.join(self.root, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def test_declared_overflow_target_is_read(self):
        self._manifest(OVERFLOW_MANIFEST)
        self.assertEqual(rotate.manifest_journal_overflow(self.root), "MERGE_PLAN_HISTORY.md")

    def test_manifest_absent_falls_back_to_default(self):
        self.assertEqual(rotate.manifest_journal_overflow(self.root),
                         rotate.JOURNAL_HISTORY_DEFAULT)

    def test_declaration_is_read_without_pyyaml(self):
        """rotate.py must run on a bare interpreter with no third-party packages —
        its sibling manifest_journal_max has a regex fallback for exactly that, and
        a yaml-only reader would re-create this defect wherever PyYAML is absent."""
        self._manifest(OVERFLOW_MANIFEST)
        real_import = builtins.__import__

        def no_yaml(name, *a, **k):
            if name == "yaml":
                raise ImportError("PyYAML not installed")
            return real_import(name, *a, **k)

        with mock.patch.object(builtins, "__import__", no_yaml):
            self.assertEqual(rotate.manifest_journal_overflow(self.root),
                             "MERGE_PLAN_HISTORY.md")

    def test_overflow_target_cannot_escape_the_instance_root(self):
        """The manifest names a sibling file, never a path. Rotation writes verbatim
        blocks of session history; a declaration like `../elsewhere.md` must not
        steer that write outside the instance."""
        self._manifest(OVERFLOW_MANIFEST.replace("MERGE_PLAN_HISTORY.md",
                                                 "../../escaped.md"))
        self.assertEqual(rotate.manifest_journal_overflow(self.root), "escaped.md")

    def test_end_to_end_rotation_lands_in_the_declared_file(self):
        """The resolver being right is not enough — main() held the literal."""
        self._manifest(OVERFLOW_MANIFEST)
        led = os.path.join(self.root, "MERGE_PLAN.md")
        with open(led, "w", encoding="utf-8", newline="") as fh:
            fh.write(ledger(range(6), comment=True))
        declared = os.path.join(self.root, "MERGE_PLAN_HISTORY.md")
        with open(declared, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# History\n")

        subprocess.run([sys.executable, os.path.abspath(rotate.__file__),
                        "--dir", self.root, "--keep", "5", "--apply", "--allow-dirty"],
                       capture_output=True, text=True, check=False)

        with open(declared, encoding="utf-8") as fh:
            self.assertIn("2026-07-15", fh.read())          # the oldest block, rotated out
        self.assertFalse(os.path.exists(os.path.join(self.root, "JOURNAL_HISTORY.md")),
                         "rotation created a history file the manifest never declared")


def fat_ledger(sizes, comment=True):
    """A ledger whose journal blocks have controlled byte sizes, newest-first.

    The stock `ledger()` builds uniform tiny blocks, which cannot express the
    defect this fixture exists for: a journal that obeys KEEP-5 exactly and is
    still 70% over its byte cap, because the cap and the count measure different
    things."""
    body = ""
    for i, n in enumerate(sizes):
        head = "2026-07-%02d [agent] — entry %d " % (10 + i, i)
        body += head + "x" * max(0, n - len(head)) + "\n\n"
    parts = ["# Ledger\n", "\n## Session journal (recent)\n", "\n"]
    if comment:
        parts.append(KEEP_COMMENT + "\n\n")
    parts.append(body)
    parts.append("\n---\n\n## Summary table\n")
    return "".join(parts)


class TestJournalSizeCap(unittest.TestCase):
    """The size cap MOVES blocks; it no longer only counts them.

    Measured on the silo that prompted this: 5 blocks — KEEP-5 exactly obeyed,
    so the count rule had nothing to move — totalling 27,764 B against a 16,384 B
    cap. Three consecutive external reviews reported the number and none of them
    could act on it, because the only remedy on offer was a hand-trim of prose
    out of past entries. A cap nobody can enforce mechanically is a cap that gets
    raised; this suite is the argument that it moves instead."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_size_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")
        self.history = os.path.join(self.root, "JOURNAL_HISTORY.md")
        with open(self.history, "w", encoding="utf-8", newline="") as fh:
            fh.write("# History\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, sizes):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(fat_ledger(sizes))

    def _journal_now(self):
        with open(self.ledger, encoding="utf-8") as fh:
            _, blocks, _ = rotate.split_journal(fh.read())
        return rotate.journal_bytes(blocks)

    def test_under_both_caps_moves_nothing(self):
        self._write([100, 100, 100])
        moved = rotate.rotate_journal(self.ledger, self.history, 5, True, 16384)
        self.assertEqual(moved, [])

    def test_at_count_cap_but_over_size_cap_still_moves(self):
        """The exact shape of the real defect: nothing to move by count, 70% over
        by size. Before this, rotate_journal returned 'nothing to move'."""
        self._write([3000, 3000, 3000, 3000, 3000])
        moved = rotate.rotate_journal(self.ledger, self.history, 5, True, 8000)
        self.assertTrue(moved, "count cap satisfied, size cap ignored — the defect")
        self.assertLessEqual(self._journal_now(), 8000)

    def test_moves_the_oldest_first_and_keeps_the_newest(self):
        self._write([1000, 1000, 1000, 5000])
        rotate.rotate_journal(self.ledger, self.history, 5, True, 3000)
        with open(self.ledger, encoding="utf-8") as fh:
            kept = fh.read()
        self.assertIn("entry 0", kept, "newest block must survive")
        self.assertNotIn("entry 3", kept, "oldest block must go first")

    def test_newest_block_is_never_moved_even_if_it_alone_exceeds_the_cap(self):
        """The floor. A cap that can empty the journal takes the previous
        session's handoff with it — the one block a booting session most needs."""
        self._write([9000, 500, 500])
        rotate.rotate_journal(self.ledger, self.history, 5, True, 1000)
        with open(self.ledger, encoding="utf-8") as fh:
            kept = fh.read()
        self.assertIn("entry 0", kept)
        self.assertGreater(self._journal_now(), 1000, "still over — and that is correct")

    def test_over_cap_by_one_block_is_reported_not_silently_accepted(self):
        self._write([9000, 500])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rotate.rotate_journal(self.ledger, self.history, 5, True, 1000)
        self.assertIn("STILL OVER", buf.getvalue())

    def test_size_moved_blocks_land_verbatim_and_leave_the_ledger(self):
        """The write-back bug this refactor could have shipped: rebuilding the
        ledger from blocks[:keep] instead of the size-trimmed list would leave a
        moved block in BOTH files."""
        self._write([1000, 1000, 1000, 1000])
        rotate.rotate_journal(self.ledger, self.history, 5, True, 2500)
        led = open(self.ledger, encoding="utf-8").read()
        hist = open(self.history, encoding="utf-8").read()
        for tag in ("entry 2", "entry 3"):
            self.assertIn(tag, hist)
            self.assertNotIn(tag, led, "block is in history AND still in the ledger")

    def test_size_moved_block_lands_above_the_older_count_moved_ones(self):
        """Newest-first must survive a move driven by two different rules at once."""
        self._write([1000, 1000, 1000, 1000, 1000, 1000])
        rotate.rotate_journal(self.ledger, self.history, 5, True, 2500)
        hist = open(self.history, encoding="utf-8").read()
        self.assertLess(hist.index("entry 2"), hist.index("entry 5"))

    def test_no_journal_max_preserves_pure_count_behaviour(self):
        """Back-compat: every existing caller passes four arguments."""
        self._write([9000, 9000, 9000])
        moved = rotate.rotate_journal(self.ledger, self.history, 5, True)
        self.assertEqual(moved, [])

    def test_dry_run_writes_nothing_when_the_move_is_size_driven(self):
        self._write([3000, 3000, 3000])
        text = open(self.ledger, encoding="utf-8").read()
        moved = rotate.rotate_journal(self.ledger, self.history, 5, False, 4000)
        self.assertTrue(moved)
        self.assertEqual(open(self.ledger, encoding="utf-8").read(), text)


PROSE_LEDGER = """# Ledger

## Summary table

| ID | Status | Subject |
|---|---|---|
| 1 | ✅ | Short label |
| 2 | ⏳ | Another label |

{prose}

---

*footer*
"""


class TestTableProseReport(unittest.TestCase):
    """The third place the growth went. Descriptions were capped and moved to
    tasks/; row cells were capped after them; the paragraphs AROUND the table
    were watched by nothing, and that is where the next 7,880 B accumulated —
    every byte duplicating a tasks/closed/ document that already held it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_prose_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, prose):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(PROSE_LEDGER.format(prose=prose))

    def _run(self, prose):
        self._write(prose)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rows, pr = rotate.report_table_prose(self.ledger)
        return rows, pr, buf.getvalue()

    def test_prose_outweighing_rows_is_flagged(self):
        rows, pr, out = self._run("**Tally:** " + "narrative " * 200)
        self.assertGreater(pr, rows)
        self.assertIn("prose outweighs", out)

    def test_a_short_tally_is_clean(self):
        rows, pr, out = self._run("**Tally:** 2 tasks. 1 completed, 1 pending.")
        self.assertLess(pr, rows)
        self.assertNotIn("prose outweighs", out)

    def test_rows_and_prose_are_counted_separately(self):
        rows, pr, _ = self._run("Short note.")
        self.assertGreater(rows, 0)
        self.assertGreater(pr, 0)

    def test_missing_table_is_skipped_not_fatal(self):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Ledger\n\nno table here\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rows, pr = rotate.report_table_prose(self.ledger)
        self.assertEqual((rows, pr), (0, 0))

    def test_reports_but_never_raises(self):
        """Same contract as every other report here: a session mid-write must
        never be stopped by a measurement."""
        self._write("**Tally:** " + "narrative " * 500)
        rotate.report_table_prose(self.ledger)


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


TALLY_LEDGER = """# Ledger

## Session journal (recent)

2026-08-04 [agent] — closed some rows. **Tally:** 999 tasks total — all of them.

---

## Summary table

| ID | Status | Subject | Blocked by | Owner |
|---|---|---|---|---|
| 1 | ✅ | done thing | — | — |
| 2 | ⏳ | open thing | — | — |
| 3 | ❌ | dropped thing | — | — |
{extra}
{tally}

**Some other paragraph.** Leave me alone.

---

*footer*
"""


class TestTallyReconcile(unittest.TestCase):
    """MP#39. The Tally was rewritten by hand on 2026-08-04 and was wrong within
    the hour, because the same session closed four more rows afterwards. These
    lock the two halves of the fix: the count comes from the TABLE, and on
    --apply the line stops being something a human maintains."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_tally_")
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, tally="", extra=""):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(TALLY_LEDGER.format(tally=tally, extra=extra))

    def _run(self, apply_=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            counts, changed = rotate.reconcile_tally(self.ledger, apply_)
        return counts, changed, buf.getvalue()

    def _text(self):
        with open(self.ledger, encoding="utf-8") as fh:
            return fh.read()

    # ── counting ────────────────────────────────────────────────────────────
    def test_counts_come_from_the_table(self):
        self._write(tally="**Tally:** 3 tasks total — 1 completed, 0 in_progress, "
                          "1 pending, 0 blocked, 1 dropped.")
        counts, _, _ = self._run()
        self.assertEqual(counts, {"completed": 1, "in_progress": 0, "pending": 1,
                                  "blocked": 0, "dropped": 1})

    def test_variation_selector_does_not_split_a_status(self):
        """⏸️ is U+23F8 U+FE0F but a ledger may write the bare U+23F8. Two
        renderings of one status must not produce two different counts."""
        self._write(extra="| 4 | ⏸️ | with selector | — | — |\n"
                          "| 5 | ⏸ | without selector | — | — |")
        counts, _, _ = self._run()
        self.assertEqual(counts["blocked"], 2)

    def test_rendered_sentence_matches_the_shipped_format(self):
        counts = {"completed": 33, "in_progress": 0, "pending": 4,
                  "blocked": 0, "dropped": 1}
        self.assertEqual(
            rotate.render_tally(counts),
            "**Tally:** 38 tasks total — 33 completed, 0 in_progress, "
            "4 pending, 0 blocked, 1 dropped.")

    # ── drift ───────────────────────────────────────────────────────────────
    def test_drift_is_reported_and_not_written_on_dry_run(self):
        stale = "**Tally:** 99 tasks total — 99 completed, 0 in_progress, 0 pending, 0 blocked, 0 dropped."
        self._write(tally=stale)
        _, changed, out = self._run(apply_=False)
        self.assertFalse(changed)
        self.assertIn("disagrees with the table", out)
        self.assertIn(stale, self._text())          # untouched

    def test_apply_rewrites_the_line(self):
        self._write(tally="**Tally:** 99 tasks total — 99 completed, 0 in_progress, "
                          "0 pending, 0 blocked, 0 dropped.")
        _, changed, out = self._run(apply_=True)
        self.assertTrue(changed)
        self.assertIn("**Tally:** 3 tasks total — 1 completed, 0 in_progress, "
                      "1 pending, 0 blocked, 1 dropped.", self._text())
        # Scoped to the table section deliberately: the fixture's journal block
        # says "999 tasks total", which CONTAINS "99 tasks total" as a substring
        # — a whole-file assertion here passes or fails on the fixture's digits
        # rather than on the behaviour.
        self.assertNotIn("99 tasks total", self._text().split("## Summary table")[1])

    def test_apply_changes_nothing_but_the_tally_line(self):
        """The ledger's other prose is explicitly not this pass's business —
        MERGE_PLAN.md's own note says 'leave this paragraph alone'."""
        self._write(tally="**Tally:** 99 tasks total — 99 completed, 0 in_progress, "
                          "0 pending, 0 blocked, 0 dropped.")
        before = self._text()
        self._run(apply_=True)
        after = self._text()
        drop = lambda t: [l for l in t.splitlines() if not l.startswith("**Tally:**")]
        self.assertEqual(drop(before), drop(after))

    def test_a_correct_tally_is_left_alone(self):
        good = ("**Tally:** 3 tasks total — 1 completed, 0 in_progress, "
                "1 pending, 0 blocked, 1 dropped.")
        self._write(tally=good)
        _, changed, out = self._run(apply_=True)
        self.assertFalse(changed)
        self.assertIn("matches the table", out)
        self.assertEqual(self._text().count(good), 1)

    # ── refusals ────────────────────────────────────────────────────────────
    def test_unrecognised_status_blocks_the_rewrite(self):
        """A total that silently omits a row is worse than a stale one: it looks
        authoritative. Report it and leave the line alone, even on --apply."""
        stale = "**Tally:** 99 tasks total — 99 completed, 0 in_progress, 0 pending, 0 blocked, 0 dropped."
        self._write(tally=stale, extra="| 4 | ??? | typo'd status | — | — |")
        counts, changed, out = self._run(apply_=True)
        self.assertFalse(changed)
        self.assertEqual(sum(counts.values()), 3)   # the good rows only
        self.assertIn("no recognised status mark", out)
        self.assertIn("row #4", out)
        self.assertIn(stale, self._text())          # untouched

    def test_a_tally_outside_the_table_section_is_never_touched(self):
        """The fixture's journal block contains a '**Tally:** 999 …' sentence.
        Anchoring on the first match in the file would rewrite a past session's
        journal entry — a verbatim record this project does not edit."""
        self._write(tally="**Tally:** 99 tasks total — 99 completed, 0 in_progress, "
                          "0 pending, 0 blocked, 0 dropped.")
        self._run(apply_=True)
        self.assertIn("**Tally:** 999 tasks total — all of them.", self._text())

    def test_no_tally_line_invents_nothing(self):
        self._write(tally="")
        counts, changed, out = self._run(apply_=True)
        self.assertIsNotNone(counts)
        self.assertFalse(changed)
        self.assertIn("nothing to reconcile", out)
        self.assertNotIn("**Tally:**", self._text().split("## Summary table")[1])

    def test_missing_summary_table_is_skipped_not_fatal(self):
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Ledger\n\nno table here\n")
        counts, changed, out = self._run(apply_=True)
        self.assertEqual((counts, changed), (None, False))
        self.assertIn("skipped", out)

    def test_reports_but_never_raises(self):
        """Same contract as every other pass here: a session mid-close must
        never be stopped by a count."""
        self._write(tally="**Tally:** garbage", extra="| 9 | ⏳ | x | — | — |")
        rotate.reconcile_tally(self.ledger, False)


class TestSummaryTableSpan(unittest.TestCase):
    """summary_table_section() is now defined in terms of summary_table_span();
    these lock that the refactor kept the bound identical, since every row scan
    and the doc-integrity gate depend on it."""

    def test_span_and_section_agree(self):
        text = TALLY_LEDGER.format(tally="**Tally:** x", extra="")
        start, end = rotate.summary_table_span(text)
        self.assertEqual(text[start:end], rotate.summary_table_section(text))

    def test_section_still_ends_at_a_h3(self):
        text = "# L\n\n## Summary table\n\n| 1 | ✅ | a |\n\n### #001 desc\n\n| 2 | ⏳ | b |\n"
        self.assertNotIn("| 2 |", rotate.summary_table_section(text))

    def test_absent_table_is_none_both_ways(self):
        self.assertIsNone(rotate.summary_table_span("# L\n\nnothing\n"))
        self.assertIsNone(rotate.summary_table_section("# L\n\nnothing\n"))


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_rotate.py ═══
