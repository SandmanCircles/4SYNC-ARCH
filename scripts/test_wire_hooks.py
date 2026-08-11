#!/usr/bin/env python3
"""
Stdlib unittest suite for wire_hooks.py — the hook wiring tool.

FIRST SUITE THIS SCRIPT HAS EVER HAD (MP#65). It was the only script in the
product without tests, and it is also the one that shipped wiring half of what
ships — those two facts are not a coincidence worth ignoring.

Run either way:
  python -m unittest test_wire_hooks       # from the scripts/ dir
  python scripts/test_wire_hooks.py        # from the repo root
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wire_hooks as wh  # noqa: E402

EXE = "C:/py/python.exe"
HOOK = "C:/inst/hooks/pre_tool_use.py"
ROOT = "C:/inst"


class TestMerge(unittest.TestCase):
    """The merge fills blanks; it never overwrites a decision."""

    def test_creates_the_pretooluse_entry_from_nothing(self):
        out = wh.merge({}, EXE, HOOK, ROOT, "warn")
        self.assertEqual(len(out["hooks"]["PreToolUse"]), 1)
        self.assertIn("pre_tool_use.py", out["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_both_paths_are_quoted(self):
        """Spaces in either path are the norm on Windows, not an edge case."""
        cmd = wh.merge({}, "C:/Program Files/py.exe", "C:/My Inst/hooks/pre_tool_use.py",
                       ROOT, "warn")["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, '"C:/Program Files/py.exe" "C:/My Inst/hooks/pre_tool_use.py"')

    def test_rerun_is_idempotent_and_never_appends_a_twin(self):
        """Adopters re-run this. Twice must equal once."""
        once = wh.merge({}, EXE, HOOK, ROOT, "warn")
        twice = wh.merge(once, EXE, HOOK, ROOT, "warn")
        self.assertEqual(len(twice["hooks"]["PreToolUse"]), 1)
        self.assertEqual(once, twice)

    def test_a_changed_interpreter_replaces_our_entry_rather_than_adding_one(self):
        once = wh.merge({}, EXE, HOOK, ROOT, "warn")
        moved = wh.merge(once, "D:/other/python.exe", HOOK, ROOT, "warn")
        self.assertEqual(len(moved["hooks"]["PreToolUse"]), 1)
        self.assertIn("D:/other/python.exe",
                      moved["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_an_unrelated_pretooluse_hook_survives(self):
        existing = {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "their_linter.py"}]}]}}
        out = wh.merge(existing, EXE, HOOK, ROOT, "warn")
        cmds = [h["hooks"][0]["command"] for h in out["hooks"]["PreToolUse"]]
        self.assertIn("their_linter.py", cmds)
        self.assertEqual(len(cmds), 2)

    def test_unrelated_top_level_keys_survive(self):
        out = wh.merge({"model": "opus", "permissions": {"allow": ["Bash"]}},
                       EXE, HOOK, ROOT, "warn")
        self.assertEqual(out["model"], "opus")
        self.assertEqual(out["permissions"], {"allow": ["Bash"]})

    def test_an_env_value_the_user_already_set_is_not_overwritten(self):
        out = wh.merge({"env": {"ARCH_HOOKS_MODE": "enforce"}}, EXE, HOOK, ROOT, "warn")
        self.assertEqual(out["env"]["ARCH_HOOKS_MODE"], "enforce")

    def test_a_foreign_sessionstart_block_is_left_alone(self):
        """This script does not write SessionStart — it must not disturb one either."""
        existing = {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "their_boot.py"}]}]}}
        out = wh.merge(existing, EXE, HOOK, ROOT, "warn")
        self.assertEqual(out["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                         "their_boot.py")


class TestBootHookBlock(unittest.TestCase):
    """MP#65. The block is PRINTED, never written — see boot_hook_block's docstring
    for why project-level wiring of the receipt would be worse than none."""

    BOOT = "C:/inst/hooks/session_start.py"

    def test_renders_both_real_paths_already_filled_in(self):
        """The step that actually goes wrong is hand-substituting these."""
        block = wh.boot_hook_block(EXE, self.BOOT)
        self.assertIn(EXE, block)
        self.assertIn(self.BOOT, block)
        self.assertNotIn("/full/path/to/", block)

    def test_it_is_valid_json_and_shaped_as_sessionstart(self):
        parsed = json.loads(wh.boot_hook_block(EXE, self.BOOT))
        self.assertEqual(list(parsed["hooks"].keys()), ["SessionStart"])

    def test_no_matcher_because_sessionstart_is_not_a_tool_event(self):
        entry = json.loads(wh.boot_hook_block(EXE, self.BOOT))["hooks"]["SessionStart"][0]
        self.assertNotIn("matcher", entry)

    def test_the_block_is_never_merged_into_the_settings_file(self):
        """The whole point of the row: printed, not written."""
        out = wh.merge({}, EXE, HOOK, ROOT, "warn")
        self.assertNotIn("SessionStart", out["hooks"])
        self.assertNotIn("session_start.py", json.dumps(out))


class TestBootGuidanceOutput(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wh_test_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "hooks"))

    def _capture(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            wh.print_boot_hook_guidance(self.root, EXE)
        return buf.getvalue()

    def test_says_why_user_level_and_not_project_level(self):
        open(os.path.join(self.root, "hooks", "session_start.py"), "w").close()
        out = self._capture()
        self.assertIn("~/.claude/settings.json", out)
        self.assertIn("OUTSIDE", out)

    def test_tells_the_reader_to_merge_not_replace(self):
        open(os.path.join(self.root, "hooks", "session_start.py"), "w").close()
        self.assertIn("do not replace", self._capture())

    def test_a_checkout_without_the_boot_hook_says_so_rather_than_printing_a_block(self):
        out = self._capture()
        self.assertIn("nothing to say", out)
        self.assertNotIn("SessionStart", out)


class TestInterpreterRefusals(unittest.TestCase):
    """An unwired hook you know about beats a wired hook that does nothing."""

    def test_the_windows_store_stub_is_named_specifically(self):
        why = wh.diagnose("C:/Users/x/AppData/Local/Microsoft/WindowsApps/python.exe")
        self.assertIsNotNone(why)
        self.assertIn("Store", why)

    def test_an_empty_interpreter_is_refused(self):
        self.assertIsNotNone(wh.diagnose(""))

    def test_a_path_that_is_not_a_file_is_refused(self):
        self.assertIsNotNone(wh.diagnose("C:/nope/python.exe"))

    def test_the_running_interpreter_passes_both_checks(self):
        exe = sys.executable.replace("\\", "/")
        self.assertIsNone(wh.diagnose(exe))
        self.assertTrue(wh.interpreter_works(exe))


if __name__ == "__main__":
    unittest.main()
