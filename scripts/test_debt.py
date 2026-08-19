#!/usr/bin/env python3
"""
Stdlib unittest suite for debt.py — the close-time session-debt clearer.

WHY A SCRIPT OWNS THE CLEAR (SYN-087, observed live on a cold trial): the
recorder in hooks/pre_tool_use.py upserts this session's row on every
file-write TOOL call, so a close that cleared its own row with a file-edit
tool — or made any write-tool call afterwards — silently restored the row,
and the next boot reported phantom debt from a session that closed properly.
A script's writes are invisible to the recorder (its WRITE_TOOLS excludes
Bash), so clearing by script makes the ordering problem disappear instead of
documenting it.

Everything runs in-process (debt.main(argv)) — no subprocess, deliberately:
this box has an intermittent _winapi.DuplicateHandle fault that fails
subprocess-based tests on unmodified code.

Run either way:
  python -m unittest test_debt        # from the scripts/ dir
  python scripts/test_debt.py         # from the repo root
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debt  # noqa: E402

HEADER = ("# 4SYNC session-debt — unwrapped sessions; an explicit close clears "
          "its own row.\n# session_id\tstarted\tlast_activity\tcwd\tstatus\n")


def row(sid):
    return "%s\t2026-08-17T10:00:00\t2026-08-17T10:05:00\tC:\\x\tunwrapped\n" % sid


class ClearCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="debt_clear_")
        import shutil
        self.addCleanup(shutil.rmtree, self.root, True)
        # A nested instance (its own config/KERNEL.yaml) with its own debt file —
        # the manifest's at_close says EVERY debt file under the root, because a
        # session that edits both leaves a row in each.
        self.nested = os.path.join(self.root, "product")
        os.makedirs(os.path.join(self.nested, "config"))
        with open(os.path.join(self.nested, "config", "KERNEL.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("meta:\n  status: AUTHORITATIVE\n")
        prev = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if prev is not None:
            self.addCleanup(os.environ.__setitem__, "CLAUDE_CODE_SESSION_ID", prev)

    def _seed(self, path, sids):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HEADER + "".join(row(s) for s in sids))

    def _run(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = debt.main(list(argv))
        return code, buf.getvalue()

    def test_clears_own_row_everywhere_and_keeps_others(self):
        top = os.path.join(self.root, ".session_debt.tsv")
        deep = os.path.join(self.nested, ".session_debt.tsv")
        self._seed(top, ["mine", "theirs"])
        self._seed(deep, ["mine"])
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(top, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("mine", text)
        self.assertIn("theirs", text)          # other sessions' rows survive
        self.assertIn("# session_id", text)    # header survives
        with open(deep, encoding="utf-8") as fh:
            self.assertNotIn("mine", fh.read())
        self.assertIn("cleared", out)

    def test_no_own_row_reports_and_exits_zero(self):
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["theirs"])
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)              # bookkeeping never blocks a close
        self.assertIn("no own row", out)

    def test_cleared_nothing_but_rows_exist_is_loud(self):
        """The id-mismatch case, observed live: in a NESTED claude run the env
        var carries the PARENT session's id, so --clear targets a row that does
        not exist while the real row sits one line away — and "no own row" reads
        like success. When nothing was cleared anywhere but unwrapped rows
        exist, the tool must say so and name them, so the closing session can
        recognize its own row and re-run with --session."""
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["48edbd23-real"])
        code, out = self._run("--clear", "--dir", self.root, "--session", "wrong-id")
        self.assertEqual(code, 0)
        self.assertIn("48edbd23-real", out)    # the surviving row is NAMED
        self.assertIn("--session", out)        # and the remedy is named too

    def test_session_id_from_environment(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "env-sid"
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["env-sid"])
        code, _ = self._run("--clear", "--dir", self.root)
        self.assertEqual(code, 0)
        with open(top, encoding="utf-8") as fh:
            self.assertNotIn("env-sid", fh.read())

    def test_unknown_session_refuses(self):
        code, out = self._run("--clear", "--dir", self.root)
        self.assertEqual(code, 2)
        self.assertIn("session", out.lower())

    def test_no_debt_files_is_fine(self):
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)

    def test_report_mode_lists_rows_without_writing(self):
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["a", "b"])
        code, out = self._run("--dir", self.root)
        self.assertEqual(code, 0)
        self.assertIn("a", out)
        with open(top, encoding="utf-8") as fh:
            self.assertIn("a", fh.read())      # report mode wrote nothing

    def test_arch_debt_file_override_is_cleared(self):
        """The recorder honors ARCH_DEBT_FILE (relocated debt file); the clear
        must too, or the documented override path silently reintroduces the
        phantom-debt failure this script exists to close."""
        alt = os.path.join(self.root, "elsewhere", "relocated_debt.tsv")
        os.makedirs(os.path.dirname(alt))
        self._seed(alt, ["mine"])
        os.environ["ARCH_DEBT_FILE"] = alt
        self.addCleanup(os.environ.pop, "ARCH_DEBT_FILE", None)
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(alt, encoding="utf-8") as fh:
            self.assertNotIn("mine", fh.read())
        self.assertIn("cleared", out)

    def test_arch_debt_file_override_appears_in_report(self):
        alt = os.path.join(self.root, "elsewhere", "relocated_debt.tsv")
        os.makedirs(os.path.dirname(alt))
        self._seed(alt, ["a"])
        os.environ["ARCH_DEBT_FILE"] = alt
        self.addCleanup(os.environ.pop, "ARCH_DEBT_FILE", None)
        code, out = self._run("--dir", self.root)
        self.assertEqual(code, 0)
        self.assertIn("a", out)

    def test_non_utf8_bytes_never_crash_and_are_preserved(self):
        """A hand-edited debt file can carry non-UTF-8 bytes (cp1252 cwd). The
        clear must not crash — bookkeeping never blocks a close — and must
        preserve the alien bytes it does not own, byte for byte."""
        top = os.path.join(self.root, ".session_debt.tsv")
        with open(top, "wb") as fh:
            fh.write(HEADER.encode("utf-8"))
            fh.write(b"theirs\t2026-08-17T10:00:00\t2026-08-17T10:05:00\tC:\\\xfc\tunwrapped\n")
            fh.write(row("mine").encode("utf-8"))
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(top, "rb") as fh:
            data = fh.read()
        self.assertNotIn(b"mine\t", data)
        self.assertIn(b"C:\\\xfc", data)        # alien byte preserved exactly
        code, _ = self._run("--dir", self.root)  # report mode must not crash either
        self.assertEqual(code, 0)

    def test_clear_is_atomic_no_tmp_left_behind(self):
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["mine", "theirs"])
        code, _ = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(top + ".tmp"))

    def test_lone_cr_in_foreign_row_is_preserved(self):
        """bytes.splitlines also splits on a lone \\r — which let a foreign row
        carrying a stray 0x0D, whose post-CR bytes start with `sid\\t`, be
        truncated at the CR. Splitting on \\n only keeps the promise: every
        byte that is not this session's row survives exactly."""
        top = os.path.join(self.root, ".session_debt.tsv")
        payload = (b"sid-B\tnote-with\rmine\ttail\n"     # one FOREIGN line, embedded CR
                   b"mine\t2026-08-17T10:00:00\t2026-08-17T10:05:00\tC:\\x\tunwrapped\n")
        with open(top, "wb") as fh:
            fh.write(HEADER.encode("utf-8") + payload)
        code, _ = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(top, "rb") as fh:
            data = fh.read()
        self.assertIn(b"sid-B\tnote-with\rmine\ttail\n", data)   # foreign row intact
        self.assertNotIn(b"\nmine\t", data)                      # own row gone

    def test_no_trailing_newline_final_row_still_cleared(self):
        top = os.path.join(self.root, ".session_debt.tsv")
        with open(top, "wb") as fh:
            fh.write(HEADER.encode("utf-8") + b"mine\ta\tb\tc\tunwrapped")  # no \n
        code, _ = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(top, "rb") as fh:
            self.assertNotIn(b"mine\t", fh.read())

    def test_git_dir_is_not_walked(self):
        gitdir = os.path.join(self.root, ".git")
        os.makedirs(gitdir)
        self._seed(os.path.join(gitdir, ".session_debt.tsv"), ["mine"])
        code, out = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(os.path.join(gitdir, ".session_debt.tsv"), encoding="utf-8") as fh:
            self.assertIn("mine", fh.read())   # untouched


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_debt.py ═══
