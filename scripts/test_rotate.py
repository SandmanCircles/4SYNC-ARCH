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
import json
import os
import shutil
import subprocess
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotate  # noqa: E402


@contextlib.contextmanager
def no_pyyaml():
    """Run a block as if PyYAML were not installed, on any box.

    Same mechanism as the inline patch in test_declaration_is_read_without_pyyaml,
    lifted out because MP#73 needed it in several places. PyYAML is absent from
    every fresh Python — "the modal fresh install" — so this is the DEFAULT adopter
    configuration, not an edge case, and the regex path it forces is the one most
    adopters actually execute."""
    real_import = builtins.__import__

    def _no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("PyYAML not installed")
        return real_import(name, *a, **k)

    with mock.patch.object(builtins, "__import__", _no_yaml):
        yield


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

    def test_four_space_indent_is_read_not_silently_defaulted(self):
        """MP#73, and this is the sharpest instance of it.

        The regex fallback anchored on EXACTLY two spaces. Reindented to four, the
        block was not found and this returned JOURNAL_MAX_DEFAULT — so a manifest
        DECLARING 16384 was silently governed by 12288, with no error anywhere.
        A cap that quietly differs from the declared one is worse than an absent
        key, because it is trusted.

        Forced through the PyYAML-absent path on purpose: that path is the modal
        fresh install, so it is the one most adopters actually execute."""
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("close:\n    journal:\n        keep: 5\n        max_bytes: 4096\n")
        with no_pyyaml():
            self.assertEqual(rotate.manifest_journal_max(self.root), 4096)

    def test_tab_indent_is_read_too(self):
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("close:\n\tjournal:\n\t\tkeep: 5\n\t\tmax_bytes: 4096\n")
        with no_pyyaml():
            self.assertEqual(rotate.manifest_journal_max(self.root), 4096)

    def test_a_sibling_block_does_not_donate_its_max_bytes(self):
        """The scoping half of the old regex was correct and must survive the
        loosening: `max_bytes` under `manifest_rules` is not the journal's."""
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("close:\n    journal:\n        keep: 5\n"
                     "integrity:\n    manifest_rules:\n        max_bytes: 999\n")
        with no_pyyaml():
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
        # RESTORED TO 200 (SYN-090). This fixture was bumped 200 -> 300 when the
        # 2,048 B floor landed, because at 200 it stopped firing — the input was
        # moved until the test passed rather than the behaviour being decided.
        # 200 repeats is ~2 KB, inside the band the floor silenced, and it fires.
        rows, pr, out = self._run("**Tally:** " + "narrative " * 200)
        self.assertGreater(pr, rows)
        self.assertIn("OVER THRESHOLD", out)

    def test_the_sub_2kb_dead_zone_is_reported(self):
        """THE BAND THE FLOOR SILENCED, now asserted directly.

        Prose that outweighs rows but stays under the old 2,048 B floor was never
        reported — permanently, and for every small or young ledger, which is the
        whole adopter population this check protects. A ledger at 90% prose printed
        'over the 50% threshold' and alarmed never."""
        rows, pr, out = self._run("**Tally:** " + "narrative " * 150)
        self.assertGreater(pr, rows)
        self.assertLess(pr, 2048, "fixture must sit INSIDE the old dead zone")
        self.assertIn("OVER THRESHOLD", out)

    def test_template_scaffolding_is_not_counted(self):
        """Each excluded class, one at a time, against the same real narrative."""
        narrative = "Row 7 landed and here is the reason it took two sessions."
        _, bare, _ = self._run(narrative)
        for dressed in (
                "<!-- an instruction to the reader -->\n\n" + narrative,
                "**Tally:** " + narrative,
                narrative + "\n\n[an unfilled placeholder]",
        ):
            _, got, _ = self._run(dressed)
            self.assertEqual(got, bare, "scaffolding leaked into the count: " + dressed[:40])

    def test_a_short_tally_is_clean(self):
        rows, pr, out = self._run("**Tally:** 2 tasks. 1 completed, 1 pending.")
        self.assertLess(pr, rows)
        self.assertNotIn("OVER THRESHOLD", out)

    def test_the_figure_arrives_already_compared(self):
        """MP#56: a bare measurement with no stated limit gives a reader nothing to
        fail, which is how this line read as scenery for weeks while reporting the
        number that eventually forced a restructure."""
        _, _, over = self._run("**Tally:** " + "narrative " * 200)
        _, _, under = self._run("**Tally:** 2 tasks. 1 completed, 1 pending.")
        for out in (over, under):
            self.assertIn("% of the summary section", out)
            self.assertIn("threshold", out)
        self.assertIn("over the 50% threshold", over)
        self.assertIn("under the 50% threshold", under)

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
        # rather than on the behavior.
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
    def test_the_old_period_form_reports_as_format_not_count_drift(self):
        """SYN-090. An instance upgrading from before v1.1.2 carries the old
        template's wording — `N tasks total.` where the current form has
        `N tasks total —`. THE COUNTS ARE RIGHT. Comparing whole lines reported
        that as "disagrees with the table", so the first rotate after an upgrade
        (a dry run, by the first-use rule) opened by naming a problem the ledger
        did not have — the cry-wolf shape SYN-088 already closed once."""
        old = ("**Tally:** 3 tasks total. 1 completed, 0 in_progress, "
               "1 pending, 0 blocked, 1 dropped.")
        self._write(tally=old)
        _, changed, out = self._run()
        self.assertFalse(changed)
        self.assertIn("older format", out)
        self.assertNotIn("disagrees with the table", out)

    def test_the_old_period_form_is_still_rewritten_on_apply(self):
        """Reported differently, treated the same: --apply restores the canonical
        line so the next run is silent."""
        self._write(tally="**Tally:** 3 tasks total. 1 completed, 0 in_progress, "
                          "1 pending, 0 blocked, 1 dropped.")
        _, changed, _ = self._run(apply_=True)
        self.assertTrue(changed)
        self.assertIn("3 tasks total — 1 completed", self._text())

    def test_a_genuine_count_error_still_reads_as_disagreement(self):
        """The format path must not swallow a real miscount wearing old wording."""
        self._write(tally="**Tally:** 99 tasks total. 99 completed, 0 in_progress, "
                          "0 pending, 0 blocked, 0 dropped.")
        _, _, out = self._run()
        self.assertIn("disagrees with the table", out)
        self.assertNotIn("older format", out)

    def test_unrecognised_status_blocks_the_rewrite(self):
        """A total that silently omits a row is worse than a stale one: it looks
        authoritative. Report it and leave the line alone, even on --apply."""
        stale = "**Tally:** 99 tasks total — 99 completed, 0 in_progress, 0 pending, 0 blocked, 0 dropped."
        self._write(tally=stale, extra="| 4 | ??? | typo'd status | — | — |")
        counts, changed, out = self._run(apply_=True)
        self.assertFalse(changed)
        self.assertEqual(sum(counts.values()), 3)   # the good rows only
        self.assertIn("no recognized status mark", out)
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


# Both legends below are VERBATIM from the only two ARCH instances that exist —
# the shipped template's prose line and a real adopter's table. They are copied
# rather than invented because MP#68 is a defect that only showed up against
# real text, and a fixture written to suit the parser proves nothing.
PROSE_LEGEND = ("**Status:** ✅ completed · 🔄 in progress · ⏳ pending, pickup-ready · "
                "⏸️ blocked (see Blocked by) · ❌ dropped, kept as audit trail")

TABLE_LEGEND = """| Symbol | Status | Meaning |
|---|---|---|
| ✅ | completed | shipped, in production or merged |
| 🔄 | in_progress | actively being worked, owner assigned or about to be |
| ⏳ | pending (open) | pickup-ready, no blockers |
| ⏸️ | blocked | waiting on upstream tasks (see Blocked by column) |
| ❌ | dropped | deliberately removed from scope; preserved as audit trail |"""


class TestLegendDerivedMarks(unittest.TestCase):
    """MP#68. An instance's task taxonomy is instance state, but rotate.py
    hardcoded the vocabulary — so a sixth status made its rows `unknown`, and
    unknown rows block the Tally rewrite. Measured on a real adopter: two rows
    in a sixth state left a hand-written count stranded with the mechanism that
    repairs it switched off. The fix is a discriminator, not a longer tuple."""

    def _ledger(self, legend="", rows=""):
        return ("# Ledger\n\n" + legend + "\n\n---\n\n## Summary table\n\n"
                "| ID | Status | Subject | Blocked by | Owner |\n|---|---|---|---|---|\n"
                "| 1 | ✅ | done | — | — |\n| 2 | ⏳ | open | — | — |\n" + rows +
                "\n**Tally:** stale\n\n---\n")

    # ── the two formats ─────────────────────────────────────────────────────
    def test_prose_legend_is_parsed(self):
        marks = dict(rotate.parse_legend_marks(self._ledger(PROSE_LEGEND)))
        self.assertEqual(marks["✅"], "completed")
        self.assertEqual(marks["🔄"], "in_progress")   # "in progress" → underscored
        self.assertEqual(marks["⏳"], "pending")        # gloss after the comma dropped

    def test_table_legend_is_parsed(self):
        marks = dict(rotate.parse_legend_marks(self._ledger(TABLE_LEGEND)))
        self.assertEqual(marks["✅"], "completed")
        self.assertEqual(marks["⏳"], "pending")        # "pending (open)" → pending
        self.assertEqual(marks["⏸"], "blocked")         # selector stripped

    def test_neither_header_nor_separator_becomes_an_entry(self):
        marks = dict(rotate.parse_legend_marks(self._ledger(TABLE_LEGEND)))
        self.assertNotIn("Symbol", marks)
        self.assertNotIn("---", marks)

    def test_summary_rows_are_not_read_as_legend_entries(self):
        """The summary table is `| id | mark |`, the same shape as a legend row.
        It is cut out before scanning, or every row id becomes a symbol."""
        marks = dict(rotate.parse_legend_marks(self._ledger()))
        self.assertEqual(marks, {})

    # ── a declared sixth mark counts ────────────────────────────────────────
    def test_prose_declared_sixth_mark_is_counted(self):
        text = self._ledger(PROSE_LEGEND + " · 🔮 future",
                            "| 3 | 🔮 | someday thing | — | — |\n")
        counts, unknown = rotate.compute_tally(text)
        self.assertEqual(unknown, [])
        self.assertEqual(counts["future"], 1)

    def test_table_declared_sixth_mark_is_counted(self):
        text = self._ledger(TABLE_LEGEND + "\n| 🔮 | future | not yet on the runway |",
                            "| 3 | 🔮 | someday thing | — | — |\n")
        counts, unknown = rotate.compute_tally(text)
        self.assertEqual(unknown, [])
        self.assertEqual(counts["future"], 1)

    def test_declared_mark_renders_after_the_base_five(self):
        text = self._ledger(PROSE_LEGEND + " · 🔮 future",
                            "| 3 | 🔮 | someday thing | — | — |\n")
        counts, _ = rotate.compute_tally(text)
        self.assertEqual(
            rotate.render_tally(counts),
            "**Tally:** 3 tasks total — 1 completed, 0 in_progress, 1 pending, "
            "0 blocked, 0 dropped, 1 future.")

    # ── the behaviours this must NOT break ──────────────────────────────────
    def test_an_UNDECLARED_mark_still_blocks_the_rewrite(self):
        """NAMED FOR THE NON-BEHAVIOUR IT PROTECTS. The block is correct for a
        typo and was only ever wrong for a vocabulary. A refactor that makes
        every unrecognized glyph countable removes the guard MP#39 relies on:
        a confidently wrong total is worse than a stale one."""
        text = self._ledger(PROSE_LEGEND, "| 3 | 🔮 | undeclared | — | — |\n")
        counts, unknown = rotate.compute_tally(text)
        self.assertEqual([tid for tid, _ in unknown], [3])
        self.assertNotIn("future", counts)

    def test_a_five_mark_ledger_is_byte_identical_to_before(self):
        """The shipped Tally sentence must not move for anyone who added
        nothing — otherwise every adopter's ledger diffs on cosmetics."""
        text = self._ledger(PROSE_LEGEND)
        counts, _ = rotate.compute_tally(text)
        self.assertEqual(
            rotate.render_tally(counts),
            "**Tally:** 2 tasks total — 1 completed, 0 in_progress, 1 pending, "
            "0 blocked, 0 dropped.")

    def test_no_legend_falls_back_to_the_hardcoded_five(self):
        """A ledger whose legend cannot be found keeps today's behaviour. This
        fix must never hand an adopter the failure it exists to remove."""
        self.assertEqual(rotate.effective_marks(self._ledger()), rotate.STATUS_MARKS)

    def test_a_stray_table_row_in_prose_does_not_mint_a_status(self):
        """'Declared' means a legend, not 'appears in any table anywhere'. A
        journal block quoting one symbol-headed row must not create a status —
        that would let narrative extend the vocabulary silently, which is the
        same class of defect as the one this whole row fixes."""
        stray = "2026-08-10 [agent] — notes.\n\n| 🔮 | future | a passing mention |\n"
        text = self._ledger(PROSE_LEGEND + "\n\n" + stray,
                            "| 3 | 🔮 | someday thing | — | — |\n")
        counts, unknown = rotate.compute_tally(text)
        self.assertEqual([tid for tid, _ in unknown], [3])
        self.assertNotIn("future", counts)

    def test_a_legend_reusing_a_base_name_is_dropped(self):
        """A second spelling of an existing status would key-collide and
        double-count. Dropping it leaves the row `unknown`, which reports the
        conflict instead of burying it inside a number."""
        text = self._ledger(PROSE_LEGEND + " · 🔮 pending")
        self.assertEqual(rotate.effective_marks(text), rotate.STATUS_MARKS)


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


