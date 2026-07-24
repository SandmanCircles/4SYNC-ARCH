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


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_pre_tool_use.py ═══
