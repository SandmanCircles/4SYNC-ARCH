#!/usr/bin/env python3
"""
Stdlib unittest suite for pre_tool_use.py — the PreToolUse guard hooks.

Focus: the whole-file guards (g4 STATUS, g5 manifest) must judge the file that
WILL exist after the call, not the payload fragment. Regression cover for the
fragment-vs-file bug, in both directions:

  * an anchored Edit that keeps the file healthy must PASS  (was a false positive:
    a fragment never carries the EOF sentinel, and rarely parses as YAML)
  * an Edit that truncates the file must still BLOCK
  * an Edit that grows the manifest past its own max_bytes must BLOCK
    (was a false negative: the size check measured the fragment)

No network, no third-party deps beyond the PyYAML the guards themselves probe
for. Imports pre_tool_use from the same hooks/ directory. Run either way:

  python -m unittest test_pre_tool_use     # from the hooks/ dir
  python hooks/test_pre_tool_use.py        # from the repo root
"""

import os
import shutil
import sys
import tempfile
import unittest

# Import pre_tool_use.py from the same directory as this test, regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_tool_use as hooks  # noqa: E402


STATUS_YAML = """\
# ═══════════════════════════════════════════════════════════════════════════
# STATUS.yaml — live state. Overwrite-mode snapshot; never a journal.
# ═══════════════════════════════════════════════════════════════════════════

meta:
  status: AUTHORITATIVE

deploy:
  product: "v1.2.0"
  web: "v0.4.1"

focus: "harden the guard hooks"
blockers: []
last_touched: "guard hooks"

# ═══ EOF STATUS.yaml ═══
"""

MANIFEST_YAML = """\
# ═══════════════════════════════════════════════════════════════════════════
# 4SYNC.yaml — instance manifest. PURE DECLARATION: no state, no narrative.
# ═══════════════════════════════════════════════════════════════════════════

sync_version: "1.0"

instance:
  name: "Test Instance"

boot:
  - MERGE_PLAN.md
  - config/KERNEL.yaml

integrity:
  eof_sentinel: "# ═══ EOF <filename> ═══"
  manifest_rules:
    max_bytes: 8192
    declaration_only: true

# ═══ EOF 4SYNC.yaml ═══
"""


def edit_payload(path, old, new, replace_all=False):
    return {"tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": old,
                           "new_string": new, "replace_all": replace_all}}


def multiedit_payload(path, pairs):
    return {"tool_name": "MultiEdit",
            "tool_input": {"file_path": path,
                           "edits": [{"old_string": o, "new_string": n} for o, n in pairs]}}