STATUS_MANIFEST = """\
sync_version: "1.0"

boot:
  - MERGE_PLAN.md
  - {status}

close:
  meter:
    script: scripts/meter.py

integrity:
  manifest_rules:
    max_bytes: {cap}
"""


def _capture(fn, *a, **kw):
    """Run fn, return (result, printed output). Every pass in rotate.py reports
    through stdout, so the output IS the interface these tests are locking."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a, **kw)
    return result, buf.getvalue()


class StatusFactsCase(ManifestEnvCase):
    """A minimal instance root: a manifest declaring where STATUS lives, and a
    STATUS file whose text each test supplies."""

    CAP = 16384

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="arch_status_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)

    def write(self, status_text, status_rel="config/STATUS.yaml", cap=None):
        with open(os.path.join(self.root, self.MANIFEST_NAME), "w", encoding="utf-8") as f:
            f.write(STATUS_MANIFEST.format(status=status_rel,
                                           cap=self.CAP if cap is None else cap))
        p = os.path.join(self.root, status_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(status_text)
        return p

    def run_facts(self, status_text, **kw):
        self.write(status_text, **kw)
        (_, findings, notes), out = _capture(rotate.report_status_facts,
                                             self.root, run_meter=False)
        return findings, notes, out

    def manifest_size(self):
        return os.path.getsize(os.path.join(self.root, self.MANIFEST_NAME))


class TestStatusFactsPlumbing(StatusFactsCase):
    def test_status_file_comes_from_the_boot_declaration(self):
        """Genesis prefixes the loader stack, so a hardcoded config/STATUS.yaml
        checks NOTHING on every instance past its own genesis — and reports clean
        while doing it, which is the worst failure a report-only pass can have."""
        p = self.write("x: 1\n", status_rel="config/DEMO_STATUS.yaml")
        self.assertEqual(rotate.find_status_file(self.root), p)

    def test_missing_status_is_skipped_not_fatal(self):
        with open(os.path.join(self.root, self.MANIFEST_NAME), "w", encoding="utf-8") as f:
            f.write(STATUS_MANIFEST.format(status="config/STATUS.yaml", cap=self.CAP))
        (path, findings, _), out = _capture(rotate.report_status_facts,
                                            self.root, run_meter=False)
        self.assertIsNone(path)
        self.assertEqual(findings, [])
        self.assertIn("skipped", out)

    def test_no_checkable_claims_is_silent(self):
        """Prose with no self-identifying claim in it must produce no findings —
        the whole design rests on silence being the default."""
        findings, notes, out = self.run_facts(
            'active_focus: "Going public is the next event; the copy reads well '
            'and nobody has objected to the pricing."\n')
        self.assertEqual(findings, [])
        self.assertNotIn("!", out)

    def test_never_raises_on_a_hostile_file(self):
        findings, _, _ = self.run_facts("\x00 12,345 B / / / #### ''' aaaaaaa\n")
        self.assertIsInstance(findings, list)


class TestStatusSizeClaims(StatusFactsCase):
    def test_matching_size_claim_is_silent(self):
        with open(os.path.join(self.root, "MERGE_PLAN.md"), "w", encoding="utf-8") as f:
            f.write("x" * 4242)
        findings, _, _ = self.run_facts('s: "MERGE_PLAN.md is 4,242 B of boot."\n')
        self.assertEqual(findings, [])

    def test_drifting_size_claim_reports_line_claimed_and_measured(self):
        with open(os.path.join(self.root, "MERGE_PLAN.md"), "w", encoding="utf-8") as f:
            f.write("x" * 4242)
        findings, _, out = self.run_facts('a: 1\nb: 2\ns: "MERGE_PLAN.md is 9,999 B."\n')
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual((f["line"], f["kind"], f["subject"]), (3, "size", "MERGE_PLAN.md"))
        self.assertEqual((f["claimed"], f["measured"]), ("9,999 B", "4,242 B"))
        self.assertIn("line 3", out)

    def test_claim_naming_a_missing_file_is_reported_not_crashed(self):
        findings, _, _ = self.run_facts('s: "docs/gone.md is 1,000 B."\n')
        self.assertEqual([(f["kind"], f["subject"], f["measured"]) for f in findings],
                         [("size", "docs/gone.md", "no such file")])

    def test_slash_shaped_prose_is_not_a_missing_file_claim(self):
        """MP#47/D3, from a second instance's real STATUS sentence.

        `--template-url/S3` has a slash, so it passed the path-shape gate, so a
        sentence about a CloudFormation inline-template limit produced two findings
        about a file nobody claimed existed. A slash alone is too weak to carry a
        MISSING-file finding; the extension test above still catches a renamed
        file, which is the case worth keeping."""
        findings, _, _ = self.run_facts(
            's: "CFN --template-url/S3 template 80,592B > 51,200B inline limit."\n')
        self.assertEqual(findings, [])

    def test_an_unattributed_byte_figure_is_prose(self):
        """'4,955 B vs 0' names nothing. A checker that guessed at its subject
        would be inventing claims in order to fail them."""
        findings, _, _ = self.run_facts('s: "bootstrap costs 4,955 B vs 0 elsewhere."\n')
        self.assertEqual(findings, [])

    def test_a_yaml_key_with_a_dot_is_not_a_path(self):
        findings, _, _ = self.run_facts(
            's: "against manifest_rules.max_bytes there are 4,231 B free."\n')
        self.assertEqual(findings, [])

    def test_a_quoted_size_claim_is_a_citation(self):
        findings, _, _ = self.run_facts(
            "s: \"the previous entry said 'MERGE_PLAN.md is 9,999 B' and was wrong.\"\n")
        self.assertEqual(findings, [])


class TestStatusCapClaims(StatusFactsCase):
    def test_a_correct_cap_pair_is_silent(self):
        self.write("placeholder\n")
        findings, _, _ = self.run_facts(
            's: "manifest %s / %d (free)."\n' % (f"{self.manifest_size():,}", self.CAP))
        self.assertEqual(findings, [])

    def test_a_wrong_size_against_a_real_cap_is_reported(self):
        findings, _, _ = self.run_facts('s: "manifest 9,999 / %d."\n' % self.CAP)
        self.assertEqual([(f["kind"], f["claimed"]) for f in findings],
                         [("cap", "9,999 B")])

    def test_regression_2026_08_05_the_cap_itself_was_the_wrong_number(self):
        """The observed defect: STATUS read 'AT 99% OF ITS CAP (12,153 / 12,288)'
        and warned the next genesis edit would hit g5 mid-file. The byte count was
        right and the CAP was invented, so the wall it described did not exist —
        which is why a check of the size alone could never have seen it, and why
        the word 'cap' beside a ratio is enough to make it a claim."""
        self.write("placeholder\n")
        findings, _, out = self.run_facts(
            's: "the manifest is AT 99%% OF ITS CAP (%s / 12,288)."\n'
            % f"{self.manifest_size():,}")
        self.assertEqual([f["claimed"] for f in findings], ["cap 12,288"])
        self.assertIn("no manifest declares that cap", out)

    def test_a_manifest_over_its_own_declared_cap_is_reported(self):
        """Needs no STATUS claim at all: the manifest and its cap are both on disk,
        and g5 blocks edits to a manifest already past it."""
        findings, _, _ = self.run_facts("s: 1\n", cap=10)
        self.assertEqual([(f["line"], f["kind"]) for f in findings], [(None, "manifest")])

    def test_a_quoted_cap_claim_is_a_citation_not_an_assertion(self):
        """STATUS quotes superseded figures on purpose, to record why they were
        wrong. Flagging those would fire every close forever on a sentence doing
        its job."""
        findings, _, _ = self.run_facts(
            "s: \"the entry said '99%% OF ITS CAP (12,153 / 12,288)' and the cap was wrong.\"\n")
        self.assertEqual(findings, [])

    def test_an_apostrophe_does_not_open_a_citation(self):
        """`the silo's cap` must not swallow the rest of the line as quoted text."""
        findings, _, _ = self.run_facts(
            's: "the silo\'s cap moved; the product\'s did not: 9,999 / %d."\n' % self.CAP)
        self.assertEqual(len(findings), 1)

    def test_a_bare_ratio_with_no_cue_and_no_known_cap_is_prose(self):
        findings, _, _ = self.run_facts('s: "we shipped 3 / 4 of the rows."\n')
        self.assertEqual(findings, [])


class TestStatusSuiteClaims(StatusFactsCase):
    def _suite(self, name, n):
        d = os.path.join(self.root, "scripts")
        os.makedirs(d, exist_ok=True)
        body = "import unittest\n\n\nclass T(unittest.TestCase):\n" + "".join(
            "    def test_%d(self):\n        pass\n\n" % i for i in range(n))
        with open(os.path.join(d, "test_%s.py" % name), "w", encoding="utf-8") as f:
            f.write(body)

    def test_a_matching_suite_count_is_silent(self):
        self._suite("widget", 7)
        findings, _, _ = self.run_facts('s: "Suites 7 widget, green."\n')
        self.assertEqual(findings, [])

    def test_a_drifting_suite_count_is_reported(self):
        self._suite("widget", 7)
        findings, _, _ = self.run_facts('s: "Suites 4 widget / 4 widget."\n')
        self.assertEqual([(f["subject"], f["claimed"], f["measured"]) for f in findings],
                         [("widget", "4", "7"), ("widget", "4", "7")])

    def test_an_unresolvable_suite_name_is_a_note_not_a_finding(self):
        """'hooks' names a directory holding two suites, not a file. Guessing which
        one it meant is how a report starts manufacturing drift; saying so out loud
        is how the claim gets rephrased into something checkable."""
        findings, notes, out = self.run_facts('s: "Suites 60 hooks."\n')
        self.assertEqual(findings, [])
        self.assertTrue(any("hooks" in n for n in notes))
        self.assertNotIn("!", out)

    def test_a_hand_rolled_harness_is_not_guessed_at(self):
        """The real one runs 35 assertions from 32 call sites, so the obvious static
        count is wrong by three — and a previous session's parser made the same class
        of error the other way, reporting 75 for a suite of 35."""
        d = os.path.join(self.root, "scripts")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "test_legacy.py"), "w", encoding="utf-8") as f:
            f.write("def check(n, c):\n    pass\n\n\ndef main():\n"
                    "    for i in range(3):\n        check('x', True)\n")
        findings, notes, _ = self.run_facts('s: "Suites 35 legacy."\n')
        self.assertEqual(findings, [])
        self.assertTrue(any("not statically countable" in n for n in notes))

    def test_identical_copies_of_one_suite_are_one_answer_not_an_ambiguity(self):
        self._suite("widget", 7)
        os.makedirs(os.path.join(self.root, "nested", "scripts"), exist_ok=True)
        shutil.copy(os.path.join(self.root, "scripts", "test_widget.py"),
                    os.path.join(self.root, "nested", "scripts", "test_widget.py"))
        findings, notes, _ = self.run_facts('s: "Suites 7 widget."\n')
        self.assertEqual((findings, notes), ([], []))

    def test_an_ordinary_sentence_does_not_invent_a_suite(self):
        """'the suite shipped … MP#42 to stop that' contains the pair '42 to'."""
        findings, notes, _ = self.run_facts(
            's: "the suite shipped late, which is what MP#42 to stop that was for"\n')
        self.assertEqual((findings, notes), ([], []))


class TestStatusShaClaims(StatusFactsCase):
    def test_a_fabricated_long_sha_is_reported_without_any_cue(self):
        """A session here once wrote a 40-char SHA invented from a short hash. A
        pin that looks authoritative and points at nothing is the worst thing this
        file can carry, so length alone makes it a claim."""
        findings, _, _ = self.run_facts('s: "pairs with %s forever."\n' % ("a1" * 20))
        self.assertEqual([(f["kind"], f["subject"]) for f in findings],
                         [("sha", "a1" * 20)])

    def test_short_hex_with_no_commit_cue_is_not_a_claim(self):
        """'the week-old 03721ead row' is a session id in prose. It asserts no
        commit, so there is nothing to resolve and nothing to report."""
        findings, _, _ = self.run_facts('s: "the week-old 03721ead row was fine."\n')
        self.assertEqual(findings, [])

    def test_short_hex_beside_a_commit_cue_is_checked(self):
        findings, _, _ = self.run_facts('s: "in sync with origin/main at 03721ead."\n')
        self.assertEqual([f["subject"] for f in findings], ["03721ead"])

    def test_a_content_digest_is_not_a_commit_claim(self):
        """MP#47/D1. `sha256:64305da8` is an image digest, and the literal
        "sha256" contains the cue "sha" — so the one token saying *this is not a
        commit* was what promoted it to one. Three false positives on a second
        instance carrying live ECR digests."""
        findings, _, _ = self.run_facts('s: "live_digest sha256:64305da8 on ECR."\n')
        self.assertEqual(findings, [])

    def test_a_sha1_length_digest_is_also_suppressed(self):
        """The prefix is checked BEFORE the unconditional 40-char rule: a sha1
        digest is itself 40 hex characters, so a suffix-only fix still fires."""
        findings, _, _ = self.run_facts('s: "blob sha1:%s here."\n' % ("b3" * 20))
        self.assertEqual(findings, [])

    def test_a_suppressed_digest_does_not_donate_its_cue(self):
        """MP#49, the real line shape from a second instance. `3ee8019` is prose
        about an image, and the only `sha` on the line belongs to the digest that
        was just suppressed — so the ruled-out token convicted its neighbour.
        Suppression has to withdraw the cue, not only the match."""
        findings, _, _ = self.run_facts(
            's: "live sha256:d32dfac2, one further back 3ee8019 in the registry."\n')
        self.assertEqual(findings, [])

    def test_a_genuine_cue_elsewhere_on_the_line_still_checks(self):
        """The control that bounds the fix. Masking removes the DIGEST's cue, not
        every cue — over-withdrawal would stop checking real pins, which fails
        quiet and is worse than the false positive it removes."""
        findings, _, _ = self.run_facts(
            's: "origin/main at 3ee8019, image sha256:d32dfac2."\n')
        self.assertEqual([f["subject"] for f in findings], ["3ee8019"])

    def test_masking_preserves_offsets_so_line_numbers_survive(self):
        """Every offset in this module is computed against the original text, so
        the masked copy must stay the same length or findings land on wrong lines."""
        self.assertEqual(len(rotate._mask_digests("x sha256:64305da8 y")),
                         len("x sha256:64305da8 y"))
        findings, _, _ = self.run_facts(
            'a: 1\nb: 2\ns: "image sha256:d32dfac2 and commit 1234abc."\n')
        self.assertEqual([f["line"] for f in findings], [3])

    def test_the_same_hex_without_an_algo_prefix_is_still_checked(self):
        """The control. Suppression is scoped to `<algo>:` — `commit: 7760f30`
        stays exactly the pin this check exists to verify."""
        findings, _, _ = self.run_facts('s: "commit 64305da8 is pinned."\n')
        self.assertEqual([f["subject"] for f in findings], ["64305da8"])

    # ── MP#51: cues are words, not substrings ────────────────────────────────

    def test_a_cue_inside_a_longer_word_is_not_a_cue(self):
        """THE REAL LINE from a second instance. `pin` matched inside "repins",
        so a hex string in prose about a rollback was reported as a commit that
        resolves nowhere — and MP#49 was opened, and shipped, blaming the
        `sha256:` digest next to it."""
        findings, _, _ = self.run_facts(
            's: "one further back 3ee8019 — rollback repins the digest."\n')
        self.assertEqual(findings, [])

    def test_the_other_real_collisions_are_silent(self):
        """Every one of these words is already present in some STATUS file in
        this project. Only the absence of a nearby hex kept them quiet."""
        for word in ("domain", "beachhead", "original", "uncommitted",
                     "headline", "remaining"):
            with self.subTest(word=word):
                findings, _, _ = self.run_facts('s: "%s 1234abc here."\n' % word)
                self.assertEqual(findings, [], word)

    def test_the_same_cues_as_whole_words_still_promote(self):
        """The control, and the direction that matters most: over-withdrawal
        fails QUIET. A cue that stops matching means a fabricated pin stops
        being reported and nothing announces it."""
        for phrase in ("pushed at 1234abc", "origin/main at 1234abc",
                       "commit 1234abc", "the pin 1234abc", "HEAD 1234abc"):
            with self.subTest(phrase=phrase):
                findings, _, _ = self.run_facts('s: "%s."\n' % phrase)
                self.assertEqual([f["subject"] for f in findings], ["1234abc"], phrase)

    def test_a_long_sha_needs_no_cue_at_all(self):
        """Length alone is still a claim — word boundaries must not touch the
        unconditional 40-char rule that catches a fabricated pin."""
        findings, _, _ = self.run_facts('s: "domain %s ordinary prose."\n' % ("a1" * 20))
        self.assertEqual([f["subject"] for f in findings], ["a1" * 20])

    def test_a_repo_name_cue_still_works_as_a_word(self):
        """A repo name is only a cue when that repo is actually discovered — the
        first version of this test asserted the finding without creating the
        repo, and passed for no reason once boundaries were added."""
        os.makedirs(os.path.join(self.root, "4SYNC-ARCH", ".git"), exist_ok=True)
        findings, _, _ = self.run_facts('s: "pairs with 4SYNC-ARCH at 1234abc."\n')
        self.assertEqual([f["subject"] for f in findings], ["1234abc"])

    def test_non_alphabetic_cues_keep_matching_literally(self):
        """`@` has no word character to bound, so it must stay a bare literal."""
        findings, _, _ = self.run_facts('s: "tagged @ 1234abc in the log."\n')
        self.assertEqual([f["subject"] for f in findings], ["1234abc"])

    def test_hex_letters_with_no_digit_are_a_word_not_a_hash(self):
        findings, _, _ = self.run_facts('s: "the commit defaced the baseline."\n')
        self.assertEqual(findings, [])

    def test_a_real_commit_in_a_real_repo_resolves(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

        def g(*a):
            return subprocess.run(["git"] + list(a), cwd=self.root, env=env,
                                  capture_output=True, text=True)
        if g("init", "-q").returncode != 0:
            self.skipTest("git unavailable")
        with open(os.path.join(self.root, "seed.txt"), "w") as f:
            f.write("x")
        g("add", "seed.txt")
        g("commit", "-qm", "seed")
        sha = g("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(sha)
        findings, _, _ = self.run_facts('s: "pushed at %s."\n' % sha[:7])
        self.assertEqual(findings, [])


class TestStatusBootClaims(StatusFactsCase):
    def test_a_drifting_boot_figure_is_reported_in_both_units(self):
        text = 's: "BOOT COST 20,539 tokens / 82,170 B — measured."\n'
        findings = []
        rotate._check_boot(text, [], (21784, 87149), findings)
        self.assertEqual([(f["subject"], f["claimed"], f["measured"]) for f in findings],
                         [("tokens", "20,539", "21,784"), ("bytes", "82,170 B", "87,149 B")])

    def test_a_matching_boot_figure_is_silent(self):
        findings = []
        rotate._check_boot('s: "boot cost 21,784 tokens / 87,149 B"\n', [],
                           (21784, 87149), findings)
        self.assertEqual(findings, [])

    def test_no_meter_means_no_boot_check_and_no_subprocess(self):
        with mock.patch.object(subprocess, "run",
                               side_effect=AssertionError("should not spawn")):
            findings, _, _ = self.run_facts('s: "BOOT COST 1 tokens."\n')
        self.assertEqual(findings, [])

    def test_the_meter_is_read_from_the_declaration(self):
        self.write("x\n")
        self.assertEqual(rotate.manifest_meter_script(self.root), "scripts/meter.py")

    def test_an_absent_meter_is_a_skip_not_a_failure(self):
        self.write("x\n")
        self.assertIsNone(rotate._meter_boot(self.root, "scripts/meter.py"))


class TestStatusFieldContradiction(StatusFactsCase):
    def test_regression_2026_08_04_a_field_contradicted_by_its_neighbour(self):
        """The other observed shape: one field asserting something the field beside
        it already disproves. Both were written by hand, months apart in attention,
        and neither announced the disagreement.

        The half of that case this pass CANNOT reach is stated here on purpose: the
        2026-08-04 original was a claim about a web page's COPY, and prose about
        file contents is out of scope by design — a checker that flags prose is a
        checker nobody reads. What it does reach is the same shape wherever the two
        fields carry numbers, which is where it kept recurring."""
        with open(os.path.join(self.root, "MERGE_PLAN.md"), "w", encoding="utf-8") as f:
            f.write("x" * 4242)
        findings, _, _ = self.run_facts(
            'a: "MERGE_PLAN.md is 4,242 B."\n'
            'b: "MERGE_PLAN.md is 9,999 B and growing."\n')
        self.assertEqual([(f["line"], f["claimed"]) for f in findings],
                         [(2, "9,999 B")])


PICKUP_LEDGER = """\
# Ledger

