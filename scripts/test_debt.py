#!/usr/bin/env python3
"""
Stdlib unittest suite for debt.py — the close-time session-debt clearer.

WHY A SCRIPT OWNS THE CLEAR: see debt.py's own module docstring, which is the
canonical telling (SYN-087). It was retold in six places and had already begun
to diverge in wording; one copy is maintainable, six are not.

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
        exist, the tool must say so and name them."""
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["48edbd23-real"])
        code, out = self._run("--clear", "--dir", self.root, "--session", "wrong-id")
        self.assertEqual(code, 0)
        self.assertIn("48edbd23-real", out)    # the surviving row is NAMED
        self.assertIn("BOOT RECEIPT", out)     # and the id is SOURCED, not guessed

    def test_the_miss_never_invites_picking_a_row(self):
        """SYN-090. This block used to end "re-run with --session <that id>",
        which invites choosing from a list whose other entries may belong to
        sessions that are LIVE right now. A deleted row is the only evidence
        that session was working — the one thing the tracker exists to keep."""
        top = os.path.join(self.root, ".session_debt.tsv")
        self._seed(top, ["live-other-session"])
        _, out = self._run("--clear", "--dir", self.root, "--session", "wrong-id")
        self.assertIn("DO NOT pick one", out)
        self.assertIn("LIVE right now", out)

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

    def _seed_at(self, *parts):
        """Seed a debt file at root/<parts...>/ and return its path."""
        d = os.path.join(self.root, *parts)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, ".session_debt.tsv")
        self._seed(p, ["mine"])
        return p

    def test_build_and_dist_are_not_walked(self):
        """SKIP_DIRS here was a THIRD divergent copy of a set rotate.py and
        meter.py both carry — this one silently omitted `dist` and `build`
        (SYN-090). A build artefact tree is not instance state, and clearing a
        row out of one is a write into generated output."""
        paths = [self._seed_at("build"), self._seed_at("dist")]
        code, _ = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        for p in paths:
            with open(p, encoding="utf-8") as fh:
                self.assertIn("mine", fh.read(), p + " was walked")

    def test_dot_directories_are_not_walked(self):
        """Named dot-dirs were skipped one at a time, so every dot-dir nobody
        listed was walked — `.cache`, `.tox`, `.next`, `.terraform`. Pruning the
        whole class is the fix that does not need a maintainer to keep guessing."""
        p = self._seed_at(".cache")
        code, _ = self._run("--clear", "--dir", self.root, "--session", "mine")
        self.assertEqual(code, 0)
        with open(p, encoding="utf-8") as fh:
            self.assertIn("mine", fh.read())

    def test_walk_is_depth_bounded(self):
        """os.walk over the WHOLE instance root ran at every close — multi-second
        on a large adopter repo, for a file that only ever sits at an instance
        root. Three levels reaches any nested-instance layout; the same bound and
        the same reason as rotate.py's MAX_REPO_DEPTH."""
        shallow = self._seed_at("a", "b", "c")            # depth 3 — still found
        deep = self._seed_at("a", "b", "c", "d")          # depth 4 — out of range
        found = debt.find_debt_files(self.root)
        norm = [os.path.normcase(os.path.abspath(f)) for f in found]
        self.assertIn(os.path.normcase(os.path.abspath(shallow)), norm)
        self.assertNotIn(os.path.normcase(os.path.abspath(deep)), norm)

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