def write_payload(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


class GuardCase(unittest.TestCase):
    """Builds a throwaway instance root: <root>/config/STATUS.yaml + <root>/4SYNC.yaml."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sync-hooks-test-")
        os.makedirs(os.path.join(self.root, "config"))
        self.status = os.path.join(self.root, "config", "STATUS.yaml")
        self.manifest = os.path.join(self.root, "4SYNC.yaml")
        self._put(self.status, STATUS_YAML)
        self._put(self.manifest, MANIFEST_YAML)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _put(self, path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_guards(self, payload):
        """Drive the same dispatch path main() uses; return the first block reason."""
        tool, path, text, cmd = hooks._extract(payload)
        full = hooks._resulting_content(tool, payload["tool_input"])
        for guard in hooks.GUARDS:
            import inspect
            nargs = len(inspect.signature(guard).parameters)
            reason = guard(tool, path, text, cmd, full) if nargs >= 5 else guard(tool, path, text, cmd)
            if reason:
                return reason
        return None


class TestResultingContent(GuardCase):
    def test_write_returns_its_own_content(self):
        p = write_payload(self.status, "hello")
        self.assertEqual(hooks._resulting_content("Write", p["tool_input"]), "hello")

    def test_edit_replays_against_disk(self):
        p = edit_payload(self.status, 'focus: "harden the guard hooks"', 'focus: "ship it"')
        got = hooks._resulting_content("Edit", p["tool_input"])
        self.assertIn('focus: "ship it"', got)
        self.assertIn("EOF STATUS.yaml", got)          # untouched tail survives
        self.assertNotIn("harden the guard hooks", got)

    def test_multiedit_replays_all_edits_in_order(self):
        p = multiedit_payload(self.status, [('product: "v1.2.0"', 'product: "v1.3.0"'),
                                            ('web: "v0.4.1"', 'web: "v0.5.0"')])
        got = hooks._resulting_content("MultiEdit", p["tool_input"])
        self.assertIn('v1.3.0', got)
        self.assertIn('v0.5.0', got)

    def test_replace_all_flag_is_honored(self):
        self._put(self.status, "a\na\na\n")
        one = hooks._resulting_content("Edit", edit_payload(self.status, "a", "b")["tool_input"])
        every = hooks._resulting_content(
            "Edit", edit_payload(self.status, "a", "b", replace_all=True)["tool_input"])
        self.assertEqual(one, "b\na\na\n")
        self.assertEqual(every, "b\nb\nb\n")

    def test_unreadable_file_yields_none(self):
        p = edit_payload(os.path.join(self.root, "nope.yaml"), "x", "y")
        self.assertIsNone(hooks._resulting_content("Edit", p["tool_input"]))

    def test_unanchorable_edit_yields_none(self):
        p = edit_payload(self.status, "string that is not in the file", "y")
        self.assertIsNone(hooks._resulting_content("Edit", p["tool_input"]))

    def test_empty_old_string_yields_none(self):
        # Edit's create-file semantics — nothing to anchor, so don't guess.
        self.assertIsNone(hooks._resulting_content("Edit", edit_payload(self.status, "", "x")["tool_input"]))


class TestStatusGuard(GuardCase):
    def test_anchored_edit_keeping_sentinel_passes(self):
        """The regression: an anchored Edit is the close protocol's documented
        write mode (4SYNC.yaml close.freshness_check.edit_mode: anchored_only)."""
        reason = self.run_guards(
            edit_payload(self.status, 'focus: "harden the guard hooks"', 'focus: "ship it"'))
        self.assertIsNone(reason)

    def test_anchored_edit_with_yaml_invalid_fragment_passes(self):
        """A fragment need not be standalone-parseable YAML; the FILE must be."""
        reason = self.run_guards(
            edit_payload(self.status, '  product: "v1.2.0"', '  product: "v1.3.0"'))
        self.assertIsNone(reason)

    def test_multiedit_anchored_passes(self):
        reason = self.run_guards(
            multiedit_payload(self.status, [('product: "v1.2.0"', 'product: "v1.3.0"'),
                                            ('blockers: []', 'blockers: ["none"]')]))
        self.assertIsNone(reason)

    def test_edit_that_truncates_the_file_blocks(self):
        """Deleting the tail removes the EOF sentinel — still caught."""
        tail = STATUS_YAML[STATUS_YAML.index("focus:"):]
        reason = self.run_guards(edit_payload(self.status, tail, 'focus: "clipped"\n'))
        self.assertIsNotNone(reason)
        self.assertIn("clipped", reason)

    def test_edit_that_breaks_yaml_blocks(self):
        reason = self.run_guards(
            edit_payload(self.status, 'focus: "harden the guard hooks"', 'focus: "a: b: c'))
        self.assertIsNotNone(reason)
        self.assertIn("YAML", reason)

    def test_edit_bloating_last_touched_blocks(self):
        reason = self.run_guards(
            edit_payload(self.status, 'last_touched: "guard hooks"',
                         'last_touched: "' + ("narrative " * 40) + '"'))
        self.assertIsNotNone(reason)
        self.assertIn("last_touched", reason)

    def test_whole_file_write_still_checked(self):
        reason = self.run_guards(write_payload(self.status, "meta:\n  status: AUTHORITATIVE\n"))
        self.assertIsNotNone(reason)
        self.assertIn("clipped", reason)

    def test_unreadable_target_skips_rather_than_fires(self):
        """Best-effort: no ground truth → stay quiet, never block blind."""
        self.assertIsNone(self.run_guards(
            edit_payload(os.path.join(self.root, "config", "STATUS.yaml.gone"), "x", "y")))


class TestBoringGuard(GuardCase):
    def test_small_anchored_edit_passes(self):
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Renamed Instance"'))
        self.assertIsNone(reason)

    def test_edit_pushing_past_max_bytes_blocks(self):
        """The false negative: the size check used to measure only the fragment."""
        bloat = "\n".join(f"  - filler/path/number/{i}.md" for i in range(400))
        reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                              "  - config/KERNEL.yaml\n" + bloat))
        self.assertIsNotNone(reason)
        self.assertIn("max_bytes", reason)

    def test_raising_max_bytes_in_the_same_edit_is_honored(self):
        """Policy is read from the RESULTING manifest, so a deliberate cap raise
        in the same write lets the growth through."""
        bloat = "\n".join(f"  - filler/path/number/{i}.md" for i in range(400))
        reason = self.run_guards(multiedit_payload(self.manifest, [
            ("  - config/KERNEL.yaml", "  - config/KERNEL.yaml\n" + bloat),
            ("max_bytes: 8192", "max_bytes: 65536"),
        ]))
        self.assertIsNone(reason)

    def test_edit_introducing_a_calendar_date_blocks(self):
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Test Instance"   # set 2026-07-23'))
        self.assertIsNotNone(reason)
        self.assertIn("2026-07-23", reason)


class TestUnaffectedGuards(GuardCase):
    """g1/g2/g3 keep their 4-arg signature and their fragment-level semantics."""

    def test_kernel_guard_still_blocks(self):
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        os.environ.pop("CLAUDE_KERNEL_EDIT", None)
        reason = self.run_guards(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"))
        self.assertIsNotNone(reason)
        self.assertIn("KERNEL", reason)

    def test_abba_guard_judges_the_fragment_not_the_file(self):
        """A new OPEN block without To: is flagged; the guard must not start
        re-flagging pre-existing blocks it wasn't asked to write."""
        abba = os.path.join(self.root, "ABBA.md")
        self._put(abba, "## Board\n\nStatus: OPEN\nBody: legacy message, no To:\n")
        clean = self.run_guards(edit_payload(abba, "## Board", "## Board (renamed)"))
        self.assertIsNone(clean)
        dirty = self.run_guards(edit_payload(abba, "## Board", "## Board\n\nStatus: OPEN\nBody: hi\n"))
        self.assertIsNotNone(dirty)
        self.assertIn("ABBA", dirty)


class TestDebtRecorderScope(unittest.TestCase):
    """The recorder is designed to survive a USER-LEVEL wire, where the hook loads
    for every session on the machine — including sessions working in projects that
    have nothing to do with ARCH. It must write a debt row inside an instance and
    write NOTHING anywhere else."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="debt_scope_")
        self.env = dict(os.environ)
        os.environ.pop("ARCH_DEBT_FILE", None)
        os.environ["ARCH_DEBT"] = "1"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self.env)

    def _write_payload(self, cwd):
        return {"tool_name": "Write", "session_id": "s-test", "cwd": cwd,
                "tool_input": {"file_path": os.path.join(cwd, "note.txt"), "content": "x"}}

    def test_records_inside_an_instance(self):
        inst = os.path.join(self.root, "myproject")
        os.makedirs(os.path.join(inst, hooks.CONFIG_DIR))
        hooks._record_debt(self._write_payload(inst))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))

    def test_records_from_a_subfolder_at_the_instance_root(self):
        inst = os.path.join(self.root, "myproject")
        sub = os.path.join(inst, "web", "src")
        os.makedirs(os.path.join(inst, hooks.CONFIG_DIR))
        os.makedirs(sub)
        hooks._record_debt(self._write_payload(sub))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))
        self.assertFalse(os.path.exists(os.path.join(sub, hooks.DEBT_FILENAME)))

    def test_writes_nothing_outside_an_instance(self):
        """The regression this exists for: with a user-level wire, the cwd fallback
        would drop a .session_debt.tsv into every unrelated project touched."""
        plain = os.path.join(self.root, "some-unrelated-repo")
        os.makedirs(plain)
        hooks._record_debt(self._write_payload(plain))
        self.assertEqual(os.listdir(plain), [], "no instance => no file, anywhere")

    def test_strict_and_lenient_instance_root_differ_only_off_instance(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        self.assertIsNone(hooks._instance_root(plain, strict=True))
        self.assertEqual(hooks._instance_root(plain), os.path.abspath(plain))


class TestG6RootFence(unittest.TestCase):
    """MP#21 — flag a write into a DIFFERENT ARCH instance than the session is in.

    The failure it exists for: a session booted in one instance nearly wrote a
    task row into a separate live instance's ledger, with that ledger's numbering
    learned by grep rather than by boot. The gate that should have caught it
    (close.freshness_check) assumes a single instance and could not."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="g6_")
        # two sibling instances + one ordinary project
        self.inst_a = os.path.join(self.root, "InstanceA")
        self.inst_b = os.path.join(self.root, "InstanceB")
        self.plain = os.path.join(self.root, "just-a-repo")
        for d in (self.inst_a, self.inst_b):
            os.makedirs(os.path.join(d, hooks.CONFIG_DIR))
        os.makedirs(self.plain)
        # a nested repo INSIDE instance A that ships its own config/ — this is
        # not hypothetical: the product repo ships the loader stack, so it has one
        self.nested = os.path.join(self.inst_a, "PRODUCT-REPO")
        os.makedirs(os.path.join(self.nested, hooks.CONFIG_DIR))
        self.logfile = os.path.join(self.root, "warn.log")
        self._old_log = os.environ.get("ARCH_HOOKS_LOG")
        os.environ["ARCH_HOOKS_LOG"] = self.logfile

    def tearDown(self):
        if self._old_log is None:
            os.environ.pop("ARCH_HOOKS_LOG", None)
        else:
            os.environ["ARCH_HOOKS_LOG"] = self._old_log
        shutil.rmtree(self.root, ignore_errors=True)

    def _fence(self, cwd, target):
        return hooks.g6_root_fence("Write", target.lower(), "x", "",
                                   None, {"cwd": cwd, "raw_path": target})

    def _log_text(self):
        if not os.path.exists(self.logfile):
            return ""
        with open(self.logfile, encoding="utf-8") as fh:
            return fh.read()

    def test_in_root_write_passes(self):
        self.assertIsNone(self._fence(self.inst_a, os.path.join(self.inst_a, "MERGE_PLAN.md")))

    def test_nested_repo_with_its_own_config_passes(self):
        """Containment, not equality. The nested product repo ships a config/ dir,
        so an equality test would resolve it as a different instance and flag the
        most common write path in the project."""
        self.assertIsNone(self._fence(self.inst_a, os.path.join(self.nested, "MERGE_PLAN.md")))

    def test_write_up_from_nested_repo_passes(self):
        """Booted in the nested repo, writing to the outer instance — same tree."""
        self.assertIsNone(self._fence(self.nested, os.path.join(self.inst_a, "MERGE_PLAN.md")))

    def test_sibling_instance_ledger_write_is_flagged(self):
        reason = self._fence(self.inst_a, os.path.join(self.inst_b, "MERGE_PLAN.md"))
        self.assertIsNotNone(reason, "the cross-instance write MUST be flagged")
        self.assertIn("CROSS-INSTANCE", reason)
        self.assertIn("InstanceB", reason)

    def test_write_into_a_non_arch_project_is_silent(self):
        """What makes a machine-wide wire safe: no instance at the target => not
        our business, and nothing is logged."""
        self.assertIsNone(self._fence(self.inst_a, os.path.join(self.plain, "notes.md")))
        self.assertEqual(self._log_text(), "")

    def test_lobby_session_write_is_flagged_quietly(self):
        """Session in no instance at all (home-folder drill-down). Returns None so
        it never blocks even under enforce, but records one line."""
        reason = self._fence(self.root, os.path.join(self.inst_a, "config", "KERNEL.yaml"))
        self.assertIsNone(reason, "a lobby drill-down must never block")
        log = self._log_text()
        self.assertIn("QUIET", log)
        self.assertIn("InstanceA", log)

    def test_non_write_tools_are_ignored(self):
        self.assertIsNone(hooks.g6_root_fence(
            "Read", "x", "", "", None,
            {"cwd": self.inst_a, "raw_path": os.path.join(self.inst_b, "MERGE_PLAN.md")}))
        self.assertIsNone(hooks.g6_root_fence(
            "Bash", "", "", "rm -rf /", None,
            {"cwd": self.inst_a, "raw_path": ""}))

    def test_missing_context_is_silent(self):
        """4- and 5-arg call shapes must not make g6 throw or guess."""
        self.assertIsNone(hooks.g6_root_fence("Write", "p", "x", "", None, None))
        self.assertIsNone(hooks.g6_root_fence("Write", "p", "x", "", None, {"cwd": self.inst_a}))

    def test_dispatcher_passes_context_to_six_arg_guards(self):
        """The arity dispatch must reach 6 params, or g6 silently never fires."""
        import inspect
        self.assertGreaterEqual(len(inspect.signature(hooks.g6_root_fence).parameters), 6)
        self.assertIn(hooks.g6_root_fence, hooks.GUARDS)


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_pre_tool_use.py ═══