## Summary table

| ID | Status | Subject | Blocked by | Owner |
|---|---|---|---|---|
| 5 | ⏳ | open one | — | — |
| 6 | ✅ | done one | — | — |
| 7 | ⏳ | open two | — | — |

**Tally:** 3 tasks total.

{pickup}
"""


class TestPickupReady(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arch_pickup_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ledger = os.path.join(self.dir, "MERGE_PLAN.md")

    def _write(self, pickup):
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write(PICKUP_LEDGER.format(pickup=pickup))

    def test_a_list_matching_the_table_is_clean(self):
        self._write("**Pickup-ready right now:** **#5** then **#7**, neither blocked.")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([], []))
        self.assertIn("✓", out)

    def test_an_omitted_pending_row_is_reported(self):
        self._write("**Pickup-ready right now:** just **#5**.")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([7], []))
        self.assertIn("#7", out)

    def test_a_row_named_after_it_closed_is_reported(self):
        """Observed on the real ledger: the paragraph named a row for a day after
        that row went ✅, and omitted another for the same day."""
        self._write("**Pickup-ready right now:** **#5**, **#6** and **#7**.")
        missing, extra = rotate.report_pickup_ready(self.ledger)
        self.assertEqual((missing, extra), ([], [6]))

    # ── the block, not the header (adopter field report, 2026-08-19) ────────
    def test_a_multi_line_pickup_list_is_read_whole(self):
        """THE FALSE POSITIVE ON EVERY CLOSE. PICKUP_RE matched `[^\n]*$` — one
        LINE — and the row scan ran against that match, so a list written as
        bullets underneath the header was never read: `named` was always empty,
        `missing` was always every pending row, and the complaint named exactly
        the rows a genuinely stale list would name. Reported by an adopter whose
        27-row ledger flagged all five pending rows while every one was correctly
        listed. The shipped template ASKS for this shape — "plus a one-line note
        on each" — so the guidance produced the bug, and the suite never caught it
        because every fixture here was a single line."""
        self._write("**Pickup-ready right now (no blockers):**\n"
                    "- `#5` — ready, nothing in front of it.\n"
                    "- `#7` — ready too.\n")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([], []))
        self.assertIn("✓", out)

    def test_the_other_form_branch_is_reachable(self):
        """DEAD CODE UNTIL NOW, and it is the branch that exists FOR an adopter:
        `_named_in_another_form` takes a `segment`, handles `MP-003`/`row 3`, and
        was only ever fed the header line, so it always returned {}. Its message
        was written because an outside adopter writing `MP-003` "cost him two
        wrong fixes and a source read before he found the pattern himself" — and
        it could never print for him."""
        self._write("**Pickup-ready right now:**\n"
                    "- `#5` — ready.\n"
                    "- `MP-007` — named in the form used everywhere else in ARCH.\n")
        (missing, _), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual(missing, [7])
        self.assertIn("named as `MP-007`", out)
        self.assertNotIn("pending but not named", out)

    def test_a_following_bold_note_is_not_part_of_the_list(self):
        """The stop condition earns its keep, and a naive read-to-`---` would not:
        a `**Blocked, but closer:**` note under the list mentioning a NON-pending
        row must not be swallowed, or the fix trades one false positive for a
        fresh one pointing the other way."""
        self._write("**Pickup-ready right now:**\n"
                    "- `#5` — ready.\n"
                    "- `#7` — ready.\n"
                    "\n"
                    "**Blocked, but closer:** #6 landed its dependency today.\n")
        (missing, extra), _ = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([], []), "the bold note leaked into the list")

    def test_an_mp_cross_reference_is_not_a_list_entry(self):
        """`MP#39` names a task inside an argument; `#39` would name a row in the
        list. Letting the two collide made a closed row read as pickup-ready."""
        self._write("**Pickup-ready right now:** **#5** and **#7** — the same "
                    "disease MP#39 cured for the Tally.")
        missing, extra = rotate.report_pickup_ready(self.ledger)
        self.assertEqual((missing, extra), ([], []))

    def test_an_unfilled_template_placeholder_is_not_a_stale_list(self):
        """The product ships this line as a bracketed placeholder. Greeting every
        new adopter with a day-one defect is how a report-only check loses its
        audience before it has one — the same shape as the template rows that
        exited 1 on a fresh install."""
        self._write("**Pickup-ready right now (no blockers):** "
                    "[List the pending task IDs and a one-line note on each.]")
        self.assertEqual(rotate.report_pickup_ready(self.ledger), (None, None))

    def test_a_markdown_link_is_not_a_placeholder(self):
        self._write("**Pickup-ready right now:** **#5**, **#7** — see [the plan](x.md).")
        self.assertEqual(rotate.report_pickup_ready(self.ledger), ([], []))

    def test_a_ledger_without_the_paragraph_is_skipped(self):
        self._write("no such list here")
        self.assertEqual(rotate.report_pickup_ready(self.ledger), (None, None))

    def test_reports_but_never_raises(self):
        self._write("**Pickup-ready right now:** #999 and #0.")
        rotate.report_pickup_ready(self.ledger)

    def test_a_row_named_as_MP_0NN_is_not_reported_as_unnamed(self):
        """The message, not the parser. `MP-007` is ARCH's canonical ID form —
        the task doc path derives from it — so an adopter naming a row that way
        is following house style, and "not named" sends them to fix the parser
        instead of the line. It did, to the first outside adopter: two wrong
        fixes and a source read. The row is still reported (it IS outside the
        list); what changes is that the report says which shape it found."""
        self._write("**Pickup-ready right now:** **#5**, and MP-007 next.")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([7], []))
        self.assertNotIn("not named", out)
        self.assertIn("MP-007", out)
        self.assertIn("#7", out)

    def test_a_genuinely_unnamed_row_still_says_not_named(self):
        """The other direction: no other shape present, so the original wording
        is the accurate one and must survive."""
        self._write("**Pickup-ready right now:** just **#5**.")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([7], []))
        self.assertIn("pending but not named", out)

    def test_a_mix_reports_each_row_in_its_own_shape(self):
        """One line can hold both, and conflating them is what made the message
        wrong in the first place."""
        self._write("**Pickup-ready right now:** MP-005 is next; nothing on the other.")
        (missing, extra), out = _capture(rotate.report_pickup_ready, self.ledger)
        self.assertEqual((missing, extra), ([5, 7], []))
        self.assertIn("MP-005", out)
        self.assertIn("pending but not named: #7", out)


class TestStatusClosedRefs(StatusFactsCase):
    """MP#62 — the admission test was the leak.

    STATUS asks for facts that are true now AND gate future work, and nothing
    tested the second half: a closed task's outcome is still TRUE, so it sails
    past any staleness check and stays in the boot path forever. Six of eighteen
    entries were closed-task outcomes while a size report fired at every close.
    Size says the file is big; it never says which entry to cut. 'Every row this
    entry cites is closed' does, and it is mechanically decidable."""

    LEDGER = ("# L\n\n## Summary table\n\n"
              "| ID | Status | Subject | Blocked by | Owner |\n"
              "|---|---|---|---|---|\n"
              "| 1 | ✅ | done | — | — |\n"
              "| 2 | ❌ | dropped | — | — |\n"
              "| 3 | ⏳ | pending | — | — |\n"
              "| 4 | 🔄 | in progress | — | — |\n")

    def refs(self, status_text, ledger_text=None):
        self.write(status_text)
        led = os.path.join(self.root, "MERGE_PLAN.md")
        with open(led, "w", encoding="utf-8") as f:
            f.write(self.LEDGER if ledger_text is None else ledger_text)
        return _capture(rotate.report_status_closed_refs, self.root, led)

    def test_an_entry_citing_only_closed_rows_is_flagged(self):
        found, out = self.refs('in_flight:\n  - "outcome of MP#1, shipped"\n')
        self.assertEqual([(f[0], f[3]) for f in found], [("in_flight", [1])])
        self.assertIn("MP#1", out)

    def test_a_dropped_row_counts_as_closed(self):
        found, _ = self.refs('in_flight:\n  - "per MP#2"\n')
        self.assertEqual(len(found), 1)

    def test_an_entry_citing_an_open_row_is_left_alone(self):
        found, _ = self.refs('in_flight:\n  - "waiting on MP#3"\n')
        self.assertEqual(found, [])

    def test_a_mix_of_open_and_closed_is_live_work(self):
        found, _ = self.refs('in_flight:\n  - "MP#1 shipped, MP#4 still running"\n')
        self.assertEqual(found, [])

    def test_an_entry_citing_nothing_is_never_flagged(self):
        """Silence is not evidence. This check would rather miss than accuse."""
        found, _ = self.refs('in_flight:\n  - "the site is live at example.com"\n')
        self.assertEqual(found, [])

    def test_an_unknown_row_id_cannot_be_judged(self):
        found, _ = self.refs('in_flight:\n  - "see MP#99"\n')
        self.assertEqual(found, [])

    def test_a_bare_hash_number_is_not_a_reference(self):
        """`Benefit #22` is a sentence. A checker that guesses at prose produces
        findings nobody trusts — the REACH discipline, applied on purpose."""
        found, _ = self.refs('in_flight:\n  - "Benefit #1 was approved as shipped"\n')
        self.assertEqual(found, [])

    def test_every_list_field_is_scanned_not_just_in_flight(self):
        """A blocker whose task closed is not a blocker. Keying on the field NAME
        would also repeat the hardcoded-filename defect MP#40 is open about."""
        found, _ = self.refs('blockers:\n  - "blocked by MP#1"\n')
        self.assertEqual([f[0] for f in found], ["blockers"])

    def test_a_scalar_field_is_not_treated_as_a_list(self):
        found, _ = self.refs('active_focus: "MP#1 is done"\nin_flight:\n  - "MP#3"\n')
        self.assertEqual(found, [])

    def test_comments_are_not_entries(self):
        found, _ = self.refs('in_flight:\n  # MP#1 is closed, see below\n  - "MP#3"\n')
        self.assertEqual(found, [])

    def test_a_folded_continuation_line_stays_with_its_entry(self):
        found, _ = self.refs('in_flight:\n  - "opened under MP#3\n    and closed by MP#1"\n')
        self.assertEqual(found, [])

    def test_reports_the_line_number_so_you_can_go_straight_there(self):
        found, out = self.refs('meta:\n  file: S\nin_flight:\n  - "MP#1"\n')
        self.assertEqual(found[0][2], 4)
        self.assertIn(":4", out)

    def test_a_clean_file_says_so(self):
        found, out = self.refs('in_flight:\n  - "MP#4 is running"\n')
        self.assertEqual(found, [])
        self.assertIn("✓", out)

    def test_the_advice_names_the_test_not_just_the_finding(self):
        _, out = self.refs('in_flight:\n  - "MP#1"\n')
        self.assertIn("YEAR FROM NOW", out)
        self.assertIn("FINDINGS.md", out)

    def test_no_summary_table_is_skipped_not_crashed(self):
        found, out = self.refs('in_flight:\n  - "MP#1"\n', ledger_text="# no table here\n")
        self.assertEqual(found, [])
        self.assertIn("skipped", out)

    def test_no_status_file_is_skipped_not_crashed(self):
        empty = tempfile.mkdtemp(prefix="arch_norefs_")
        self.addCleanup(shutil.rmtree, empty, True)
        found, out = _capture(rotate.report_status_closed_refs, empty,
                              os.path.join(empty, "MERGE_PLAN.md"))
        self.assertEqual(found, [])
        self.assertIn("skipped", out)


