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


class TestSettingsRoot(unittest.TestCase):
    """MP#64 — where Claude Code ACTUALLY reads settings for this instance.

    The defect: this script wrote to the instance root unconditionally, which is
    right only when the instance is also the repository root. ARCH in a subfolder
    of an existing codebase — the shape of every adoption that adds ARCH to a
    project rather than starting from an empty folder — got a settings file
    nothing ever reads, reported as success."""

    def setUp(self):
        # REALPATH, NOT JUST mkdtemp (MP#78). `settings_root` reports what git says,
        # and `git rev-parse --show-toplevel` returns the CANONICAL path. On macOS
        # `tempfile.mkdtemp()` hands back `/var/folders/...`, where `/var` is a
        # symlink to `/private/var` — so git answered `/private/var/...`, the
        # fixture expected `/var/...`, and three tests here failed on every macOS
        # box while passing on Linux and Windows. Reported by an adopter running a
        # Laravel app with ARCH in a subfolder, 2026-08-11.
        #
        # THE PRODUCTION CODE IS CORRECT AND MUST NOT BE "FIXED": the canonical path
        # is the one Claude Code resolves settings against, so git's answer is the
        # right one and the fixture's notion of its own location was wrong.
        # `os.path.abspath` (which wire_hooks uses) normalises a path but does NOT
        # resolve symlinks — only `realpath` does. That is the whole bug.
        #
        # Canonicalised once here rather than at each assertion, because the defect
        # belongs to the fixture's root, not to any individual comparison.
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="wire_root_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _git_init(path):
        import subprocess
        os.makedirs(path, exist_ok=True)
        subprocess.run(["git", "init", "-q", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    def test_instance_that_is_the_repo_root_stays_put(self):
        """The common case, and the one that must not regress: an instance which is
        its own repository keeps writing exactly where it always did."""
        inst = self._git_init(os.path.join(self.root, "solo"))
        sroot, why = wh.settings_root(inst)
        self.assertEqual(os.path.normcase(sroot), os.path.normcase(inst))
        self.assertIn("repository root", why)

    def test_nested_instance_resolves_to_the_repository_root(self):
        """A real adopter layout, 2026-08-10: ARCH at `ops/` under a framework app."""
        proj = self._git_init(os.path.join(self.root, "myapp"))
        ops = os.path.join(proj, "ops")
        os.makedirs(ops)
        sroot, why = wh.settings_root(ops)
        self.assertEqual(os.path.normcase(sroot), os.path.normcase(proj))
        self.assertIn("nested", why)

    def test_a_nested_repo_of_its_own_is_left_alone(self):
        """The case that would look identical to a naive 'is it the top folder?'
        test and must not: the product repo sits inside this silo and IS its own
        repository, so it is its own settings root. Resolving by GIT ROOT gets this
        right with no special case; resolving by 'project root' could not."""
        outer = self._git_init(os.path.join(self.root, "outer"))
        inner = self._git_init(os.path.join(outer, "PRODUCT-REPO"))
        sroot, _ = wh.settings_root(inner)
        self.assertEqual(os.path.normcase(sroot), os.path.normcase(inner))

    def test_outside_a_git_repo_settings_stay_with_the_instance(self):
        """A documented exception, not a fallback: outside a repository Claude Code
        keeps settings in the directory the session starts from."""
        inst = os.path.join(self.root, "no-git")
        os.makedirs(inst)
        sroot, why = wh.settings_root(inst)
        self.assertEqual(os.path.normcase(sroot), os.path.normcase(inst))
        self.assertIn("not a git repository", why)


class TestManifestWiring(unittest.TestCase):
    """MP#64 — ARCH_MANIFEST is wired only when the manifest is not the default.

    Genesis already merges it into `.claude/settings.json`, so this is not a
    universal gap. It bites when the file Claude Code loads is not the one genesis
    wrote — the same nested layout above."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wire_manifest_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _manifest(self, name):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write('sync_version: "1.0"\ninstance:\n  name: "X"\nboot:\n  - config/K.yaml\n')
        return name

    def test_finds_a_renamed_manifest_by_content(self):
        """Genesis renames the manifest per project, so it cannot be found by name
        — which is exactly the case that needs ARCH_MANIFEST set."""
        self._manifest("CRM.yaml")
        self.assertEqual(wh.find_manifest(self.root), "CRM.yaml")

    def test_ignores_yaml_that_is_not_a_manifest(self):
        with open(os.path.join(self.root, "docker-compose.yaml"), "w", encoding="utf-8") as fh:
            fh.write("services:\n  web:\n    image: nginx\n")
        self.assertIsNone(wh.find_manifest(self.root))

    def test_boot_on_the_first_line_is_found(self):
        """Sibling of the rotate.py case: `"\nboot:" in head` needed a preceding
        newline, so a manifest opening with `boot:` was unfindable and wiring
        silently declined to pin ARCH_MANIFEST for a renamed instance."""
        with open(os.path.join(self.root, "FIRST.yaml"), "w", encoding="utf-8") as fh:
            fh.write('boot:\n  - config/K.yaml\nsync_version: "1.0"\n')
        self.assertEqual(wh.find_manifest(self.root), "FIRST.yaml")

    def test_manifest_behind_a_long_prologue_is_found(self):
        """The 4,096-byte head undercut the 16,384-byte manifest cap, so a
        legally-sized prologue could hide both marker keys."""
        with open(os.path.join(self.root, "PROLOGUE.yaml"), "w", encoding="utf-8") as fh:
            fh.write("# prologue line\n" * 400)
            fh.write('sync_version: "1.0"\nboot:\n  - config/K.yaml\n')
        self.assertEqual(wh.find_manifest(self.root), "PROLOGUE.yaml")

    def test_default_named_manifest_is_not_wired(self):
        """Wiring ARCH_MANIFEST=4SYNC.yaml would be a no-op that reads as a
        decision — the hook already defaults to it."""
        merged = wh.merge({}, "py", "/h.py", "/r", "warn", "4SYNC.yaml")
        self.assertNotIn("ARCH_MANIFEST", merged["env"])

    def test_renamed_manifest_is_wired(self):
        merged = wh.merge({}, "py", "/h.py", "/r", "warn", "CRM.yaml")
        self.assertEqual(merged["env"]["ARCH_MANIFEST"], "CRM.yaml")

    def test_an_existing_manifest_choice_is_never_overwritten(self):
        """Same contract as every other env key here: this script fills blanks, it
        does not overwrite decisions."""
        existing = {"env": {"ARCH_MANIFEST": "MINE.yaml"}}
        merged = wh.merge(existing, "py", "/h.py", "/r", "warn", "CRM.yaml")
        self.assertEqual(merged["env"]["ARCH_MANIFEST"], "MINE.yaml")

    def test_no_manifest_found_wires_nothing(self):
        merged = wh.merge({}, "py", "/h.py", "/r", "warn", None)
        self.assertNotIn("ARCH_MANIFEST", merged["env"])


class StatusCase(unittest.TestCase):
    """--status: is THIS machine wired for THIS instance? Verified, not inferred.

    SYN-089, field-reported: an instance git-synced to a second machine boots
    with the entire enforcement layer silently absent — wiring is machine-local
    by design, and the SessionStart receipt is both the announcement channel and
    part of what is missing. The only reliable detector was a session noticing
    an absence. This makes absence a report instead.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wh_status_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "hooks"))
        with open(os.path.join(self.root, "hooks", "pre_tool_use.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("# hook body\n")
        self.user = os.path.join(self.root, "fake_user_settings.json")

    def _run(self, check_interpreter=False):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = wh.status(self.root, user_settings=self.user,
                             check_interpreter=check_interpreter)
        return code, buf.getvalue()

    def _wire_project(self):
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        blob = {"hooks": {"PreToolUse": [{"matcher": "Write|Edit", "hooks": [
                    {"type": "command",
                     "command": '"%s" "%s"' % (sys.executable,
                                               os.path.join(self.root, "hooks",
                                                            "pre_tool_use.py"))}]}]},
                "env": {"ARCH_HOOKS_MODE": "warn"}}
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(blob, fh)

    def test_unwired_machine_reports_unwired_and_exits_1(self):
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("UNWIRED", out)
        self.assertIn("--write", out)          # the fix is named, not implied

    def test_project_level_wire_reports_wired(self):
        self._wire_project()
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("WIRED", out)

    def test_user_level_wire_reports_wired_and_receipt(self):
        blob = {"hooks": {
            "PreToolUse": [{"hooks": [{"type": "command",
                            "command": '"py" "/x/hooks/pre_tool_use.py"'}]}],
            "SessionStart": [{"hooks": [{"type": "command",
                              "command": '"py" "/x/hooks/session_start.py"'}]}]}}
        with open(self.user, "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("user", out.lower())
        self.assertNotIn("receipt: NOT wired", out)

    def test_no_receipt_is_called_out(self):
        self._wire_project()                    # guards wired, receipt not
        _, out = self._run()
        self.assertIn("receipt: NOT wired", out)

    def test_shared_project_settings_json_counts_as_wired(self):
        """Claude Code reads hooks from the SHARED .claude/settings.json too —
        pre_tool_use.py's own docstring says to wire there. Committed team
        wiring must not read as UNWIRED (the false alarm steers a redundant
        --write)."""
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        blob = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                "command": '"py" "/x/hooks/pre_tool_use.py"'}]}]}}
        with open(os.path.join(sdir, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("WIRED", out)

    def test_missing_hook_script_is_a_problem(self):
        """A wiring whose hook script is gone (machine-migration aftermath)
        fails as silently as a dead interpreter — WIRED over it is the exact
        'worse than no hook' state."""
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        blob = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                "command": '"C:/nope/python.exe" "C:/nope/hooks/pre_tool_use.py"'}]}]}}
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(blob, fh)
        code, out = self._run(check_interpreter=True)
        self.assertEqual(code, 1)
        self.assertIn("missing hook script", out)

    def test_receipt_at_project_level_is_found(self):
        self._wire_project()
        sdir = os.path.join(self.root, ".claude")
        with open(os.path.join(sdir, "settings.local.json"), encoding="utf-8") as fh:
            blob = json.load(fh)
        blob["hooks"]["SessionStart"] = [{"hooks": [{"type": "command",
            "command": '"py" "/x/hooks/session_start.py"'}]}]
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(blob, fh)
        _, out = self._run()
        self.assertNotIn("receipt: NOT wired", out)
        self.assertIn("wired at project-local level", out)

    def test_unreadable_file_does_not_hide_other_wiring(self):
        """One trailing comma must not mask valid wiring elsewhere — the old
        early-return reported 'cannot tell' after two lines and never checked
        the user level at all."""
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ not json }")
        blob = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                "command": '"py" "/x/hooks/pre_tool_use.py"'}]}]}}
        with open(self.user, "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("NOT READABLE", out)
        self.assertIn("WIRED", out)

    def test_unreadable_and_nothing_else_is_cannot_tell(self):
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ not json }")
        code, out = self._run()
        self.assertEqual(code, 2)
        self.assertIn("CANNOT TELL", out)

    def test_hooks_as_list_does_not_crash_and_is_flagged(self):
        """The natural paste error — the entry ARRAY dropped in as 'hooks' — is
        valid JSON, so it passed _load_settings and crashed the old iterator
        with AttributeError. A status report must survive what a hand edit can
        produce, and say what it found."""
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        blob = {"hooks": [{"matcher": "Write", "hooks": [
            {"type": "command", "command": '"py" "/x/hooks/pre_tool_use.py"'}]}]}
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(blob, fh)
        code, out = self._run()
        self.assertEqual(code, 2)          # may hold the wiring — cannot tell
        self.assertIn("not a JSON object", out)

    def test_teammates_shared_paths_do_not_sink_a_healthy_local_wiring(self):
        """The shared settings.json is COMMITTED and carries one machine's
        absolute paths — problems there are expected on every other machine
        and must not drag a healthy local wiring to exit 1 (per-source
        verdict, the SYN-089 false alarm)."""
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(sdir, exist_ok=True)
        dead = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                "command": '"C:/other-machine/py.exe" "C:/other-machine/hooks/pre_tool_use.py"'}]}]}}
        with open(os.path.join(sdir, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(dead, fh)
        healthy = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                   "command": '"%s" "%s"' % (sys.executable,
                                             os.path.join(self.root, "hooks",
                                                          "pre_tool_use.py"))}]}]}}
        with open(os.path.join(sdir, "settings.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(healthy, fh)
        code, out = self._run(check_interpreter=True)
        self.assertEqual(code, 0)
        self.assertIn("PROBLEM (project)", out)   # reported, not fatal
        self.assertIn("WIRED (project-local)", out)

    def test_settings_path_that_is_a_directory_is_cannot_tell(self):
        sdir = os.path.join(self.root, ".claude")
        os.makedirs(os.path.join(sdir, "settings.local.json"))   # a DIRECTORY
        code, out = self._run()
        self.assertEqual(code, 2)
        self.assertIn("NOT READABLE", out)


class CommandTokenCase(unittest.TestCase):
    """Hand-written wirings use double quotes, single quotes, or none — the
    parser must read all three, or --status misjudges working wirings."""

    def test_double_quoted(self):
        cmd = '"C:/py/python.exe" "C:/inst/hooks/pre_tool_use.py"'
        self.assertEqual(wh._command_exe(cmd), "C:/py/python.exe")
        self.assertEqual(wh._command_hook_path(cmd), "C:/inst/hooks/pre_tool_use.py")

    def test_single_quoted(self):
        cmd = "'/usr/bin/python3' '/repo/hooks/pre_tool_use.py'"
        self.assertEqual(wh._command_exe(cmd), "/usr/bin/python3")
        self.assertEqual(wh._command_hook_path(cmd), "/repo/hooks/pre_tool_use.py")

    def test_unquoted(self):
        cmd = "C:/py/python.exe C:/inst/hooks/pre_tool_use.py"
        self.assertEqual(wh._command_exe(cmd), "C:/py/python.exe")
        self.assertEqual(wh._command_hook_path(cmd), "C:/inst/hooks/pre_tool_use.py")

    def test_empty(self):
        self.assertEqual(wh._command_exe(""), "")
        self.assertEqual(wh._command_hook_path(""), "")


class MergeMalformedCase(unittest.TestCase):
    """--write must refuse a 'hooks' key that is not an object instead of
    silently merging garbage into it (verified corruption: the list's keys
    iterated as a dict yields {'matcher': 'hooks'})."""

    def test_hooks_malformed_detector(self):
        self.assertTrue(wh._hooks_malformed({"hooks": []}))
        self.assertTrue(wh._hooks_malformed({"hooks": "x"}))
        self.assertFalse(wh._hooks_malformed({"hooks": {}}))
        self.assertFalse(wh._hooks_malformed({}))
        self.assertFalse(wh._hooks_malformed(None))


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_wire_hooks.py ═══