class TestStatusSizeReport(StatusFactsCase):
    """MP#48 — the fourth place the growth went.

    Descriptions were capped, then the row cells they fled into, then the prose
    around the table. Nothing watched the OTHER boot file, and STATUS reached
    29,253 B of which most was closed-task narrative already held by
    tasks/closed/ — a third copy of state."""

    def test_reports_total_and_per_field_bytes(self):
        """Total is measured on the NORMALISED text, not os.path.getsize — on
        Windows a CRLF checkout makes the on-disk size larger than the content,
        and a boot-cost figure that changes with the checkout's line endings
        would be measuring the filesystem rather than the file."""
        self.write('meta:\n  file: S\nin_flight:\n  - "a"\n  - "b"\n')
        (total, fields), out = _capture(rotate.report_status_size, self.root)
        self.assertEqual({n for n, _ in fields}, {"meta", "in_flight"})
        self.assertEqual(total, sum(b for _, b in fields))  # no preamble in this fixture
        self.assertIn("status-size:", out)

    def test_flags_a_file_over_the_soft_threshold(self):
        self.write('in_flight:\n  - "%s"\n' % ("x" * 900))
        _, out = _capture(rotate.report_status_size, self.root, soft_max=256)
        self.assertIn("!", out)
        self.assertIn("SNAPSHOT", out)

    def test_a_healthy_file_is_silent(self):
        self.write('meta:\n  file: S\n')
        _, out = _capture(rotate.report_status_size, self.root, soft_max=4096)
        self.assertNotIn("!", out)

    def test_names_the_biggest_field_so_you_know_where_to_look(self):
        """Per-field for the reason the meter is per-file: the question is never
        'did it grow' but WHICH field grew."""
        self.write('meta:\n  file: S\nin_flight:\n  - "%s"\n' % ("x" * 600))
        _, out = _capture(rotate.report_status_size, self.root, soft_max=64)
        self.assertIn("in_flight:", out)

    def test_never_raises_when_no_status_file_is_declared(self):
        empty = tempfile.mkdtemp(prefix="arch_nostatus_")
        self.addCleanup(shutil.rmtree, empty, True)
        (total, fields), out = _capture(rotate.report_status_size, empty)
        self.assertEqual((total, fields), (0, []))
        self.assertIn("skipped", out)

    def test_a_junk_env_threshold_does_not_break_a_close(self):
        self.write('meta:\n  file: S\n')
        old = os.environ.get(rotate.STATUS_SOFT_MAX_ENV)
        os.environ[rotate.STATUS_SOFT_MAX_ENV] = "not-a-number"
        try:
            rotate.report_status_size(self.root)
        finally:
            if old is None:
                os.environ.pop(rotate.STATUS_SOFT_MAX_ENV, None)
            else:
                os.environ[rotate.STATUS_SOFT_MAX_ENV] = old


class TestRepoDiscovery(unittest.TestCase):
    """MP#47/D2 — sibling-repo discovery was depth-1.

    It held here only because THIS instance is the shallow case: `4SYNC-ARCH/`
    and `web/` are immediate children. A second instance kept its repos one level
    deeper and every true SHA in them was reported as resolving in no repo —
    the checker flagging CORRECT facts, which is how a report earns being ignored.
    `.git` directories are created bare because _git_roots only tests isdir()."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="arch_repos_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _mkrepo(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(os.path.join(p, ".git"), exist_ok=True)
        return p

    def test_depth_one_repo_is_found_by_bare_name(self):
        self._mkrepo("product")
        self.assertIn("product", dict(rotate._git_roots(self.root)))

    def test_nested_repo_one_level_deeper_is_found(self):
        self._mkrepo("instance")
        self._mkrepo("instance", "web")
        self.assertIn("instance/web", dict(rotate._git_roots(self.root)))

    def test_nested_repo_is_named_by_relative_path(self):
        """Two instances can each hold a `web/`. The name is also fed to the SHA
        cue list, where a bare `web` is short enough to fire on ordinary prose."""
        self._mkrepo("a", "web")
        self._mkrepo("b", "web")
        names = dict(rotate._git_roots(self.root))
        self.assertEqual(sorted(n for n in names if n.endswith("web")),
                         ["a/web", "b/web"])

    def test_discovery_is_depth_bounded(self):
        self._mkrepo("l1", "l2", "l3", "l4")
        names = dict(rotate._git_roots(self.root, max_depth=3))
        self.assertNotIn("l1/l2/l3/l4", names)

    def test_descent_continues_through_a_repo(self):
        """A repo inside a repo is the normal shape here (`4SYNC/4SYNC-ARCH/`)."""
        self._mkrepo("outer")
        self._mkrepo("outer", "inner")
        names = dict(rotate._git_roots(self.root))
        self.assertIn("outer", names)
        self.assertIn("outer/inner", names)

    def test_skip_dirs_are_not_descended(self):
        self._mkrepo("node_modules", "pkg")
        self.assertEqual([n for n in dict(rotate._git_roots(self.root)) if "pkg" in n], [])


class TestShaInNestedRepo(StatusFactsCase):
    """The end-to-end half of D2: a real commit in a real depth-2 repo."""

    def test_a_commit_in_a_nested_repo_resolves(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        nested = os.path.join(self.root, "instance", "web")
        os.makedirs(nested, exist_ok=True)

        def g(*a):
            return subprocess.run(["git"] + list(a), cwd=nested, env=env,
                                  capture_output=True, text=True)
        if g("init", "-q").returncode != 0:
            self.skipTest("git unavailable")
        with open(os.path.join(nested, "seed.txt"), "w") as f:
            f.write("x")
        g("add", "seed.txt")
        g("commit", "-qm", "seed")
        sha = g("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(sha)
        findings, _, _ = self.run_facts('s: "pushed at %s."\n' % sha[:7])
        self.assertEqual(findings, [], "a true SHA in a nested repo was called stale")


class TestCliFlags(unittest.TestCase):
    def test_dry_run_is_a_registered_flag(self):
        """MP#47/D5. The usage block documented `--dry-run` and argparse rejected
        it, so the first flag a cautious adopter reaches for was the one certain
        to fail. argparse lists only registered options in --help."""
        out = subprocess.run([sys.executable, rotate.__file__, "--help"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[:200])
        self.assertIn("--dry-run", out.stdout)



class TestBootGrowthAlert(unittest.TestCase):
    """MP#56 — the meter has written a per-close series all along and nothing ever
    read it back. A trend nobody compares against fails the same way a measurement
    nobody reads does."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rot-growth-")
        os.makedirs(os.path.join(self.root, "metrics"))
        self._script = rotate.manifest_meter_script
        self._boot = rotate._meter_boot
        rotate.manifest_meter_script = lambda root, mn=None: "meter.py"
        self.addCleanup(self._restore)

    def _restore(self):
        rotate.manifest_meter_script = self._script
        rotate._meter_boot = self._boot
        shutil.rmtree(self.root, ignore_errors=True)

    def _series(self, *boot_tokens):
        p = os.path.join(self.root, "metrics", "roc_series.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for n in boot_tokens:
                print(json.dumps({"boot_tokens": n}), file=fh)

    def _run(self, now):
        rotate._meter_boot = lambda root, script, manifest_name=None: (now, now * 4)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = rotate.report_boot_growth(self.root)
        return got, buf.getvalue()

    def test_growth_over_threshold_is_flagged(self):
        self._series(10000)
        got, out = self._run(12000)          # +20%
        self.assertEqual(got, (12000, 10000))
        self.assertIn("OVER THRESHOLD", out)

    def test_growth_under_threshold_is_reported_not_flagged(self):
        self._series(10000)
        _, out = self._run(10500)            # +5%
        self.assertIn("boot-growth:", out)
        self.assertNotIn("OVER THRESHOLD", out)

    def test_a_shrink_is_never_flagged(self):
        self._series(10000)
        _, out = self._run(9000)
        self.assertNotIn("OVER THRESHOLD", out)
        self.assertIn("-1,000", out)

    def test_it_compares_against_the_LAST_row(self):
        """Not the first, and not an average — the question is what changed since
        the previous close."""
        self._series(5000, 20000, 10000)
        got, _ = self._run(10500)
        self.assertEqual(got[1], 10000)

    def test_absent_series_says_so_rather_than_passing_silently(self):
        got, out = self._run(10000)
        self.assertIsNone(got)
        self.assertIn("nothing to compare", out)

    def test_malformed_series_does_not_fail_the_close(self):
        p = os.path.join(self.root, "metrics", "roc_series.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            print("{not json at all", file=fh)
        got, out = self._run(10000)
        self.assertIsNone(got)
        self.assertIn("skipped", out)

try:
    import yaml as _yaml_probe  # noqa: F401
    _HAS_YAML = True
except Exception:  # noqa: BLE001 — absent from every fresh Python; the default case
    _HAS_YAML = False


class TestManifestAtRest(unittest.TestCase):
    """g5 judges a WRITE; nothing judged the file on disk.

    A manifest can arrive non-compliant through any path the hook does not cover —
    a Bash redirect where g5 can only `ask`, hooks unwired, an edit from another
    machine, or a file that predates the rule. The failure stays silent until the
    NEXT write is refused, and then reads as "the guard is broken" rather than
    "this file is non-compliant" (MP#54's original misdiagnosis)."""

    CLEAN = (
        "sync_version: \"1.0\"\n"
        "instance:\n"
        "  name: \"T\"\n"
        "integrity:\n"
        "  manifest_rules:\n"
        "    max_bytes: 16384\n"
        "    declaration_only: true\n"
    )

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rot-atrest-")
        prev = os.environ.get("ARCH_MANIFEST")
        os.environ["ARCH_MANIFEST"] = "4SYNC.yaml"

        def restore():
            if prev is None:
                os.environ.pop("ARCH_MANIFEST", None)
            else:
                os.environ["ARCH_MANIFEST"] = prev
            shutil.rmtree(self.root, ignore_errors=True)

        self.addCleanup(restore)

    def _put(self, text):
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            found = rotate.report_manifest_at_rest(self.root)
        return found, buf.getvalue()

    def test_clean_manifest_reports_compliant(self):
        self._put(self.CLEAN)
        found, out = self._run()
        self.assertEqual(found, [])
        self.assertIn("compliant", out)

    def test_a_date_at_rest_is_found(self):
        self._put(self.CLEAN.replace("sync_version:", "# touched 2026-08-09\nsync_version:"))
        found, out = self._run()
        self.assertEqual(len(found), 1)
        self.assertIn("2026-08-09", out)

    def test_the_finding_names_the_line(self):
        """The original episode was misdiagnosed because the refusal said WHICH
        date but never WHERE, and the author of the refused write is usually not
        the author of the offending line."""
        self._put(self.CLEAN.replace("sync_version:", "# touched 2026-08-09\nsync_version:"))
        _, out = self._run()
        self.assertIn("line 1", out)

    def test_a_date_without_declaration_only_is_not_a_finding(self):
        """The rule is the manifest's own declaration, not our preference."""
        self._put(self.CLEAN.replace("    declaration_only: true\n", "")
                            .replace("sync_version:", "# touched 2026-08-09\nsync_version:"))
        found, _ = self._run()
        self.assertEqual(found, [])

    @unittest.skipUnless(_HAS_YAML, "parse check at rest requires PyYAML")
    def test_an_unparseable_manifest_at_rest_is_found(self):
        self._put("boot:\n  - a line that wraps\n    and says this: breaking it\n")
        found, out = self._run()
        self.assertEqual(len(found), 1)
        self.assertIn("does NOT parse", out)

    def test_absent_manifest_is_not_a_finding(self):
        found, _ = self._run()
        self.assertEqual(found, [])



class TestSnapshotOverflowMap(ManifestEnvCase):
    """MP#79. rotate said "Cut it" and named no destination — and when a session
    acted on that, "trim" collapsed into "delete" and grew a rationale that was
    false: 55 of 60 sentences existed nowhere else. The journal never had that
    failure, because close.journal.overflow_to declares where its overflow GOES,
    so trimming means moving. This generalises that one key to STATUS.

    The map is per-instance ON PURPOSE — another adopter's destinations are not
    ours — so an undeclared map degrades to generic advice, never to a guess.

    INHERITS ManifestEnvCase, and the first version of this class did not.
    CI's renamed-manifest leg (ARCH_MANIFEST=PROJECT.yaml) failed three of
    these tests while every local run passed: the fixtures write 4SYNC.yaml
    and the lookup correctly honoured the ambient variable, so it read a file
    the fixture never wrote. The shipped code was right and the tests were
    wrong — MP#78's shape exactly, and the base class exists because this has
    happened before."""

    def _manifest(self, *lines):
        root = os.path.realpath(tempfile.mkdtemp(prefix="rot-ovf-"))
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("".join(l + chr(10) for l in lines))
        return root

    def _status(self, root, size=400):
        os.makedirs(os.path.join(root, "config"), exist_ok=True)
        with open(os.path.join(root, "config", "STATUS.yaml"), "w", encoding="utf-8") as fh:
            fh.write("a: " + ("x" * size) + chr(10))

    def _report(self, root):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rotate.report_status_size(root, soft_max=100)
        return buf.getvalue()

    def test_a_declared_list_is_returned_in_order(self):
        root = self._manifest("close:", "  snapshot:", "    file: config/STATUS.yaml",
                              "    overflow_to: [FINDINGS.md, config/KERNEL.yaml, tasks/closed/]")
        self.assertEqual(rotate.manifest_snapshot_overflow(root),
                         ["FINDINGS.md", "config/KERNEL.yaml", "tasks/closed/"])

    def test_a_single_string_is_accepted(self):
        root = self._manifest("close:", "  snapshot:", "    overflow_to: FINDINGS.md")
        self.assertEqual(rotate.manifest_snapshot_overflow(root), ["FINDINGS.md"])

    def test_an_undeclared_map_returns_empty_not_a_guess(self):
        root = self._manifest("close:", "  snapshot:", "    file: config/STATUS.yaml")
        self.assertEqual(rotate.manifest_snapshot_overflow(root), [])

    def test_an_absent_manifest_returns_empty(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="rot-ovf-none-"))
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(rotate.manifest_snapshot_overflow(root), [])

    def test_the_report_names_the_declared_destinations(self):
        """The whole point: the overage message must say where to put it."""
        root = self._manifest("close:", "  snapshot:", "    file: config/STATUS.yaml",
                              "    overflow_to: [FINDINGS.md, tasks/closed/]")
        self._status(root)
        out = self._report(root)
        self.assertIn("FINDINGS.md", out)
        self.assertIn("tasks/closed/", out)

    def test_an_undeclared_instance_still_gets_advice(self):
        """Degrade to generic guidance — never to silence, and never to a
        destination this instance did not declare."""
        root = self._manifest("close:", "  snapshot:", "    file: config/STATUS.yaml")
        self._status(root)
        out = self._report(root)
        self.assertIn("TRIM IT", out)
        self.assertNotIn("FINDINGS.md", out)

    def test_the_overage_demands_arrival_rather_than_shrinkage(self):
        """MP#79's third criterion, for the case a script cannot perform. BOTH
        recorded failures deleted text under an "it is recorded elsewhere" rationale
        that was false and unchecked — the second by a fresh session that had just
        read the row about the first. So the success condition named here is the
        grep, not the byte count."""
        root = self._manifest("close:", "  snapshot:", "    overflow_to: FINDINGS.md")
        self._status(root)
        out = self._report(root)
        self.assertIn("ARRIVAL, not shrinkage", out)
        self.assertIn("BEFORE cutting it", out)


class TestLineEndingsSurviveAWrite(unittest.TestCase):
    """Found in the pre-v1.1.1 bug pass, in the shipped writer with the widest
    blast radius.

    `read()` is universal-newline, so a CRLF file arrives as "\\n"; writing that
    back converted EVERY line ending in the file — a whole-file rewrite disguised as
    moving one journal block. Windows git defaults to autocrlf=true, so a CRLF
    working tree is the ordinary state for the platform this was written on.

    `verify_moves` could not catch it: it re-reads through the same universal-newline
    path, where both versions decode identically. Third checker this session that was
    blind to the exact failure it existed to catch."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="rot-eol-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _file(self, data):
        p = os.path.join(self.root, "f.md")
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_a_crlf_file_stays_crlf(self):
        p = self._file(b"one\r\ntwo\r\nthree\r\n")
        rotate.atomic_write(p, "one\nTWO\nthree\n")
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), b"one\r\nTWO\r\nthree\r\n")

    def test_an_lf_file_stays_lf(self):
        p = self._file(b"one\ntwo\n")
        rotate.atomic_write(p, "one\nTWO\n")
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), b"one\nTWO\n")

    def test_a_new_file_is_written_as_given(self):
        p = os.path.join(self.root, "new.md")
        rotate.atomic_write(p, "a\nb\n")
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), b"a\nb\n")

    def test_content_already_carrying_crlf_is_not_doubled(self):
        p = self._file(b"one\r\n")
        rotate.atomic_write(p, "one\r\ntwo\r\n")
        with open(p, "rb") as fh:
            self.assertNotIn(b"\r\r\n", fh.read())

    def test_a_journal_rotation_on_a_crlf_ledger_leaves_it_crlf(self):
        """End to end, not just the primitive: the operation that surfaced it."""
        led = os.path.join(self.root, "MERGE_PLAN.md")
        history = os.path.join(self.root, "HISTORY.md")
        with open(led, "wb") as fh:
            fh.write(ledger(range(4), comment=True).replace("\n", "\r\n").encode("utf-8"))
        moved = rotate.rotate_journal(led, history, keep=1, apply_=True,
                                      journal_max=10 ** 6)
        self.assertTrue(moved, "fixture did not rotate anything — test proves nothing")
        with open(led, "rb") as fh:
            data = fh.read()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\r\r\n", data)
        lf_only = data.replace(b"\r\n", b"")
        self.assertNotIn(b"\n", lf_only, "some lines lost their CR")


class TestOverCapManifest(unittest.TestCase):
    """The manifest gets a MESSAGE and no declared destination — ruled 2026-08-15
    (Michael), against MP#79's criterion applied mechanically.

    The journal and snapshot keys route CONTENT that legitimately outgrows its file.
    A manifest over its cap has none: `declaration_only` says what is over was never
    supposed to be in it, so a declared home for it would read as permission for it
    to exist — and the key would cost every adopter bytes in the very file that is
    over cap, to improve one warning for a case that has never fired."""

    def test_an_over_cap_manifest_is_told_to_move_not_to_shrink(self):
        findings, notes = [], []
        rotate._check_caps("", [], [("4SYNC.yaml", 20000, 16384)], findings, notes)
        self.assertEqual(len(findings), 1)
        rendered = " ".join(str(f) for f in findings)
        self.assertIn("TRIM IT BY MOVING, NOT DELETING", rendered)
        self.assertIn("DECLARATION ONLY", rendered)

    def test_it_does_not_send_anyone_to_a_declared_key(self):
        """The key does not exist and must not be advertised: a message naming a
        setting nobody can set is worse than the vague advice it replaced."""
        findings, notes = [], []
        rotate._check_caps("", [], [("4SYNC.yaml", 20000, 16384)], findings, notes)
        self.assertNotIn("overflow_to", " ".join(str(f) for f in findings))

    def test_a_manifest_under_its_cap_is_not_a_finding(self):
        findings, notes = [], []
        rotate._check_caps("", [], [("4SYNC.yaml", 12000, 16384)], findings, notes)
        self.assertEqual(findings, [])


class TestDeclaredNames(ManifestEnvCase):
    """MP#83. The manifest declared the ledger's name in THREE keys and both scripts
    carried it as a string literal — MP#34's defect one line up, where `rotate.py`
    hardcoded the journal-history filename that `overflow_to` already declared."""

    def _manifest(self, *lines):
        root = os.path.realpath(tempfile.mkdtemp(prefix="rot-names-"))
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("".join(l + chr(10) for l in lines))
        return root

    def test_the_ledger_name_comes_from_ledger_sync(self):
        root = self._manifest("close:", "  ledger_sync:", "    file: TASKS.md")
        self.assertEqual(rotate.manifest_ledger_name(root), "TASKS.md")

    def test_the_journal_key_answers_when_ledger_sync_is_silent(self):
        root = self._manifest("close:", "  journal:", "    file: PLAN.md",
                              "    section: '## Session journal'")
        self.assertEqual(rotate.manifest_ledger_name(root), "PLAN.md")

    def test_an_undeclared_ledger_falls_back_to_the_shipped_name(self):
        root = self._manifest("close:", "  journal:", "    keep: 5")
        self.assertEqual(rotate.manifest_ledger_name(root), rotate.LEDGER_FILENAME)

    def test_the_bulletin_name_is_declared_and_its_archive_is_derived(self):
        root = self._manifest("close:", "  bulletin:", "    file: BOARD.md")
        self.assertEqual(rotate.manifest_bulletin_name(root), "BOARD.md")
        self.assertEqual(rotate.archive_name("BOARD.md"), "BOARD_ARCHIVE.md")

    def test_an_absent_manifest_leaves_every_default_standing(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="rot-names-none-"))
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(rotate.manifest_ledger_name(root), rotate.LEDGER_FILENAME)
        self.assertEqual(rotate.manifest_bulletin_name(root), rotate.BULLETIN_FILENAME)
        self.assertEqual(rotate.manifest_task_prefix(root), rotate.TASK_PREFIX_DEFAULT)


class TestTaskPrefix(ManifestEnvCase):
    """The prefix is DERIVED from instance.name and merely SWITCHED ON by the
    manifest — nobody types the code, so it cannot drift from the instance it
    names, and an adopter cannot pick one that collides with MP by accident."""

    def _manifest(self, *lines):
        root = os.path.realpath(tempfile.mkdtemp(prefix="rot-pfx-"))
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("".join(l + chr(10) for l in lines))
        return root

    def test_the_shipped_default_is_MP(self):
        root = self._manifest("instance:", "  name: 4SYNC", "close:", "  tasks:",
                              "    dir: tasks")
        self.assertEqual(rotate.manifest_task_prefix(root), "MP")

    def test_opting_in_derives_the_code_from_the_instance_name(self):
        root = self._manifest("instance:", "  name: 4SYNC", "close:", "  tasks:",
                              "    prefix: derived")
        self.assertEqual(rotate.manifest_task_prefix(root), "SYN")

    def test_leading_non_letters_are_stripped_before_the_first_three(self):
        root = self._manifest("instance:", "  name: 4CITE", "close:", "  tasks:",
                              "    prefix: derived")
        self.assertEqual(rotate.manifest_task_prefix(root), "CIT")

    def test_a_value_other_than_derived_is_not_a_configured_prefix(self):
        """Michael's ruling: derived, not configured. A literal here would be a
        second place the code lives and a first chance for it to drift."""
        root = self._manifest("instance:", "  name: 4SYNC", "close:", "  tasks:",
                              "    prefix: ZZZ")
        self.assertEqual(rotate.manifest_task_prefix(root), "MP")

    def test_a_name_with_no_letters_degrades_to_the_default(self):
        root = self._manifest("instance:", "  name: '4444'", "close:", "  tasks:",
                              "    prefix: derived")
        self.assertEqual(rotate.manifest_task_prefix(root), "MP")

    def test_the_prefix_derives_without_pyyaml_too(self):
        """THE FINDING FROM THE PRE-v1.1.1 AUDIT. `instance:` is a TOP-LEVEL key
        and `_block_under` requires one space of indent by design, so the regex
        fallback could not see it: `prefix: derived` resolved to SYN with PyYAML
        and MP without — same manifest, two answers, no error anywhere. PyYAML-
        absent is the modal adopter install, so the broken path was the COMMON one,
        and the checkers downstream (status-refs among them) would have quietly
        keyed to the wrong prefix on every such box."""
        root = self._manifest("instance:", "  name: 4SYNC", "close:", "  tasks:",
                              "    prefix: derived")
        with no_pyyaml():
            self.assertEqual(rotate.manifest_task_prefix(root), "SYN")

    def test_the_switch_reads_without_pyyaml_too(self):
        root = self._manifest("instance:", "  name: 4SYNC", "close:", "  tasks:",
                              "    dir: tasks")
        with no_pyyaml():
            self.assertEqual(rotate.manifest_task_prefix(root), "MP")

    def test_ledger_and_bulletin_resolve_without_pyyaml_too(self):
        root = self._manifest("close:", "  journal:", "    file: PLAN.md",
                              "  bulletin:", "    file: BOARD.md")
        with no_pyyaml():
            self.assertEqual(rotate.manifest_ledger_name(root), "PLAN.md")
            self.assertEqual(rotate.manifest_bulletin_name(root), "BOARD.md")


class TestPrefixedDocumentResolution(unittest.TestCase):
    """NOTHING IS EVER RENAMED (Michael, 2026-08-14), so a MIXED directory is the
    steady state here forever — not a migration in progress. The 78 documents in
    tasks/closed/ keep `MP-0NN.md` permanently, every cross-reference in STATUS,
    the ledger, FINDINGS and the journal stays valid, and the backward-compatibility
    problem is DELETED rather than solved."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="rot-mixed-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")
        with open(self.ledger, "w", encoding="utf-8", newline="") as fh:
            fh.write(TASK_LEDGER)
        self.tasks = os.path.join(self.root, "tasks")
        self.closed = os.path.join(self.tasks, "closed")
        os.makedirs(self.closed)

    def _doc(self, name, body="body"):
        p = os.path.join(self.tasks, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body + "\n")
        return p

    def test_doc_names_is_prefixed_then_legacy(self):
        self.assertEqual(rotate.doc_names(83, "SYN"), ["SYN-083.md", "MP-083.md"])

    def test_the_default_prefix_yields_one_name_not_a_duplicate(self):
        self.assertEqual(rotate.doc_names(83), ["MP-083.md"])

    def test_a_legacy_document_is_found_under_a_prefixed_instance(self):
        self._doc("MP-002.md")
        name, live, _ = rotate.find_doc(self.tasks, self.closed, 2, "SYN")
        self.assertEqual(name, "MP-002.md")
        self.assertTrue(os.path.exists(live))

    def test_the_prefixed_name_wins_when_both_exist(self):
        self._doc("MP-002.md")
        self._doc("SYN-002.md")
        name, _, _ = rotate.find_doc(self.tasks, self.closed, 2, "SYN")
        self.assertEqual(name, "SYN-002.md")

    def test_a_new_document_takes_the_prefixed_name(self):
        name, _, _ = rotate.find_doc(self.tasks, self.closed, 9, "SYN")
        self.assertEqual(name, "SYN-009.md")

    def test_a_legacy_row_closes_under_its_legacy_name(self):
        """THE ONE THAT MATTERS. Row 1 is terminal and its document is MP-001.md;
        it must land in closed/ as MP-001.md. A close that renamed on the way out
        would break every reference written before the switchover, at exactly the
        moment the row leaves the ledger and stops being watched."""
        self._doc("MP-001.md", "closed thing")
        self._doc("SYN-002.md", "open thing")     # the mixed directory, in one line
        self._doc("MP-027.md", "the other open row")
        moved, missing = rotate.rotate_task_docs(self.root, self.ledger, True, "SYN")
        self.assertEqual(missing, [])
        self.assertTrue(os.path.exists(os.path.join(self.closed, "MP-001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.closed, "SYN-001.md")))
        self.assertEqual([n for _, n, _, _ in moved], ["MP-001.md"])

    def test_an_open_row_with_neither_name_is_still_reported(self):
        self._doc("MP-001.md")
        self._doc("SYN-027.md")
        moved, missing = rotate.rotate_task_docs(self.root, self.ledger, False, "SYN")
        self.assertEqual([t for t, _, _ in missing], [2])

    def test_the_missing_report_names_the_prefixed_form(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rotate.rotate_task_docs(self.root, self.ledger, False, "SYN")
        self.assertIn("SYN-002.md", buf.getvalue())


class TestRowReferencesUnderAPrefix(unittest.TestCase):
    """A checker keyed only to `MP#` would go blind on every row written after the
    switchover WHILE STILL REPORTING GREEN — a check whose failure is
    indistinguishable from its success, which is this project's recurring defect."""

    def test_the_default_pattern_is_unchanged(self):
        self.assertIs(rotate.status_ref_re(), rotate.STATUS_REF_RE)

    def test_a_prefixed_instance_matches_both_forms(self):
        found = rotate.status_ref_re("SYN").findall("MP#79 stands and SYN-083 is new")
        self.assertEqual(found, ["79", "083"])

    def test_the_repo_name_is_not_a_row_reference(self):
        """`4SYNC-ARCH` must not read as row ARCH of instance 4SY — the word
        boundary is what stops it, and it is worth a test because the false
        positive would be silent."""
        self.assertEqual(rotate.status_ref_re("SYN").findall("4SYNC-ARCH v1.1.1"), [])


class ResolveManifestFallbackCase(unittest.TestCase):
    """resolve_manifest: ARCH_MANIFEST → default name → content discovery.

    SYN-088, cold trial: hand-run in a genesis-renamed instance with ARCH_MANIFEST
    unset, this script fell back to the default filename, found nothing, and said
    "no STATUS file declared or found — skipped" — indistinguishable from an
    instance that declares no STATUS — while report_manifest_at_rest FOUND the
    renamed manifest in the same run, because it globs by content. A resolver
    that can be out-known by a later line of its own report is worse than none.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_resolve_")
        self.addCleanup(shutil.rmtree, self.root, True)
        prev = os.environ.pop("ARCH_MANIFEST", None)
        if prev is not None:
            self.addCleanup(os.environ.__setitem__, "ARCH_MANIFEST", prev)

    def _manifest(self, name):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write('sync_version: "1.0"\n\nboot:\n  - MERGE_PLAN.md\n')

    def test_env_pin_wins(self):
        os.environ["ARCH_MANIFEST"] = "PINNED.yaml"
        self.addCleanup(os.environ.pop, "ARCH_MANIFEST", None)
        self._manifest("PINNED.yaml")
        name, how = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "PINNED.yaml")
        self.assertIn("ARCH_MANIFEST", how)

    def test_env_pin_with_missing_file_is_loud_not_healed(self):
        os.environ["ARCH_MANIFEST"] = "GONE.yaml"
        self.addCleanup(os.environ.pop, "ARCH_MANIFEST", None)
        self._manifest("REAL.yaml")   # discoverable, but the pin must NOT silently heal
        name, how = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "GONE.yaml")
        self.assertIn("MISSING", how)

    def test_default_name_found(self):
        self._manifest("4SYNC.yaml")
        name, _ = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "4SYNC.yaml")

    def test_renamed_manifest_discovered_by_content(self):
        self._manifest("TRELLIS.yaml")
        name, how = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "TRELLIS.yaml")
        self.assertIn("discovered", how)

    def test_non_manifest_yaml_is_not_discovered(self):
        with open(os.path.join(self.root, "ci.yaml"), "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  build:\n    steps: []\n")
        name, _ = rotate.resolve_manifest(self.root)
        self.assertIsNone(name)

    def test_boot_on_the_first_line_is_discovered(self):
        """`"\nboot:" in head` required a PRECEDING newline, so a manifest whose
        FIRST line is `boot:` was invisible to discovery and reported as no
        manifest — the exact silence SYN-088 closed one layer up. Nothing in the
        format requires a key before `boot:`."""
        with open(os.path.join(self.root, "FIRST.yaml"), "w", encoding="utf-8") as fh:
            fh.write('boot:\n  - config/KERNEL.yaml\nsync_version: "1.0"\n')
        name, how = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "FIRST.yaml")
        self.assertIn("discovered", how)

    def test_manifest_behind_a_long_prologue_is_discovered(self):
        """Discovery read a fixed 4,096-byte head. A manifest may legally carry a
        longer comment prologue than that — the declared cap is 16,384 — so both
        marker keys could sit past the window and the file read as a non-manifest.
        The bound must exceed what a manifest is allowed to be, not undercut it."""
        with open(os.path.join(self.root, "PROLOGUE.yaml"), "w", encoding="utf-8") as fh:
            fh.write("# prologue line\n" * 400)      # ~6,000 B before any key
            fh.write('sync_version: "1.0"\n\nboot:\n  - config/KERNEL.yaml\n')
        name, _ = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "PROLOGUE.yaml")

    def test_nothing_to_find(self):
        name, _ = rotate.resolve_manifest(self.root)
        self.assertIsNone(name)

    def test_both_names_at_root_warns_about_the_sibling(self):
        """The default wins the ladder, but resolving it SILENTLY while a
        renamed sibling also declares a manifest is how a stale vendored
        default hijacks every check — the how-string must name the sibling."""
        self._manifest("4SYNC.yaml")
        self._manifest("TRELLIS.yaml")
        name, how = rotate.resolve_manifest(self.root)
        self.assertEqual(name, "4SYNC.yaml")
        self.assertIn("TRELLIS.yaml", how)


class DiscoverManifestsNestedCase(unittest.TestCase):
    """A renamed ROOT manifest must not drop nested default-named manifests out
    of at-rest and cap coverage — nested candidates are tried under BOTH the
    resolved name and the default (a vendored pre-genesis checkout ships the
    default; a genesis'd nested instance ships its own rename)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_discover_")
        self.addCleanup(shutil.rmtree, self.root, True)
        prev = os.environ.pop("ARCH_MANIFEST", None)
        if prev is not None:
            self.addCleanup(os.environ.__setitem__, "ARCH_MANIFEST", prev)

    def _manifest(self, rel):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        if os.path.dirname(rel):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('sync_version: "1.0"\n\nboot:\n  - MERGE_PLAN.md\n')

    def test_renamed_root_still_reaches_nested_default(self):
        self._manifest("TRELLIS.yaml")
        self._manifest("product/4SYNC.yaml")
        rels = [r for r, _, _ in rotate.discover_manifests(self.root, "TRELLIS.yaml")]
        self.assertIn("TRELLIS.yaml", rels)
        self.assertIn("product/4SYNC.yaml", rels)

    def test_stale_default_beside_renamed_root_manifest_stays_covered(self):
        """Both names at the ROOT: at-rest and cap coverage must reach both —
        a single-name lookup checked only whichever won resolution."""
        self._manifest("TRELLIS.yaml")
        self._manifest("4SYNC.yaml")
        rels = [r for r, _, _ in rotate.discover_manifests(self.root, "TRELLIS.yaml")]
        self.assertIn("TRELLIS.yaml", rels)
        self.assertIn("4SYNC.yaml", rels)

    def test_resolution_and_reports_never_mutate_environ(self):
        """The removed main() export must stay removed: a discovered manifest is
        an inference, and exporting it makes it a pin that outlives the run.
        This guards the resolver, discovery and the at-rest report — main()'s
        own non-mutation is the code-review invariant this test documents."""
        self._manifest("TRELLIS.yaml")
        rotate.resolve_manifest(self.root)
        rotate.discover_manifests(self.root, "TRELLIS.yaml")
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rotate.report_manifest_at_rest(self.root, "TRELLIS.yaml")
        self.assertIsNone(os.environ.get("ARCH_MANIFEST"))


class NewbornLedgerCase(unittest.TestCase):
    """report_table_prose must not cry wolf on a newborn ledger.

    SYN-088, cold trial: the FIRST rotate an adopter ever ran opened with a
    complaint about a file genesis had just written — 321 B prose / 119 B rows on
    a 1-row ledger. A report that fires on day one of every fresh instance is a
    report adopters learn to ignore.

    WHAT CHANGED (SYN-090): that was first fixed with an absolute 2,048 B floor,
    which bought the newborn's silence by silencing the entire band beneath it —
    every small and every young ledger, permanently. This class now tests the
    same intent through the mechanism that replaced it: the newborn is quiet
    because the template's own comment, footer, bold labels and unfilled
    placeholders are not counted as the adopter's narrative, and a newborn
    carrying REAL narrative that outweighs its rows is now correctly reported.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_newborn_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ledger = os.path.join(self.root, "MERGE_PLAN.md")

    def _write(self, prose):
        rows = "| ID | Status | Subject |\n|---|---|---|\n| 1 | ⏳ | a |\n"
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write("# L\n\n## Summary table\n\n" + rows + "\n" + prose + "\n")

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rotate.report_table_prose(self.ledger)
        return buf.getvalue()

    def test_a_newborn_ledgers_own_scaffolding_stays_calm(self):
        """The day-one file, as genesis writes it: labels and placeholders, no
        narrative. This is the exact shape that produced the false alarm."""
        self._write(
            "**Tally:** [N] tasks total — [X] completed, [Y] pending.\n\n"
            "<!-- rotate.py DERIVES the Tally from the rows and checks it every\n"
            "     run — keep the exact shape above, em dash included. -->\n\n"
            "**Pickup-ready right now (no blockers):** [List the pending rows as "
            "bare `#3`, `#7` plus a one-line note on each.]\n\n"
            "---\n\n*Part of 4SYNC ARCH. Adapt freely.*")
        self.assertNotIn("OVER THRESHOLD", self._run())

    def test_a_newborn_carrying_real_narrative_is_reported(self):
        """THE DEAD ZONE, at the size that used to define it. 300 B of actual
        prose against 56 B of rows is 84% — real bloat on a small ledger, and
        under the old floor it was unreportable no matter how lopsided it got."""
        self._write("**Pickup-ready:** " + "x" * 300)
        self.assertIn("OVER THRESHOLD", self._run())

    def test_real_bloat_still_fires(self):
        self._write("**Pickup-ready:** " + "x" * 4000)
        self.assertIn("OVER THRESHOLD", self._run())


class BulletinCoherenceCase(unittest.TestCase):
    """SYN-090. `agents:` ⇔ `check_at_boot` was prose in the bootstrap, and
    genesis deletes the bootstrap — so nothing checked the pair after an
    instance's first close. Both directions fail silently."""

    ROSTER = ("# Board\n\n## Roster — who this board can address\n\n"
              "| Name | Aliases | Shell | Git |\n|---|---|---|---|\n")

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_coh_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.abba = os.path.join(self.root, "ABBA.md")

    def _write(self, agents, check_at_boot, extra=""):
        rows = "".join("| Agent%d | A%d | yes | yes |\n" % (i, i)
                       for i in range(1, agents + 1))
        with open(self.abba, "w", encoding="utf-8") as fh:
            fh.write(self.ROSTER + rows + extra)
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write('sync_version: "1.0"\n\nboot:\n  - config/KERNEL.yaml\n\n'
                     "close:\n  bulletin:\n    file: ABBA.md\n"
                     "    check_at_boot: %s\n" % ("true" if check_at_boot else "false"))

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = rotate.report_bulletin_coherence(self.root, self.abba, "4SYNC.yaml")
        return got, buf.getvalue()

    def test_multi_agent_with_the_check_on_is_coherent(self):
        self._write(2, True)
        got, out = self._run()
        self.assertEqual(got, (2, True))
        self.assertNotIn("INCOHERENT", out)

    def test_one_agent_with_the_check_off_is_coherent(self):
        self._write(1, False)
        _, out = self._run()
        self.assertNotIn("INCOHERENT", out)

    def test_multi_agent_with_the_check_off_is_reported(self):
        """Mail queues forever and nothing tells the sender."""
        self._write(3, False)
        _, out = self._run()
        self.assertIn("INCOHERENT", out)
        self.assertIn("check_at_boot: true", out)

    def test_a_lone_agent_paying_for_the_scan_is_reported(self):
        self._write(1, True)
        _, out = self._run()
        self.assertIn("INCOHERENT", out)
        self.assertIn("omit the agents: block", out)

    def test_template_placeholder_rows_are_not_agents(self):
        """THE SHIPPED TEMPLATE'S OWN ROWS. ABBA.md ships two placeholder rows —
        `<your git-capable agent>` / `<your bridge-only agent>` — against a
        manifest that correctly ships `check_at_boot: false` and the `agents:`
        block commented. Counting placeholders as agents made this check fire
        `! INCOHERENT` on a coherent template, i.e. on the FIRST close of every
        fresh instance, about files genesis had just written. That is the SYN-088
        cry-wolf failure, and it is the same template-scaffolding mistake the
        prose check already fixed — a placeholder is not content."""
        self._write(0, False, extra="| `<your git-capable agent>` | | yes | yes |\n"
                                    "| `<your bridge-only agent>` | | no | no |\n")
        got, out = self._run()
        self.assertEqual(got[0], 0, "placeholder rows counted as agents")
        self.assertNotIn("INCOHERENT", out)

    def test_a_placeholder_beside_a_real_agent_counts_only_the_real_one(self):
        """Half-completed rosters are the normal mid-genesis state."""
        self._write(1, False, extra="| `<your bridge-only agent>` | | no | no |\n")
        got, _ = self._run()
        self.assertEqual(got[0], 1)

    def test_a_commented_example_row_is_not_an_agent(self):
        """The shipped board shows the row shape inside an HTML comment. Counting
        it would make every fresh one-surface instance read as multi-agent — the
        cry-wolf-on-day-one shape again."""
        self._write(1, False,
                    extra="<!-- a role agent gets a declared Shell:\n"
                          "     | Mailman | | declared | yes | -->\n")
        got, out = self._run()
        self.assertEqual(got[0], 1)
        self.assertNotIn("INCOHERENT", out)

    def test_a_board_with_no_roster_is_skipped_not_guessed(self):
        with open(self.abba, "w", encoding="utf-8") as fh:
            fh.write("# Board\n\nno roster here\n")
        with open(os.path.join(self.root, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write('sync_version: "1.0"\n\nboot:\n  - k\n\nclose:\n  bulletin:\n'
                     "    file: ABBA.md\n    check_at_boot: true\n")
        got, out = self._run()
        self.assertIsNone(got)
        self.assertEqual(out, "")


class ThirdPartyNamesCase(unittest.TestCase):
    """SYN-090. The genesis guard for this was a RULE — it told the session to
    grep for names — and a rule addressed to a practice nobody thinks is wrong
    does not fire: the practice that produced the original leak was deliberate,
    reviewed four times and shipped four times."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rotate_names_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _f(self, name, text):
        p = os.path.join(self.root, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def _run(self, *paths):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            found = rotate.report_third_party_names(self.root, list(paths))
        return found, buf.getvalue()

    def test_each_attribution_shape_is_caught(self):
        for text in ("A defect reported by Fixture Persona on the cold trial.",
                     "This broke in Fixture Persona's repo during the upgrade.",
                     "The fix landed after review (Fixture Persona, 2026-08-14)."):
            found, out = self._run(self._f("t.md", text))
            self.assertEqual(len(found), 1, text)
            self.assertEqual(found[0][3], "Fixture Persona")
            self.assertIn("?", out)

    def test_a_single_first_name_is_not_a_finding(self):
        """The two-word requirement is what makes this quiet enough to ship —
        an owner's own first name runs through this project's prose constantly."""
        found, _ = self._run(self._f("t.md", "Per Michael's ruling the row runs first."))
        self.assertEqual(found, [])

    def test_tooling_proper_nouns_are_allowed(self):
        found, _ = self._run(self._f("t.md", "Measured in Claude Code's ledger."))
        self.assertEqual(found, [])

    def test_it_asks_rather_than_decides(self):
        """Whether a name belongs in a file that may become public is a consent
        question about a human being. The output must not read as a verdict."""
        _, out = self._run(self._f("t.md", "Found by Fixture Persona."))
        self.assertIn("Does this person know", out)
        self.assertIn("Asked, not blocked", out)

    def test_clean_prose_prints_nothing_at_all(self):
        found, out = self._run(self._f("t.md", "The rotate script grew a new check."))
        self.assertEqual(found, [])
        self.assertEqual(out, "")

    def test_an_unreadable_path_is_skipped_not_fatal(self):
        found, _ = self._run(os.path.join(self.root, "does-not-exist.md"))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_rotate.py ═══
