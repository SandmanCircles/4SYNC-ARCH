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

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

# Import pre_tool_use.py from the same directory as this test, regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_tool_use as hooks  # noqa: E402

# Is PyYAML on this box? NOT a preference — it is absent from every fresh Python
# install, so the no-PyYAML path below is the DEFAULT adopter experience, not an
# edge case. Anything asserting YAML-parse strictness must skip without it; what
# the stdlib-only path still guarantees is covered positively instead.
try:
    import yaml  # type: ignore # noqa: F401
    HAS_YAML = True
except Exception:  # noqa: BLE001
    HAS_YAML = False


@contextlib.contextmanager
def no_pyyaml():
    """Run a block as if PyYAML were not installed, on any box.

    `sys.modules[name] = None` makes `import name` raise ImportError, which is
    exactly what the guards' `try: import yaml` sees on a stdlib-only install.
    Restores whatever was there before, including nothing."""
    sentinel = object()
    previous = sys.modules.get("yaml", sentinel)
    sys.modules["yaml"] = None
    try:
        yield
    finally:
        if previous is sentinel:
            del sys.modules["yaml"]
        else:
            sys.modules["yaml"] = previous


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


class ManifestEnvCase(unittest.TestCase):
    """Pin ARCH_MANIFEST to the fixture's own manifest name for the whole test.

    These fixtures write a manifest literally named `4SYNC.yaml`, then exercise code
    that resolves `os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml"`. Inheriting an
    ambient value aims that lookup at a file the fixture never wrote, so the guard
    finds no manifest and quietly passes what it should block.

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


class GuardCase(ManifestEnvCase):
    """Builds a throwaway instance root: <root>/config/STATUS.yaml + <root>/4SYNC.yaml."""

    def setUp(self):
        super().setUp()
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

    def run_verdict(self, payload, cwd=None):
        """Drive the same dispatch path main() uses; return (kind, reason).

        Mirrors main() including the 6-arg context branch. It previously stopped
        at 5 args, so g6 — the only 6-arg guard — could never fire through this
        helper and its Bash coverage would have tested nothing."""
        import inspect
        tool, path, text, cmd = hooks._extract(payload)
        ti = payload.get("tool_input") or {}
        full = hooks._resulting_content(tool, ti)
        ctx = {"cwd": cwd or self.root,
               "raw_path": ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""}
        for guard in hooks.GUARDS:
            nargs = len(inspect.signature(guard).parameters)
            if nargs >= 6:
                returned = guard(tool, path, text, cmd, full, ctx)
            elif nargs == 5:
                returned = guard(tool, path, text, cmd, full)
            else:
                returned = guard(tool, path, text, cmd)
            kind, reason = hooks._verdict(returned)
            if reason:
                return kind, reason
        return None, None

    def run_guards(self, payload, cwd=None):
        """The first reason string, kind discarded — the pre-MP#44 shape, kept so
        every existing assertion reads unchanged."""
        return self.run_verdict(payload, cwd=cwd)[1]


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

    # ── SYN-108 item 3: the two sentinel checks disagreed ───────────────────
    def test_a_last_line_merely_containing_EOF_is_not_a_sentinel(self):
        """This guard accepted any last line CONTAINING the substring "EOF";
        `session_start.check_sentinel` requires it to START with `# ═══ EOF`.
        SYN-101 item 5 said one of them is wrong — **the loose one is**, and the
        direction of the disagreement is what settles it.

        The manifest DECLARES the canonical form:
        `integrity.eof_sentinel: "# ═══ EOF <filename> ═══"`. So the strict test is
        the one reading the contract, and the loose one fails toward SILENCE: a
        genuinely clipped file whose tail happens to carry those three letters
        passed a check whose entire job is catching clipped writes. In a file this
        project truncates by accident often enough to have written a guard for it,
        "EOF" is not a rare substring — `# ═══ EOF` is."""
        # Every tail here is VALID YAML on purpose. A bare `EOF` line is not, so
        # g4's parse check (a) fires first and the file is rejected for the wrong
        # reason — which would make this test pass while proving nothing about the
        # sentinel check (b) it is aimed at.
        for tail in ("last_touched: see EOF notes below",
                     "# TODO before EOF: trim this",
                     "sentinel_note: EOF marker was removed",
                     "some_key: EOF"):
            with self.subTest(tail=tail):
                body = STATUS_YAML[:STATUS_YAML.rindex("# ═══ EOF")] + tail + "\n"
                reason = self.run_guards(write_payload(self.status, body))
                self.assertIsNotNone(reason, tail)
                self.assertIn("clipped", reason)

    def _g4(self, body):
        """g4 alone, not the dispatcher.

        The accept-side cases below all REPLACE the sentinel line, which makes them
        whole-file writes that drop a line — so g7 fires first and the assertion
        would be about the stale-write guard rather than the sentinel test. The
        reject cases above deliberately go through the full dispatcher instead,
        because a silent PASS is the bug and it has to be shown not to survive
        anywhere in the chain."""
        return hooks.g4_status_write_guard(
            "Write", self.status.replace(os.sep, "/").lower(), body, "", full=body)

    def test_the_canonical_sentinel_is_accepted(self):
        """The control. Tightening a check is the direction that breaks working
        installs, so the shipped shape — and its indented and annotated variants,
        both of which appear in this project's own files — must still pass."""
        base = STATUS_YAML[:STATUS_YAML.rindex("# ═══ EOF")]
        for tail in ("# ═══ EOF STATUS.yaml ═══",
                     "# ═══ EOF STATUS.yaml — do not remove ═══",
                     "  # ═══ EOF STATUS.yaml ═══"):
            with self.subTest(tail=tail):
                self.assertIsNone(self._g4(base + tail + "\n"), tail)

    def test_both_hooks_apply_the_same_sentinel_test(self):
        """The item is the DISAGREEMENT, not either check on its own. Duplicated
        deliberately rather than shared — machinery modules never import one
        another (see `_declares_manifest`) — so the pinning has to be a test."""
        import session_start as ss
        base = STATUS_YAML[:STATUS_YAML.rindex("# ═══ EOF")]
        for tail, want_ok in (("# ═══ EOF STATUS.yaml ═══", True),
                              ("  # ═══ EOF STATUS.yaml ═══", True),
                              ("last_touched: see EOF notes", False),
                              ("some_key: EOF", False)):
            with self.subTest(tail=tail):
                p = os.path.join(self.root, "probe.yaml")
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(base + tail + "\n")
                self.assertEqual(ss.check_sentinel(p), want_ok, tail)
                self.assertEqual(self._g4(base + tail + "\n") is None, want_ok, tail)

    @unittest.skipUnless(HAS_YAML, "YAML parse validation requires PyYAML")
    def test_edit_that_breaks_yaml_blocks(self):
        """Check (a) — the PARSE check, and the only one that needs PyYAML.

        Skipped rather than asserted on a stdlib-only box: the guard is behaving
        as designed there, so a red test would be blaming it for a dependency it
        declares optional. What the default path DOES hold is asserted below."""
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




class TestStatusStaleWriteGuard(GuardCase):
    """g7 (MP#57) — a WHOLE-FILE Write to STATUS asks and names what it would remove.

    The hazard is a session rewriting the snapshot from its session-start copy and
    silently reverting facts another session wrote in between. The guard cannot know
    which of the two it is looking at, so it asks — and the assertions below are as
    much about what it must NOT touch (anchored edits, first writes, no-ops) as about
    what it catches."""

    def test_whole_file_write_that_drops_a_line_asks(self):
        stale = STATUS_YAML.replace('product: "v1.2.0"', 'product: "v1.1.0"')
        kind, reason = self.run_verdict(write_payload(self.status, stale))
        self.assertEqual(kind, "ask")
        self.assertIn("stale-write", reason)

    def test_the_prompt_names_what_would_be_lost(self):
        """A bare "are you sure?" is unanswerable — the human knows no more than the
        guard does. The disappearing lines ARE the question."""
        stale = STATUS_YAML.replace('product: "v1.2.0"', 'product: "v1.1.0"')
        _, reason = self.run_verdict(write_payload(self.status, stale))
        self.assertIn("v1.2.0", reason)

    def test_identical_write_is_silent(self):
        self.assertIsNone(self.run_guards(write_payload(self.status, STATUS_YAML)))

    def test_pure_addition_is_silent(self):
        """Nothing on disk disappears, so nothing can have been reverted."""
        added = STATUS_YAML.replace("meta:", 'note: "added"\nmeta:', 1)
        self.assertIsNone(self.run_guards(write_payload(self.status, added)))

    def test_anchored_edit_never_reaches_this_guard(self):
        """The documented concurrency-safe write mode must stay unpunished."""
        self.assertIsNone(self.run_guards(
            edit_payload(self.status, 'product: "v1.2.0"', 'product: "v1.3.0"')))

    def test_first_write_to_a_missing_status_is_silent(self):
        """Genesis authoring STATUS for the first time has nothing to revert."""
        os.remove(self.status)
        self.assertIsNone(self.run_guards(write_payload(self.status, STATUS_YAML)))

    def test_non_status_file_is_untouched(self):
        other = os.path.join(self.root, "config", "NOTES.yaml")
        self._put(other, "a: 1\n")
        self.assertIsNone(self.run_guards(write_payload(other, "b: 2\n")))

    def test_bash_write_does_not_double_ask(self):
        """g4 already asks for a shell write it cannot inspect; g7 must not stack a
        second prompt on the same call."""
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "echo x > config/STATUS.yaml"}}
        kind, reason = self.run_verdict(payload)
        self.assertEqual(kind, "ask")
        self.assertIn("STATUS write guard", reason)
        self.assertNotIn("stale-write", reason)

    def test_guard_is_registered_after_g4(self):
        """A clipped whole-file write must be REFUSED by g4, never offered to the
        human as a choice by g7."""
        names = [g.__name__ for g in hooks.GUARDS]
        self.assertLess(names.index("g4_status_write_guard"),
                        names.index("g7_status_stale_write_guard"))

    def test_clipped_whole_file_write_still_blocks_rather_than_asks(self):
        kind, reason = self.run_verdict(
            write_payload(self.status, STATUS_YAML.split("# ═══ EOF")[0]))
        self.assertEqual(kind, "block")
        self.assertIn("EOF sentinel", reason)

@unittest.skipUnless(HAS_YAML, "the manifest parse check requires PyYAML")
class TestBoringGuardParseCheck(GuardCase):
    """MP#60 — a manifest write that breaks the YAML must not be swallowed.

    The defect: one `except Exception` covered both "PyYAML is absent" (degrade,
    correct) and "this content does not parse" (the finding). A broken manifest
    with no date and under max_bytes went through silently. It happened for real
    and was caught by the close discipline, not by the guard.

    SKIPPED WITHOUT PyYAML, and that is the point rather than an omission: with no
    parser there is no parse verdict to assert. Asserting one anyway would make a
    red suite the modal first experience of a fresh clone, which is the exact defect
    MP#54/F1 removed. The degraded path is covered POSITIVELY by
    TestBoringGuardWithoutPyYAML below, so the skip leaves no hole."""

    BREAKER = ("  - a list item that wraps onto\n"
               "    a second line and says this: which breaks it")

    def test_unparseable_manifest_blocks(self):
        reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                              self.BREAKER))
        self.assertIsNotNone(reason)
        self.assertIn("UNPARSEABLE", reason)

    def test_refusal_names_the_line(self):
        """MP#54's standing complaint: a refusal that does not say WHERE is a riddle."""
        reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                              self.BREAKER))
        self.assertIn("line ", reason)

    def test_refusal_names_the_problem(self):
        reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                              self.BREAKER))
        self.assertIn("mapping values are not allowed", reason)

    def test_valid_manifest_still_passes(self):
        """The check must not become a reason every ordinary edit is refused."""
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Still Fine"'))
        self.assertIsNone(reason)

    def test_valid_yaml_that_is_not_a_mapping_blocks(self):
        reason = self.run_guards(write_payload(self.manifest, "just a bare string\n"))
        self.assertIsNotNone(reason)
        self.assertIn("not a mapping", reason)


class TestBoringGuardDateAttribution(GuardCase):
    """The refusal must say WHERE the date is and WHOSE it is.

    One dated comment write-locks the manifest for everybody afterwards, so the
    author of a refused write is usually not the author of the offending line.
    A refusal that says only "this write contains a date" reads as "the guard is
    broken" to someone whose edit was three lines away — which is how the original
    write-lock episode was misdiagnosed (MP#54)."""

    # ── SYN-106 item 4 ──────────────────────────────────────────────────────
    def test_the_date_branch_asks_rather_than_refuses(self):
        """SYN-044's ruling, applied where it had not reached. A date in a manifest
        is a JUDGEMENT — narrative creep or a legitimate reference — and a human is
        the only thing that can tell them apart. g5's other branch (over max_bytes)
        stays a block because it is a mechanical fact, not a judgement.

        Not the same question as g6, which blocks deliberately: the fence is
        permanent doctrine and asking would train the reflex to approve. This one
        was never doctrine — it was a heuristic given a wall to stand behind."""
        kind, reason = self.run_verdict(
            edit_payload(self.manifest, 'name: "Test Instance"',
                         'name: "Test Instance"  # 2026-08-09'))
        self.assertEqual("ask", kind)
        self.assertIn("2026-08-09", reason)

    def test_over_max_bytes_still_blocks(self):
        """The control for the change above — only the date branch softened."""
        big = MANIFEST_YAML + "\n# " + ("x" * 20000)
        kind, _ = self.run_verdict(write_payload(self.manifest, big))
        self.assertEqual("block", kind)

    def test_an_impossible_date_is_not_a_date(self):
        """`20\\d\\d-[01]\\d-[0-3]\\d` accepted month 00-19 and day 00-39, so
        `2026-19-39` write-locked a manifest as a 'calendar date'. Version strings,
        ids and ranges land in that shape; calendar dates do not."""
        for token in ("2026-19-39", "2026-00-15", "2026-08-00", "2026-13-01",
                      "2026-08-32"):
            with self.subTest(token=token):
                self.assertIsNone(
                    self.run_guards(edit_payload(
                        self.manifest, 'name: "Test Instance"',
                        f'name: "Test Instance"  # ref {token}')), token)

    def test_a_real_date_is_still_caught(self):
        """The control that bounds the tightening — a narrowing fails quiet."""
        for token in ("2026-01-01", "2026-12-31", "2026-02-29"):
            with self.subTest(token=token):
                self.assertIsNotNone(
                    self.run_guards(edit_payload(
                        self.manifest, 'name: "Test Instance"',
                        f'name: "Test Instance"  # {token}')), token)

    def test_a_date_this_write_introduces_is_attributed_to_it(self):
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Test Instance"  # 2026-08-09'))
        self.assertIsNotNone(reason)
        self.assertIn("This write introduces it", reason)

    def test_the_refusal_names_the_line(self):
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Test Instance"  # 2026-08-09'))
        self.assertIn("line ", reason)

    def test_a_preexisting_date_is_not_blamed_on_this_write(self):
        """The case the whole change exists for: an innocent edit to a manifest
        somebody else dated."""
        self._put(self.manifest, MANIFEST_YAML.replace(
            'sync_version: "1.0"', '# touched 2026-08-09\nsync_version: "1.0"'))
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Renamed"'))
        self.assertIsNotNone(reason)
        self.assertIn("ALREADY IN THE FILE", reason)
        self.assertIn("did not introduce it", reason)

    def test_the_origin_check_opens_the_raw_path_not_the_lowercased_one(self):
        """MP#70, pinned so it holds on a case-INsensitive filesystem too.

        `_extract` lowercases the path for matching; the origin check then has to
        READ the file. Opening the lowercased name raises on any case-sensitive
        filesystem whenever the target has a capital in it — and the shipped
        manifest is `4SYNC.yaml` — so the except below it silently downgraded
        every refusal to the hedged message. The feature was inert on Linux and
        macOS from the day it shipped.

        WHY THIS TEST EXISTS RATHER THAN JUST THE TWO ABOVE: on Windows the
        lowercased open succeeds, so a test asserting only on the MESSAGE cannot
        see the bug. The two tests above are green here and red on Linux, which
        is precisely why this shipped through v1.0.7 and v1.0.8 — the suite was
        only ever run on the filesystem that hides it. Assert the path itself and
        the platform stops mattering."""
        import builtins
        opened = []
        real_open = builtins.open

        def spy(file, *a, **kw):
            opened.append(file)
            return real_open(file, *a, **kw)

        builtins.open = spy
        try:
            reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                                  'name: "Test Instance"  # 2026-08-09'))
        finally:
            builtins.open = real_open

        self.assertIsNotNone(reason)
        self.assertIn("This write introduces it", reason)

        # COMPARE NORMALISED. `_extract` does BOTH `.replace("\\","/")` and
        # `.lower()`, so on Windows the mangled path differs from the raw one in
        # separators as well as case. A first cut of this test compared against
        # `self.manifest.lower()` — still backslashed — which matched nothing, so
        # it passed against the unfixed guard. Normalise separators on both sides
        # and let CASE be the only thing under test.
        seen = [str(p).replace("\\", "/") for p in opened]
        raw = self.manifest.replace("\\", "/")
        self.assertIn(raw, seen)
        # The discriminating half: pre-fix, g5's own open() put the lowercased
        # path in this list alongside the raw one _resulting_content reads.
        if raw != raw.lower():
            self.assertNotIn(raw.lower(), seen,
                             "g5 opened the LOWERCASED path — MP#70 has regressed, and "
                             "on a case-sensitive filesystem attribution is inert again")

    def test_a_clean_manifest_edit_still_passes(self):
        reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                              'name: "Renamed"'))
        self.assertIsNone(reason)


class TestBoringGuardWithoutPyYAML(GuardCase):
    """The degraded path, pinned so nobody 'fixes' it into a partial validator.

    PyYAML is absent from every fresh Python, so this is the modal adopter. The
    parse check is genuinely gone here — that is disclosed, not hidden — while the
    two rules a regex CAN evaluate keep biting."""

    def test_parse_check_is_skipped_not_faked(self):
        """Without a parser there is no parse verdict, and none is invented."""
        with no_pyyaml():
            reason = self.run_guards(edit_payload(
                self.manifest, "  - config/KERNEL.yaml",
                "  - a list item that wraps onto\n    a second line and says this: broken"))
        self.assertIsNone(reason)

    def test_max_bytes_still_blocks(self):
        bloat = "\n".join(f"  - filler/path/number/{i}.md" for i in range(400))
        with no_pyyaml():
            reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                                  "  - config/KERNEL.yaml\n" + bloat))
        self.assertIsNotNone(reason)
        self.assertIn("max_bytes", reason)

    def test_declaration_only_still_blocks_a_date(self):
        with no_pyyaml():
            reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                                  'name: "Test Instance"  # 2026-08-09'))
        self.assertIsNotNone(reason)
        self.assertIn("2026-08-09", reason)

    def test_the_journal_cap_is_not_mistaken_for_the_manifest_cap(self):
        """SECOND-PASS AUDIT FIND, and rotate.py had already written it down:
        'an unscoped max_bytes: search finds close.journal.max_bytes first, which
        is a different cap.' rotate scoped its lookup; g5's regex fallback did not
        — first max_bytes in document order won, and the journal block sits above
        integrity in every real manifest. Latent on this project only because both
        caps happen to be 16384. On a no-PyYAML box — the modal adopter install —
        a roomier journal cap silently became the manifest's, and the only HARD
        limit in the stack stopped limiting."""
        roomy = MANIFEST_YAML.replace(
            "integrity:",
            "close:\n  journal:\n    max_bytes: 999999\n\nintegrity:")
        self._put(self.manifest, roomy)
        bloat = "\n".join(f"  - filler/path/number/{i}.md" for i in range(400))
        with no_pyyaml():
            reason = self.run_guards(edit_payload(self.manifest, "  - config/KERNEL.yaml",
                                                  "  - config/KERNEL.yaml\n" + bloat))
        self.assertIsNotNone(reason, "the 8192 manifest cap should have refused this")
        self.assertIn("8192", reason)

    def test_declaration_only_is_read_from_the_rules_block_not_anywhere(self):
        """Same scoping, other key: a stray `declaration_only: true` outside
        integrity.manifest_rules must not write-lock dates for a manifest whose
        rules never declared it."""
        loose = MANIFEST_YAML.replace(
            "    declaration_only: true\n", "").replace(
            "instance:", "tuning:\n  declaration_only: true\n\ninstance:")
        self._put(self.manifest, loose)
        with no_pyyaml():
            reason = self.run_guards(edit_payload(self.manifest, 'name: "Test Instance"',
                                                  'name: "Test Instance"  # 2026-08-09'))
        self.assertIsNone(reason)


class TestStatusGuardWithoutPyYAML(GuardCase):
    """What g4 still guarantees on a stdlib-only install — the DEFAULT install.

    PyYAML is absent from every fresh Python, so this is the modal adopter's
    experience rather than a lean-box edge case. These run on any box: the
    import is blocked deliberately, so a machine that HAS PyYAML still proves
    the degraded path. Muting the parse test without this would leave the path
    most adopters run with no coverage at all."""

    def test_clipped_write_still_blocks(self):
        """Check (b), the EOF sentinel — needs no parser and keeps working."""
        tail = STATUS_YAML[STATUS_YAML.index("focus:"):]
        with no_pyyaml():
            reason = self.run_guards(edit_payload(self.status, tail, 'focus: "clipped"\n'))
        self.assertIsNotNone(reason)
        self.assertIn("clipped", reason)

    def test_bloated_last_touched_still_blocks(self):
        """Check (c), last_touched scope — a regex, so it survives too."""
        with no_pyyaml():
            reason = self.run_guards(
                edit_payload(self.status, 'last_touched: "guard hooks"',
                             'last_touched: "' + ("narrative " * 40) + '"'))
        self.assertIsNotNone(reason)
        self.assertIn("last_touched", reason)

    def test_healthy_edit_still_passes(self):
        """No parser must not mean no writes — the false-positive direction."""
        with no_pyyaml():
            reason = self.run_guards(
                edit_payload(self.status, 'focus: "harden the guard hooks"', 'focus: "ship it"'))
        self.assertIsNone(reason)

    def test_malformed_yaml_passes_and_that_is_the_documented_limit(self):
        """The degradation, PINNED so it is a known limit and not a surprise.

        Without PyYAML nothing validates YAML structure, so this write goes
        through — sentinel and last_touched are both fine. Do NOT "fix" this by
        hand-rolling a partial validator: a regex that presents as a YAML check
        is the false-confidence pattern the STATUS checker exists to prevent.
        The honest fix is this test plus the sentence at the guard."""
        with no_pyyaml():
            reason = self.run_guards(
                edit_payload(self.status, 'focus: "harden the guard hooks"', 'focus: "a: b: c'))
        self.assertIsNone(reason)


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

    def test_manifest_env_is_pinned_to_the_fixture(self):
        """Locks the isolation itself: drop ManifestEnvCase and this fails loudly
        instead of the whole suite failing only on machines that set the var."""
        self.assertEqual(os.environ.get("ARCH_MANIFEST"), "4SYNC.yaml")

    def test_kernel_guard_still_fires_and_now_asks(self):
        """g1 still catches the write; MP#44 changed the CONSEQUENCE, not the catch.
        Editing doctrine is a decision, so it goes to the human rather than being
        refused outright."""
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        os.environ.pop("CLAUDE_KERNEL_EDIT", None)
        kind, reason = self.run_verdict(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"))
        self.assertIsNotNone(reason)
        self.assertIn("KERNEL", reason)
        self.assertEqual("ask", kind)

    def test_abba_guard_judges_the_fragment_not_the_file(self):
        """A new OPEN message without To: is flagged; the guard must not start
        re-flagging pre-existing messages it wasn't asked to write.

        The fixture carries real `### [n]` headers: this test once asserted the
        guard's own defect, using headerless prose that only a block-scoped check
        could flag. The fragment-vs-file intent it was written for is unchanged."""
        abba = os.path.join(self.root, "ABBA.md")
        self._put(abba, "## Board\n\n### [1] From: X · Status: OPEN\nlegacy message, no To:\n")
        clean = self.run_guards(edit_payload(abba, "## Board", "## Board (renamed)"))
        self.assertIsNone(clean)
        dirty = self.run_guards(edit_payload(
            abba, "## Board", "## Board\n\n### [2] From: X · Status: OPEN\nRe: hi\n"))
        self.assertIsNotNone(dirty)
        self.assertIn("ABBA", dirty)


class TestAbbaHeaderFormat(GuardCase):
    """Regression cover for a guard that rejected the format the product ships.

    `To:` lives INLINE in the documented header — `### [n] To: <Agent> · From: …
    · Status: OPEN` — but g2 anchored it to the start of a line inside the block,
    so NO correctly formatted message could satisfy it. Latent since it shipped:
    under `warn` it logged and allowed, and the log line read like a real catch.
    The first instance to run at `enforce` found its bulletin board unwritable."""

    HEADER = "### [251] To: LoCo · From: Cow · 2026-08-03 · Status: OPEN"

    def _edit(self, new):
        abba = os.path.join(self.root, "ABBA.md")
        self._put(abba, "## OPEN messages\n")
        return self.run_guards(edit_payload(abba, "## OPEN messages", new))

    def test_documented_inline_to_header_passes(self):
        self.assertIsNone(self._edit(f"## OPEN messages\n\n{self.HEADER}\nRe: x\nBody.\n"))

    def test_open_header_without_to_still_blocks(self):
        reason = self._edit("## OPEN messages\n\n"
                            "### [252] From: Cow · 2026-08-03 · Status: OPEN\nRe: x\n")
        self.assertIsNotNone(reason)
        self.assertIn("ABBA", reason)

    def test_template_placeholder_header_is_ignored(self):
        """`### [n]` is the format template every board ships, not a message."""
        self.assertIsNone(self._edit(
            "## OPEN messages\n\n### [n] To: <Agent> · From: <who> · <date> · Status: OPEN|DONE\n"))

    def test_body_prose_cannot_satisfy_the_guard(self):
        """Header-scoping is TIGHTER than the fix it was chosen over: body prose
        containing 'according to:' must not count as addressing the message."""
        reason = self._edit("## OPEN messages\n\n### [253] From: Cow · Status: OPEN\n"
                            "Re: x\nAccording to: the spec, this is fine.\n")
        self.assertIsNotNone(reason)


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

    @staticmethod
    def _instance(path, kernel="KERNEL.yaml"):
        """An ARCH instance is a config dir WITH a KERNEL in it (MP#63). Every test
        here builds one through this helper, so the marker cannot drift out of the
        fixtures unnoticed — a bare config dir is now a deliberate, separate case."""
        cfg = os.path.join(path, hooks.CONFIG_DIR)
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, kernel), "w", encoding="utf-8") as fh:
            fh.write("meta:\n  role: identity-kernel\n")
        return path

    def _bash_payload(self, cwd, cmd):
        return {"tool_name": "Bash", "session_id": "s-test", "cwd": cwd,
                "tool_input": {"command": cmd}}

    def test_records_inside_an_instance(self):
        inst = self._instance(os.path.join(self.root, "myproject"))
        hooks._record_debt(self._write_payload(inst))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))

    # ── SYN-108 item 1: the recorder could not see a shell write ────────────
    #
    # The gate was `tool_name not in WRITE_TOOLS`, so a session writing through
    # heredocs and redirects never got a row. NAMED AT FOUR CONSECUTIVE CLOSES:
    # three reported "no own row" at debt-clear and read it as the default, and the
    # fourth confirmed the mechanism by writing one file with a file tool and
    # getting a row. A tracker that records nothing is worse than no tracker,
    # because the boot receipt then asserts the all-clear.
    #
    # The blast radius is the reason this is not just a widened gate: the recorder
    # CREATES a file in whatever directory it resolves, so MP#63's constraint is the
    # acceptance criterion. Write intent is judged by `_bash_write_paths`, the same
    # machinery the path guards use.

    def test_a_shell_heredoc_write_records(self):
        inst = self._instance(os.path.join(self.root, "myproject"))
        cmd = "cat <<%s > notes.md\nsome content\nEOF" % "'EOF'"
        hooks._record_debt(self._bash_payload(inst, cmd))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)),
                        "a heredoc write left no row — the tracker is off for any "
                        "session that favours shell tools")

    def test_a_shell_redirect_write_records(self):
        inst = self._instance(os.path.join(self.root, "myproject"))
        hooks._record_debt(self._bash_payload(inst, "echo x >> MERGE_PLAN.md"))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))

    def test_a_shell_read_records_nothing(self):
        """Liveness is about WRITES. A tracker that moves on every `ls` says only
        that a session exists, which the row's own presence already said."""
        inst = self._instance(os.path.join(self.root, "myproject"))
        for cmd in ("ls -la", "cat MERGE_PLAN.md", "git status",
                    "grep -rn foo tasks/"):
            with self.subTest(cmd=cmd):
                hooks._record_debt(self._bash_payload(inst, cmd))
                self.assertFalse(
                    os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)), cmd)

    def test_a_shell_write_outside_any_instance_records_nothing(self):
        """MP#63 holds for the Bash path too, and this is the assertion that keeps
        it holding. Widening the gate widened what can create a file in a stranger's
        repository; the loader-stack requirement is what stops it."""
        laravel = os.path.join(self.root, "laravel-app")
        os.makedirs(os.path.join(laravel, hooks.CONFIG_DIR))
        hooks._record_debt(self._bash_payload(laravel, "echo x > config/app.php"))
        self.assertFalse(os.path.exists(os.path.join(laravel, hooks.DEBT_FILENAME)))

    def test_a_shell_write_to_the_debt_file_itself_does_not_re_upsert(self):
        """SYN-087's exemption, which the Bash path must inherit rather than
        re-open: a close clears the row with a shell command, and if that write
        recorded, the close would report cleared and the next boot would report
        phantom debt."""
        inst = self._instance(os.path.join(self.root, "myproject"))
        debtfile = os.path.join(inst, hooks.DEBT_FILENAME)
        hooks._record_debt(self._write_payload(inst))          # create a row
        self.assertTrue(os.path.exists(debtfile))
        os.remove(debtfile)
        hooks._record_debt(self._bash_payload(inst, "rm %s" % hooks.DEBT_FILENAME))
        self.assertFalse(os.path.exists(debtfile),
                         "clearing the debt file through a shell re-created it")

    def test_records_from_a_subfolder_at_the_instance_root(self):
        inst = self._instance(os.path.join(self.root, "myproject"))
        sub = os.path.join(inst, "web", "src")
        os.makedirs(sub)
        hooks._record_debt(self._write_payload(sub))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))
        self.assertFalse(os.path.exists(os.path.join(sub, hooks.DEBT_FILENAME)))

    def test_a_bare_config_dir_is_not_an_instance(self):
        """MP#63, reproduced from the field before it was fixed. Laravel, Symfony
        and Drupal ship `config/` at the project root. Under the USER-LEVEL wire
        this product recommends, that made every such repo an instance, so ordinary
        app-dev sessions with no ARCH involvement dropped a .session_debt.tsv —
        session ids and absolute local paths — into a repo that does not gitignore
        it. The file materialising in somebody else's application repo is the
        defect; the mis-identification is only how it got there."""
        laravel = os.path.join(self.root, "laravel-app")
        os.makedirs(os.path.join(laravel, hooks.CONFIG_DIR))
        os.makedirs(os.path.join(laravel, "app"))
        hooks._record_debt(self._write_payload(laravel))
        self.assertFalse(
            os.path.exists(os.path.join(laravel, hooks.DEBT_FILENAME)),
            "a framework's config/ dir must not make its repo a debt-recording instance")

    def test_walks_past_a_bare_config_dir_to_a_real_instance(self):
        """A bare config/ is no longer an ANSWER, so the walk must continue rather
        than stop there — otherwise the fix would trade littering for losing the
        row of a session genuinely working inside an instance."""
        inst = self._instance(os.path.join(self.root, "real-instance"))
        nested = os.path.join(inst, "vendor-app")
        os.makedirs(os.path.join(nested, hooks.CONFIG_DIR))
        hooks._record_debt(self._write_payload(nested))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))
        self.assertFalse(os.path.exists(os.path.join(nested, hooks.DEBT_FILENAME)))

    def test_the_marker_survives_the_genesis_prefix(self):
        """Genesis renames the stack per project (`config/4SHIELD_KERNEL.yaml`), so
        the marker is matched as a SUFFIX. An exact-filename test would have worked
        on this repo and failed on every instance that had actually run genesis."""
        inst = self._instance(os.path.join(self.root, "prefixed"),
                              kernel="4SHIELD_KERNEL.yaml")
        hooks._record_debt(self._write_payload(inst))
        self.assertTrue(os.path.exists(os.path.join(inst, hooks.DEBT_FILENAME)))

    def test_pinning_still_overrides_everything(self):
        """ARCH_DEBT_FILE is the containment an adopter reached for before this fix
        existed (2026-08-10) and it keeps working. Its companion in that field
        report, ARCH_INSTANCE_ROOT, never existed — no code has ever read it —
        which is why the fix is code and not a documented pinning pattern."""
        laravel = os.path.join(self.root, "pinned-app")
        os.makedirs(os.path.join(laravel, hooks.CONFIG_DIR))
        pinned = os.path.join(self.root, "elsewhere.tsv")
        os.environ["ARCH_DEBT_FILE"] = pinned
        hooks._record_debt(self._write_payload(laravel))
        self.assertTrue(os.path.exists(pinned))
        self.assertFalse(os.path.exists(os.path.join(laravel, hooks.DEBT_FILENAME)))

    def test_writes_nothing_outside_an_instance(self):
        """The regression this exists for: with a user-level wire, the cwd fallback
        would drop a .session_debt.tsv into every unrelated project touched."""
        plain = os.path.join(self.root, "some-unrelated-repo")
        os.makedirs(plain)
        hooks._record_debt(self._write_payload(plain))
        self.assertEqual(os.listdir(plain), [], "no instance => no file, anywhere")

    def _seed(self, inst, rows):
        self._instance(inst)
        path = os.path.join(inst, hooks.DEBT_FILENAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(hooks.DEBT_HEADER + "\n")
            for r in rows:
                fh.write("\t".join(r) + "\n")
        return path

    def _ids(self, path):
        with open(path, encoding="utf-8") as fh:
            return [ln.split("\t")[0] for ln in fh if ln.strip() and not ln.startswith("#")]

    @staticmethod
    def _ago(days):
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(time.time() - days * 86400))

    def test_ages_out_rows_past_the_window(self):
        """The defect this closes: nothing ever removed a row, so a nested
        instance's file reached 13 rows going back three weeks."""
        inst = os.path.join(self.root, "proj")
        path = self._seed(inst, [
            ["s-old", self._ago(40), self._ago(40), inst, "unwrapped"],
            ["s-recent", self._ago(2), self._ago(2), inst, "unwrapped"],
        ])
        hooks._record_debt(self._write_payload(inst))
        ids = self._ids(path)
        self.assertNotIn("s-old", ids, "a 40-day-old row must age out")
        self.assertIn("s-recent", ids, "a 2-day-old row is still live debt")
        self.assertIn("s-test", ids, "this session's own row is written")

    def test_never_ages_out_this_sessions_own_row(self):
        """A long-running session's own row carries a stale last_activity — the
        recorder only fires on file writes — so ageing it would delete the row of
        the session actively writing the file."""
        inst = os.path.join(self.root, "proj")
        path = self._seed(inst, [
            ["s-test", self._ago(90), self._ago(90), inst, "unwrapped"],
        ])
        hooks._record_debt(self._write_payload(inst))
        self.assertIn("s-test", self._ids(path))

    def test_unparseable_timestamp_is_kept_not_dropped(self):
        """Fail-safe direction, and it is deliberately asymmetric: dropping on a
        parse failure would silently delete the thing the file exists to preserve."""
        inst = os.path.join(self.root, "proj")
        path = self._seed(inst, [
            ["s-garbled", "not-a-timestamp", "also-not", inst, "unwrapped"],
        ])
        hooks._record_debt(self._write_payload(inst))
        self.assertIn("s-garbled", self._ids(path))

    def test_ageing_can_be_disabled(self):
        inst = os.path.join(self.root, "proj")
        path = self._seed(inst, [
            ["s-ancient", self._ago(400), self._ago(400), inst, "unwrapped"],
        ])
        os.environ["ARCH_DEBT_MAX_AGE_DAYS"] = "0"
        hooks._record_debt(self._write_payload(inst))
        self.assertIn("s-ancient", self._ids(path))

    def test_a_junk_window_falls_back_to_the_default(self):
        """ARCH_DEBT_MAX_AGE_DAYS=banana must not crash a tool call."""
        inst = os.path.join(self.root, "proj")
        path = self._seed(inst, [
            ["s-old", self._ago(40), self._ago(40), inst, "unwrapped"],
        ])
        os.environ["ARCH_DEBT_MAX_AGE_DAYS"] = "banana"
        hooks._record_debt(self._write_payload(inst))
        self.assertNotIn("s-old", self._ids(path))

    def test_strict_and_lenient_instance_root_differ_only_off_instance(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        self.assertIsNone(hooks._instance_root(plain, strict=True))
        self.assertEqual(hooks._instance_root(plain), os.path.abspath(plain))

    def test_require_kernel_is_the_only_thing_that_separates_the_two_readings(self):
        """Pins the split itself. The same directory is an instance to the fencing
        reading and not to the recording one — that disagreement is the design
        (MP#63), not a bug to reconcile later."""
        laravel = os.path.join(self.root, "framework-root")
        os.makedirs(os.path.join(laravel, hooks.CONFIG_DIR))
        self.assertEqual(hooks._instance_root(laravel, strict=True),
                         os.path.abspath(laravel))
        self.assertIsNone(
            hooks._instance_root(laravel, strict=True, require_kernel=True))


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
        # SYN-106 item 5: these fixtures used to be BARE config/ dirs, which is no
        # longer what an instance looks like to g6's target side. They carry a
        # KERNEL now because they are standing in for real instances — the thing
        # they were always meant to model.
        for d in (self.inst_a, self.inst_b):
            os.makedirs(os.path.join(d, hooks.CONFIG_DIR))
            self._kernel(d)
        os.makedirs(self.plain)
        # a nested repo INSIDE instance A that ships its own config/ — this is
        # not hypothetical: the product repo ships the loader stack, so it has one
        self.nested = os.path.join(self.inst_a, "PRODUCT-REPO")
        os.makedirs(os.path.join(self.nested, hooks.CONFIG_DIR))
        self._kernel(self.nested)
        # a NEIGHBOURING ordinary project that merely ships config/ — Laravel,
        # Symfony and Drupal all do. Sibling to the instances, not nested, which
        # is the topology the old suite never built.
        self.neighbour = os.path.join(self.root, "laravel-neighbour")
        os.makedirs(os.path.join(self.neighbour, hooks.CONFIG_DIR))
        os.makedirs(os.path.join(self.neighbour, "storage"))
        self.logfile = os.path.join(self.root, "warn.log")
        self._old_log = os.environ.get("ARCH_HOOKS_LOG")
        os.environ["ARCH_HOOKS_LOG"] = self.logfile

    def tearDown(self):
        if self._old_log is None:
            os.environ.pop("ARCH_HOOKS_LOG", None)
        else:
            os.environ["ARCH_HOOKS_LOG"] = self._old_log
        shutil.rmtree(self.root, ignore_errors=True)

    def _kernel(self, root, name="KERNEL.yaml"):
        with open(os.path.join(root, hooks.CONFIG_DIR, name), "w",
                  encoding="utf-8") as fh:
            fh.write("meta:\n  role: identity-kernel\n")

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

    def test_g6_asks_the_two_sides_of_the_fence_different_questions(self):
        """THE ASYMMETRY, named so it cannot be 'tidied up' in either direction.

        THIS TEST REPLACES `test_g6_still_resolves_a_bare_config_dir_as_an_instance`
        (MP#63), which locked the bare-config reading in on BOTH sides as a
        "DELIBERATE NON-CHANGE". ITS PREMISE EXPIRED. The stated justification was:
        "the cost of a false positive is one declined write the human can approve."
        g6 does not ask — it BLOCKS, and its own docstring says so, resolving MP#44
        toward block precisely so the fence cannot be clicked away. MP#63 priced a
        false positive at one clearable prompt; MP#44 had already made it a wall.
        Two rulings, made independently, never reconciled — and the later one voids
        the earlier one's cost model. Measured cost of the non-change: writing a log
        file into a neighbouring Laravel project's `storage/` was refused.

        THE FIX IS NOT "require a KERNEL", it is "require one on the TARGET side".
        Both sides must err toward REFUSING, and they need opposite tests to do it:

          target side  — over-identifying costs a blocked legitimate write, so be
                         PRECISE: demand the loader stack.
          session side — under-identifying costs the whole fence, because a session
                         that resolves to no instance falls into the QUIET lobby
                         branch and is ALLOWED. So be LIBERAL: a bare config/ is
                         enough to make a session answerable to the fence.

        Making it symmetric was tried and measured: it lets a session booted in any
        non-ARCH project write into a real instance's ledger — the 2026-07-28
        incident through a side door. The second assertion below is that case, and
        it is the one that fails if someone 'simplifies' this later."""
        # target side: PRECISE. A neighbour that merely ships config/ is not an
        # instance, and writing into it is none of the fence's business.
        self.assertIsNone(
            self._fence(self.inst_a, os.path.join(self.neighbour, "storage", "log.txt")),
            "a bare config/ neighbour must not be read as an instance")
        # session side: LIBERAL. Booted in that same neighbour, a write INTO a real
        # instance is still fenced. This is what symmetry would break.
        reason = self._fence(self.neighbour, os.path.join(self.inst_b, "MERGE_PLAN.md"))
        self.assertIsNotNone(
            reason, "a session outside ARCH must still be fenced OUT of an instance")
        self.assertIn("CROSS-INSTANCE", reason)

    def test_a_sibling_project_shipping_config_is_not_fenced(self):
        """THE TOPOLOGY THE OLD SUITE NEVER BUILT, and the reason item 5 survived
        five releases. `test_intra_project_write_under_a_nested_instance_...` below
        builds ARCH NESTED at `ops/` under the framework root, where containment
        makes every intra-project write one tree — and concluded the predicted
        friction was rare. True for that layout. In the SIBLING layout, which is
        what an ordinary machine looks like, containment does not apply and every
        write into the neighbour was refused."""
        for rel in ("storage/log.txt", "config/app.php", "app/Models/User.php"):
            with self.subTest(rel=rel):
                target = os.path.join(self.neighbour, *rel.split("/"))
                self.assertIsNone(self._fence(self.inst_a, target), rel)

    def test_the_stack_marker_survives_a_genesis_prefix(self):
        """Genesis PREFIXES the stack and keeps the stem, so the marker is matched
        as a case-insensitive SUFFIX: `4SHIELD_KERNEL.yaml` is a KERNEL. Verified
        against both live instances on the authoring machine before this landed.

        `canon_index.yaml` is the second accepted stem so an instance survives a
        rename of either file. `status.yaml` was CONSIDERED AND REJECTED: this test
        is shared with the debt recorder, whose false positive is a
        .session_debt.tsv written into a stranger's repository (the MP#63 bug), and
        a bare `*status.yaml` is an ordinary filename in CI and monitoring repos."""
        prefixed = os.path.join(self.root, "PrefixedInstance")
        os.makedirs(os.path.join(prefixed, hooks.CONFIG_DIR))
        self._kernel(prefixed, "4SHIELD_KERNEL.yaml")
        self.assertTrue(hooks._has_stack(prefixed))

        canon = os.path.join(self.root, "CanonOnly")
        os.makedirs(os.path.join(canon, hooks.CONFIG_DIR))
        self._kernel(canon, "4CITE_CANON_INDEX.yaml")
        self.assertTrue(hooks._has_stack(canon), "canon_index alone is enough")

        statusy = os.path.join(self.root, "MonitoringRepo")
        os.makedirs(os.path.join(statusy, hooks.CONFIG_DIR))
        self._kernel(statusy, "status.yaml")
        self.assertFalse(hooks._has_stack(statusy),
                         "a bare status.yaml must NOT make an ordinary repo an instance")

    def test_intra_project_write_under_a_nested_instance_is_never_refused(self):
        """MP#63 severity refinement, verified rather than assumed. With ARCH nested
        at `ops/` under a framework root that also ships config/, MULTI_PROJECT
        §0.1 predicted constant friction. It is wrong about the frequency and right
        about the mechanism: containment means every intra-PROJECT write is one
        tree, so the misleading message appears only on genuine cross-project
        writes. This is why the g6 message was NOT touched."""
        framework = os.path.join(self.root, "laravel-app")
        ops = os.path.join(framework, "ops")
        os.makedirs(os.path.join(framework, hooks.CONFIG_DIR))
        os.makedirs(os.path.join(ops, hooks.CONFIG_DIR))
        for target in ("app/Models/User.php", "config/app.php", "ops/notes.md"):
            self.assertIsNone(self._fence(ops, os.path.join(framework, target)),
                              f"intra-project write to {target} must not be refused")
        self.assertIsNotNone(self._fence(ops, os.path.join(self.inst_b, "MERGE_PLAN.md")),
                             "a genuine cross-project write is still refused")

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


def bash_payload(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


class TestBashRouting(GuardCase):
    """MP#43 — a path-scoped guard protects a FILE; it used to ask about a TOOL.

    Every guard below tested `tool in WRITE_TOOLS` and returned None before
    looking at the target, so `Set-Content config/KERNEL.yaml` wrote what `Edit`
    could not. Verified on all five guards 2026-08-05, with controls.

    PROBE TRAP, for anyone re-testing this BY HAND rather than through this
    suite: piping a JSON payload to the hook from PowerShell 5.1 prepends a
    UTF-8 BOM, `json.load` raises, and main() takes its documented "never block
    on a malformed payload" escape and exits 0 — so EVERY probe reads as
    "allowed" no matter what the guards would do. Cost one session three false
    verifications in a day. Write the payload BOM-free and redirect from a file,
    and always include a known-blocking control: a run where nothing blocks is
    indistinguishable from a run where nothing was evaluated.
    """

    def setUp(self):
        super().setUp()
        self.kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(self.kernel, "meta:\n  status: AUTHORITATIVE\n")
        os.environ.pop("CLAUDE_KERNEL_EDIT", None)

    # ── g1: pure path decision, fully enforceable through Bash ──────────────
    def test_g1_bash_relative_path(self):
        kind, reason = self.run_verdict(bash_payload("Set-Content config/KERNEL.yaml 'x'"))
        self.assertIsNotNone(reason)
        self.assertEqual("ask", kind)

    def test_g1_bash_absolute_path(self):
        kind, reason = self.run_verdict(bash_payload(f"Set-Content {self.kernel} 'x'"))
        self.assertIsNotNone(reason)
        self.assertEqual("ask", kind)

    def test_g1_bash_basename_only(self):
        """`cd config; Set-Content KERNEL.yaml` never shows the directory in the
        token naming the file, so the shell pattern is deliberately looser."""
        kind, reason = self.run_verdict(bash_payload("Set-Content KERNEL.yaml 'x'"))
        self.assertIsNotNone(reason)
        self.assertEqual("ask", kind)

    def test_g1_bash_redirection(self):
        self.assertIsNotNone(self.run_guards(bash_payload("echo hi > config/KERNEL.yaml")))

    def test_g1_bash_sed_in_place(self):
        self.assertIsNotNone(self.run_guards(bash_payload("sed -i s/a/b/ config/KERNEL.yaml")))

    # ── reads must stay silent: these are WRITE guards ──────────────────────
    def test_read_only_commands_do_not_fire(self):
        for cmd in ("cat config/KERNEL.yaml",
                    "grep meta config/KERNEL.yaml",
                    "Get-Content config/KERNEL.yaml",
                    "head -5 config/STATUS.yaml",
                    "git diff config/KERNEL.yaml"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_guards(bash_payload(cmd)))

    def test_commit_message_mentioning_a_path_does_not_fire(self):
        """THE FIRST FALSE POSITIVE THIS FILE SHIPPED, caught within minutes of
        shipping: g6 blocked the very commit that introduced it. The message
        quoted another instance's path in a courier note, and the `->` arrows in
        the prose registered as shell redirection.

        Two causes, both fixed: `>` is no longer matched inside `->`/`=>`, and a
        heredoc BODY is data rather than a list of targets. Mentioning a path is
        not writing to it — the distinction the whole guard rests on."""
        cmd = ("git commit -F - <<'EOF'\n"
               "fix: note that ../Coworker/ABBA.md needs the same change\n"
               "suite 39 -> 60; loose 10 -> 9\n"
               "EOF")
        self.assertIsNone(self.run_guards(bash_payload(cmd), cwd=self.root))

    def test_arrows_alone_are_not_redirection(self):
        self.assertEqual([], hooks._bash_write_paths("echo 'a -> b' config/KERNEL.yaml"))

    def test_heredoc_target_before_the_body_is_still_caught(self):
        """Dropping the body must not drop real coverage: the redirection target
        is named on the command line, before the `<<`."""
        paths = hooks._bash_write_paths("cat > config/KERNEL.yaml <<'EOF'\nx\nEOF")
        self.assertTrue(any("kernel.yaml" in p.lower() for p in paths))

    # ── SYN-106 item 1: a heredoc used to hide every command after it ───────
    #
    # `_strip_heredoc_body` returned `cmd[:nl]` — the command truncated at the end
    # of the FIRST heredoc's opening line. The terminator and every subsequent
    # command in the compound were discarded before any guard saw them, so one
    # heredoc disarmed all six path guards at once, silently. Reproduced end to
    # end at exit-code level (2 bare, 0 with a heredoc in front) before the fix.
    #
    # THE FIX MUST NOT UNDO MP#50. The body still has to be dropped — a path
    # MENTIONED in a commit message is not a path WRITTEN — so these tests come in
    # pairs: the body stays invisible, everything around it becomes visible again.

    def test_a_write_after_a_heredoc_is_not_invisible(self):
        """THE BYPASS. Same trailing redirect, with and without a heredoc ahead of
        it: both must block. Asserted at the verdict, not at the helper, because
        the helper being right is not the claim — the guard firing is."""
        other = self._other_instance()
        write = f"echo pwned > {other}/config/OTHER_STATUS.yaml"
        bare = self.run_verdict(bash_payload(write), cwd=self.root)
        hidden = self.run_verdict(
            bash_payload("cat <<%s > notes.txt\nharmless\nEOF\n%s" % ("'EOF'", write)),
            cwd=self.root)
        self.assertEqual("block", bare[0], "control: the bare write must block")
        self.assertEqual("block", hidden[0],
                         "a heredoc ahead of the write must not hide it")
        self.assertIn("CROSS-INSTANCE", hidden[1] or "")

    def test_two_heredocs_then_a_write_is_not_invisible(self):
        """One heredoc was enough to hide the rest; two must not resynchronise the
        scanner onto a body and lose the tail a different way."""
        other = self._other_instance()
        cmd = ("cat <<%s > a.txt\nfirst\nEOF\n"
               "cat <<%s > b.txt\nsecond\nEOF\n"
               "echo pwned > %s/config/OTHER_STATUS.yaml"
               % ("'EOF'", "'EOF'", other))
        kind, reason = self.run_verdict(bash_payload(cmd), cwd=self.root)
        self.assertEqual("block", kind)
        self.assertIn("CROSS-INSTANCE", reason or "")

    def test_heredoc_bodies_are_still_not_targets(self):
        """The MP#50 half, restated for the multi-heredoc case. Both bodies name a
        guarded path; neither is a write."""
        cmd = ("cat <<%s > a.txt\n../Other/config/KERNEL.yaml\nEOF\n"
               "cat <<%s > b.txt\nconfig/STATUS.yaml\nEOF" % ("'EOF'", "'EOF'"))
        paths = hooks._bash_write_paths(cmd)
        self.assertEqual(["a.txt", "b.txt"], paths)

    def test_dash_heredoc_with_an_indented_terminator_is_consumed(self):
        """`<<-` strips leading tabs, so its terminator is legally indented. Miss
        that and the scanner runs to EOF looking for a terminator it already
        passed — the original bug, arriving through the tab."""
        cmd = ("cat <<-%s > a.txt\n\tbody\n\tEOF\n"
               "echo x > config/KERNEL.yaml" % "'EOF'")
        self.assertIn("config/KERNEL.yaml", hooks._bash_write_paths(cmd))

    def test_an_unterminated_heredoc_still_truncates(self):
        """The conservative fallback, kept deliberately. With no terminator every
        remaining line IS body — bash would not run it — so dropping the tail is
        correct, and it is the one case where the old behaviour was right."""
        cmd = "cat <<%s > a.txt\nnever closed\necho x > config/KERNEL.yaml" % "'EOF'"
        self.assertEqual(["a.txt"], hooks._bash_write_paths(cmd))

    def test_herestring_is_not_a_heredoc(self):
        """`<<<` is a here-STRING: no body, no terminator. Treating it as a heredoc
        would swallow the rest of the command — the same bypass by another door."""
        self.assertIn("config/KERNEL.yaml",
                      hooks._bash_write_paths("cat <<<hi > config/KERNEL.yaml"))

    # ── SYN-106 item 2: g3 only knew `git <verb>` ───────────────────────────
    def test_g3_catches_git_with_global_options(self):
        """`git -C dir commit` and `git -c k=v commit` slipped straight past — the
        pattern demanded the verb immediately after `git`.

        RECORDED BECAUSE IT IS NOT A COINCIDENCE: after the 2026-08-20 incident,
        where a `cd` that did not take effect sent `git reset --hard` into the wrong
        repo, the durable remediation adopted was "every git command uses explicit
        `-C` instead of `cd`". The habit taken up after the worst incident in the
        log is precisely the form this guard could not see."""
        os.environ["ARCH_SANDBOX"] = "1"
        self.addCleanup(os.environ.pop, "ARCH_SANDBOX", None)
        for cmd in ("git commit -m x",
                    "git -C . commit -m x",
                    "git -C /some/path add -A",
                    "git -c user.name=x commit -m x",
                    "git --no-pager -C . stash",
                    "git -C . -c core.hooksPath=/dev/null commit -m x"):
            with self.subTest(cmd=cmd):
                kind, reason = self.run_verdict(bash_payload(cmd), cwd=self.root)
                self.assertEqual("block", kind, cmd)
                self.assertIn("Sandbox git guard", reason or "")

    def test_g3_does_not_fire_on_reads_or_on_the_word_git(self):
        """The control. Narrowing is not the risk here — widening is."""
        os.environ["ARCH_SANDBOX"] = "1"
        self.addCleanup(os.environ.pop, "ARCH_SANDBOX", None)
        for cmd in ("git status", "git -C . log --oneline", "git diff",
                    "echo 'git commit' >> notes.txt", "legit commit -m x"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_guards(bash_payload(cmd), cwd=self.root), cmd)

    # ── SYN-106 items 6-7: found by ADVERSARIAL PROBES, not by reading ──────
    #
    # Both were found by firing 46 crafted write-intent commands at the shipped
    # hook and asking which ones it missed — a method that does not depend on the
    # author's opinion of his own code, and which caught two silent bypasses that
    # re-reading the diff had not. Both are pre-existing in _BASH_REDIRECT_TARGET
    # and neither was touched by the SYN-106/107/108/109 sweep; they are the same
    # CLASS as item 1 — a write the guard cannot see — reached by different syntax.
    #
    # EACH WAS CONFIRMED AGAINST REAL BASH BEFORE BEING CALLED A HOLE. A third
    # probe (`<<-` with a SPACE-indented terminator) also came back silent and is
    # NOT a defect: POSIX `<<-` strips tabs only, so bash treats that heredoc as
    # unterminated, and the trailing command never executes. The guard truncating
    # there is correct, and the test below pins it so it is not "fixed" later.

    def test_a_backslash_continuation_does_not_hide_the_target(self):
        """`echo x > \\` + newline + `  path` is ONE command to bash and it really
        writes — verified by running it. The redirect pattern's `\\s*` spans the
        newline, so `\\S+` captured the backslash itself as the target; that token
        has no slash and no extension, so the filter dropped it and the real path
        was never seen."""
        other = self._other_instance()
        tgt = f"{other}/config/OTHER_STATUS.yaml"
        kind, reason = self.run_verdict(
            bash_payload("echo x > \\\n  " + tgt), cwd=self.root)
        self.assertEqual("block", kind)
        self.assertIn("CROSS-INSTANCE", reason or "")

    def test_a_noclobber_override_is_still_a_redirect(self):
        """`>|` forces a write even under `set -o noclobber` — so it is a redirect
        that writes MORE insistently than `>`, and it was the one the guard could
        not see. The `|` landed inside the captured token, so the path resolved
        against nothing and the fence stayed quiet."""
        other = self._other_instance()
        tgt = f"{other}/config/OTHER_STATUS.yaml"
        kind, reason = self.run_verdict(
            bash_payload("echo x >| " + tgt), cwd=self.root)
        self.assertEqual("block", kind)
        self.assertIn("CROSS-INSTANCE", reason or "")

    # ── Bug sweep, 2026-08-25: BUG-003, found by an independent finder agent ──
    #
    # `\S+` stops at the first whitespace, and an unquoted `$(...)`/backtick
    # redirect target has one inside — the old pattern captured only `$(echo`,
    # a token with no slash and no extension, so the filter dropped it and every
    # guard saw zero targets: total silent allow, no ask, no block, no debt row.
    # Same CLASS as the two tests above, found the same way (an adversarial
    # probe, not a re-read), through the one syntax the SYN-106 sweep never
    # tried. This closes the ZERO-TARGETS case specifically — a regex-matching
    # guard (g1/g4/g5) now sees the full substituted text. IT DOES NOT fix
    # g6_root_fence for this syntax: g6 resolves its target as a real filesystem
    # path, and the mangled token (`$(echo ...` with only the trailing paren
    # stripped) is not one, so a cross-instance write reached ONLY through
    # command substitution still slips past g6 specifically — a known, separate,
    # harder limitation, not something this fix claims to close.

    def test_unquoted_command_substitution_does_not_hide_the_target(self):
        kind, reason = self.run_verdict(
            bash_payload("echo pwned > $(echo config/KERNEL.yaml)"), cwd=self.root)
        self.assertEqual("ask", kind)
        self.assertIn("KERNEL write guard", reason or "")

    def test_unquoted_backtick_substitution_does_not_hide_the_target(self):
        kind, reason = self.run_verdict(
            bash_payload("echo pwned > `echo config/KERNEL.yaml`"), cwd=self.root)
        self.assertEqual("ask", kind)
        self.assertIn("KERNEL write guard", reason or "")

    def test_command_substitution_with_no_matching_guard_is_not_silently_empty(self):
        """The regression this class actually guards against: BEFORE this fix,
        `_bash_write_paths` returned `[]` for this command, so it never reached
        ANY guard at all. It now reaches at least one — asserted at the
        tokenizer level since no shipped guard's pattern happens to match an
        arbitrary filename, and that is fine; the guards' coverage is a
        separate question from whether the target is visible to them."""
        self.assertNotEqual(
            hooks._bash_write_paths("echo pwned > $(echo some/arbitrary/file.txt)"),
            [])

    def test_an_unterminated_dash_heredoc_is_still_truncated(self):
        """NOT A DEFECT, pinned so it is not "fixed" into one.

        `<<-` strips leading TABS, never spaces (POSIX). A space-indented `EOF`
        therefore does not terminate the heredoc, bash reports
        "here-document delimited by end-of-file", and the trailing command is BODY
        — it never runs. Verified by executing it: the file it would have written
        does not appear. A guard that blocked here would be flagging a write that
        cannot happen."""
        other = self._other_instance()
        cmd = ("cat <<-EOF > a.txt\n  body\n  EOF\n"
               f"echo x > {other}/config/OTHER_STATUS.yaml")
        self.assertIsNone(self.run_guards(bash_payload(cmd), cwd=self.root))

    def test_unguarded_bash_is_silent(self):
        for cmd in ("echo hello > notes.txt", "ls -la", "py -m pytest"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_guards(bash_payload(cmd)))

    # ── g2/g4/g5: CONTENT guards. Via Bash the result is unknowable, so they
    #    must NOT assert a content verdict — they ask, and say why.
    def test_g4_status_via_bash_asks_without_a_content_verdict(self):
        kind, reason = self.run_verdict(bash_payload("Set-Content config/STATUS.yaml 'junk'"))
        self.assertEqual("ask", kind)
        self.assertIn("cannot inspect", reason)
        # the regression that would creep in the first time someone "improves" it:
        self.assertNotIn("does not parse", reason)
        self.assertNotIn("looks clipped", reason)

    def test_g5_manifest_via_bash_asks_without_a_content_verdict(self):
        kind, reason = self.run_verdict(bash_payload("Set-Content 4SYNC.yaml 'junk'"))
        self.assertEqual("ask", kind)
        self.assertIn("cannot inspect", reason)
        self.assertNotIn("over its own declared", reason)

    def test_g2_abba_via_bash_asks_without_a_content_verdict(self):
        kind, reason = self.run_verdict(bash_payload("Set-Content ABBA.md 'junk'"))
        self.assertEqual("ask", kind)
        self.assertIn("cannot inspect", reason)
        self.assertNotIn("lacks a 'To:'", reason)

    # ── g6: the row that mattered most. MP#36 made the fence permanent
    #    doctrine, and it was enforced against four tools and not a shell.
    def test_g6_cross_instance_via_bash_blocks(self):
        """Doubles as an ORDERING assertion: the target is deliberately named
        *STATUS.yaml, so g4 also matches it. g4 can only `ask` (it cannot see
        what a shell command produces) while g6 blocks on doctrine — so if a
        content guard is ever ordered ahead of the fence, this fails."""
        other = self._other_instance()   # a REAL instance — see the helper (SYN-106)
        target = os.path.join(other, "config", "OTHER_STATUS.yaml")
        kind, reason = self.run_verdict(
            bash_payload(f"Set-Content {target} 'x'"), cwd=self.root)
        self.assertIsNotNone(reason)
        self.assertIn("CROSS-INSTANCE", reason)
        self.assertEqual("block", kind, "the fence is doctrine, not a per-call judgement")

    def test_g6_same_instance_via_bash_is_silent(self):
        inside = os.path.join(self.root, "notes.md")
        self.assertIsNone(self.run_guards(
            bash_payload(f"echo hi > {inside}"), cwd=self.root))

    # ── MP#50: a redirect names its own target; a verb does not ──────────────

    def _other_instance(self):
        """A REAL instance: config/ plus a loader-stack file.

        The KERNEL is not decoration (SYN-106 item 5). g6's target side now demands
        the stack, so a bare config/ dir here would stop being an instance and every
        cross-instance assertion in this class would go quietly green against a
        guard that never fired — the fixture asserting the absence of the thing it
        was built to prove."""
        other = tempfile.mkdtemp(prefix="sync-hooks-other-")
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, "config"))
        with open(os.path.join(other, "config", "KERNEL.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("meta:\n  role: identity-kernel\n")
        return other

    def test_reading_another_instance_with_a_null_redirect_is_silent(self):
        """THE REAL FAILING COMMAND. A read-only survey of another instance —
        md5sum, test -f, no writes — was REFUSED as a cross-instance write
        because `2>/dev/null` supplied write intent and every path token in the
        command was then harvested as a target. Reads across the fence are
        explicitly permitted; this blocked the permitted half of the rule."""
        other = self._other_instance()
        cmd = (f'for f in a b; do md5sum {other}/scripts/$f 2>/dev/null; '
               f'[ -f {other}/hooks/$f ]; done')
        self.assertIsNone(self.run_guards(bash_payload(cmd), cwd=self.root))

    def test_a_genuine_cross_instance_redirect_still_blocks(self):
        """The control that bounds the fix — narrowing must not lose the catch."""
        other = self._other_instance()
        kind, reason = self.run_verdict(
            bash_payload(f"echo x > {other}/config/OTHER_STATUS.yaml"), cwd=self.root)
        self.assertIn("CROSS-INSTANCE", reason or "")
        self.assertEqual("block", kind)

    def test_a_write_verb_still_harvests_its_arguments(self):
        """`cp a b` writes to an argument, not to a redirect target. Verb intent
        keeps the BROAD harvest on purpose: narrowing per-verb is a wide surface
        whose failure mode is a missed write, and a false negative fails quiet."""
        other = self._other_instance()
        kind, reason = self.run_verdict(
            bash_payload(f"cp notes.md {other}/config/OTHER_STATUS.yaml"), cwd=self.root)
        self.assertIn("CROSS-INSTANCE", reason or "")
        self.assertEqual("block", kind)

    # ── SYN-106 item 3: a copy's SOURCE is a read, not a write ──────────────
    def test_copying_out_of_another_instance_is_a_read(self):
        """`cp ../Other/config/x .` reads that instance and writes to THIS one.
        Reads across the fence are explicitly allowed — the courier pattern is
        built on them — but the broad write-verb harvest claimed every path token
        in the command, so the SOURCE was reported as the cross-instance target."""
        other = self._other_instance()
        for cmd in (f"cp {other}/config/OTHER_STATUS.yaml .",
                    f"cp -r {other}/tasks ./tasks-copy",
                    f"mv {other}/notes.md ./notes.md"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.run_guards(bash_payload(cmd), cwd=self.root), cmd)

    def test_copying_INTO_another_instance_still_blocks(self):
        """The control, and the direction that matters. Narrowing must not lose the
        catch — a false negative here fails quiet, which is worse than the false
        positive being removed."""
        other = self._other_instance()
        for cmd in (f"cp notes.md {other}/config/OTHER_STATUS.yaml",
                    f"mv notes.md {other}/MERGE_PLAN.md"):
            with self.subTest(cmd=cmd):
                kind, reason = self.run_verdict(bash_payload(cmd), cwd=self.root)
                self.assertEqual("block", kind, cmd)
                self.assertIn("CROSS-INSTANCE", reason or "")

    def test_the_narrowing_bails_out_where_it_cannot_be_sure(self):
        """Three shapes where 'the last operand is the destination' is not true, or
        not the whole story. Each keeps the BROAD harvest on purpose — bailing to
        over-identification is the conservative direction for a fence.

          -t DEST src   inverts the operand order outright
          a compound    has more than one command's operands in the token list
          a redirect    names its own target, which is not the last operand
        """
        other = self._other_instance()
        tgt = f"{other}/config/OTHER_STATUS.yaml"
        for cmd in (f"cp -t {other}/config notes.md",
                    f"cp a.md b.md && cp notes.md {tgt}",
                    f"cp notes.md copy.md > {tgt}"):
            with self.subTest(cmd=cmd):
                kind, _ = self.run_verdict(bash_payload(cmd), cwd=self.root)
                self.assertEqual("block", kind, cmd)

    def test_a_null_redirect_beside_a_real_one_still_blocks(self):
        """/dev/null is dropped as a target, not as evidence — a real redirect
        in the same command must still be found."""
        other = self._other_instance()
        kind, _ = self.run_verdict(
            bash_payload(f"ls 2>/dev/null > {other}/config/OTHER_STATUS.yaml"),
            cwd=self.root)
        self.assertEqual("block", kind)

    def test_reading_a_guarded_file_in_this_instance_stays_silent(self):
        """`grep`/`cat` with a null redirect is still a read, in-instance."""
        self.assertIsNone(self.run_guards(
            bash_payload("grep -n x config/KERNEL.yaml 2>/dev/null"), cwd=self.root))


class TestVerdictContract(unittest.TestCase):
    """MP#44 — the guard names the finding, the dispatcher picks the consequence."""

    def test_bare_string_still_blocks(self):
        """An adopter's existing 4-arg guard returns a plain string and must keep
        blocking exactly as before — same opt-in discipline as the arity dispatch."""
        self.assertEqual(("block", "nope"), hooks._verdict("nope"))

    def test_explicit_kinds(self):
        self.assertEqual(("ask", "r"), hooks._verdict(("ask", "r")))
        self.assertEqual(("block", "r"), hooks._verdict(("block", "r")))

    def test_unknown_kind_degrades_to_block(self):
        """If a guard says something is wrong and we can't tell how strongly,
        the safe reading is the strict one."""
        self.assertEqual(("block", "r"), hooks._verdict(("whatever", "r")))

    def test_none_and_empty_allow(self):
        self.assertEqual(("block", None), hooks._verdict(None))
        self.assertEqual(("block", None), hooks._verdict(("ask", "")))


class TestDispatcherEndToEnd(GuardCase):
    """Exercises main() as a subprocess: exit codes and the ask JSON.

    subprocess with text input is BOM-free, which is exactly what the hand-probe
    trap documented in TestBashRouting gets wrong."""

    def _run(self, payload, mode):
        import json as _json
        import subprocess
        # Carry `cwd` as a real payload does. Without it main() falls back to the
        # hook PROCESS's cwd — the repo the suite runs from — and g6 correctly
        # reports every fixture write as cross-instance, masking the guard under
        # test. The fixture was wrong, not the fence.
        payload = dict(payload, cwd=self.root)
        env = dict(os.environ, ARCH_HOOKS_MODE=mode, ARCH_MANIFEST="4SYNC.yaml",
                   ARCH_DEBT="0", ARCH_HOOKS_LOG=os.path.join(self.root, "hooks.log"))
        hook = os.path.join(os.path.dirname(os.path.abspath(hooks.__file__)), "pre_tool_use.py")
        return subprocess.run([sys.executable, hook], input=_json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def test_askable_guard_asks_under_enforce(self):
        import json as _json
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        env_backup = os.environ.pop("CLAUDE_KERNEL_EDIT", None)
        if env_backup is not None:
            self.addCleanup(os.environ.__setitem__, "CLAUDE_KERNEL_EDIT", env_backup)
        r = self._run(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"), "enforce")
        self.assertEqual(0, r.returncode, r.stderr)
        out = _json.loads(r.stdout)
        self.assertEqual("ask", out["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual("PreToolUse", out["hookSpecificOutput"]["hookEventName"])
        self.assertIn("KERNEL", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_non_askable_guard_still_exits_2(self):
        clipped = "meta:\n  status: X\n"          # no EOF sentinel
        r = self._run(write_payload(self.status, clipped), "enforce")
        self.assertEqual(2, r.returncode)
        self.assertIn("clipped", r.stderr)
        self.assertEqual("", r.stdout.strip(), "a block must not also emit ask JSON")

    def test_warn_returns_no_permission_decision_at_all(self):
        """SYN-098. Warn decides NOTHING — it does not gate, and it does not approve.

        This assertion has now been inverted twice and the history is the point.
        MP#44: warn emits nothing on stdout. SYN-090: warn emits `allow` plus text,
        because silence was the defect. SYN-098: warn emits text and NO decision,
        because `allow` is not neutral — Claude Code documents it as SKIPPING the
        permission prompt, so warn was suppressing the very prompt a protected-file
        write should have raised. The mode that ships by default was weaker than
        shipping no hook.

        The invariant that survived all three revisions: **warn must not change what
        would have happened.** Only this version actually delivers it."""
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        r = self._run(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"), "warn")
        self.assertEqual(0, r.returncode)
        out = json.loads(r.stdout)
        self.assertNotIn("hookSpecificOutput", out,
                         "warn must return no permission decision — `allow` skips the "
                         "prompt, which is interference, not observation")

    def test_warn_still_speaks_while_deciding_nothing(self):
        """Deciding nothing must not mean saying nothing — that was SYN-090's defect
        and this is the test that keeps the fix from being undone by this row."""
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        r = self._run(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"), "warn")
        self.assertIn("systemMessage", json.loads(r.stdout))

    def test_warn_mode_tells_the_session_what_it_found(self):
        """THE v1.1.2 FAILURE (SYN-090): a guard caught a bad STATUS write, logged
        it, and allowed it — correctly, warn does not refuse. But the session was
        told NOTHING: exit 0, empty stderr, the finding in a log nobody reads
        mid-session. The guard was never the defect; its silence was.

        DRIVEN THROUGH THE EOF-SENTINEL CHECK, WHICH NEEDS NO PARSER. The first
        version of this test used the YAML-parse branch and so passed only where
        PyYAML happens to be installed — it went red on every CI leg but
        `with-pyyaml`. PyYAML is absent from every fresh Python, which this
        codebase calls the modal fresh install, so a feature test that silently
        depends on it covers the path fewest adopters run. The parse branch has
        its own gated test below."""
        tail = STATUS_YAML[STATUS_YAML.index("focus:"):]
        r = self._run(edit_payload(self.status, tail, 'focus: "clipped"\n'), "warn")
        self.assertEqual(0, r.returncode, "warn must not block")
        out = json.loads(r.stdout)
        msg = out["systemMessage"]
        self.assertNotIn("hookSpecificOutput", out)
        # Everything the old permissionDecisionReason carried now rides here — the
        # field that carried it is gone, the information is not (SYN-098).
        self.assertIn("clipped", msg)
        self.assertIn("g4_status_write_guard", msg)
        self.assertIn("enforce", msg,
                      "the session should learn this would block under enforce")

    @unittest.skipUnless(HAS_YAML, "the YAML parse branch requires PyYAML")
    def test_warn_speaks_for_the_parse_branch_too(self):
        """The v1.1.2 shape exactly — an anchored Edit built on a clipped read,
        leaving an orphan tail outside the closing quote. Gated, because check (a)
        is skipped without a parser; the test above keeps the FEATURE covered on
        stdlib-only boxes, so this skip leaves no hole."""
        self._put(self.status,
                  'meta:\n  file: STATUS.yaml\n\n'
                  'active_focus: "v1.1.2 published and verified\n'
                  '  and the slot is held."\n\n# \u2550\u2550\u2550 EOF STATUS.yaml \u2550\u2550\u2550\n')
        r = self._run(edit_payload(self.status,
                                   'active_focus: "v1.1.2 published and verified',
                                   'active_focus: "v1.1.3 in flight"'), "warn")
        self.assertEqual(0, r.returncode, "warn must not block")
        out = json.loads(r.stdout)
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIn("does not parse as YAML", out["systemMessage"])

    def test_the_shipped_default_is_enforce(self):
        """SYN-098, Michael 2026-08-21. With ARCH_HOOKS_MODE UNSET, a guard bites.

        The default was `warn` on rollout reasoning — watch for false positives
        before letting guards block. Sound reasoning, wrong default, because warn
        also emitted an explicit allow and the primary harness honors that by
        skipping the permission prompt. A fresh install was therefore weaker than
        no install. Both halves moved together: warn stopped interfering, and the
        mode that ships started protecting.

        Asserted by UNSETTING the variable rather than passing "enforce" — the
        default is the whole claim, and a test that names the value cannot see it."""
        clipped = "meta:\n  status: X\n"          # no EOF sentinel
        env = dict(os.environ, ARCH_MANIFEST="4SYNC.yaml", ARCH_DEBT="0",
                   ARCH_HOOKS_LOG=os.path.join(self.root, "hooks.log"))
        env.pop("ARCH_HOOKS_MODE", None)
        import subprocess
        hook = os.path.join(os.path.dirname(os.path.abspath(hooks.__file__)),
                            "pre_tool_use.py")
        payload = dict(write_payload(self.status, clipped), cwd=self.root)
        r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                           stdin=None, capture_output=True, text=True, env=env)
        self.assertEqual(2, r.returncode,
                         "with no mode set, a guard finding must BLOCK")
        self.assertIn("clipped", r.stderr)

    def test_warn_says_nothing_when_no_guard_fires(self):
        """A mode that speaks on every call is a mode nobody reads."""
        self._put(self.status, 'meta:\n  file: STATUS.yaml\n# \u2550\u2550\u2550 EOF STATUS.yaml \u2550\u2550\u2550\n')
        r = self._run(edit_payload(self.status, "meta:", "meta:"), "warn")
        self.assertEqual(0, r.returncode)
        self.assertEqual("", r.stdout.strip())

    def test_malformed_payload_stays_silent(self):
        import subprocess
        env = dict(os.environ, ARCH_HOOKS_MODE="enforce", ARCH_DEBT="0")
        hook = os.path.join(os.path.dirname(os.path.abspath(hooks.__file__)), "pre_tool_use.py")
        r = subprocess.run([sys.executable, hook], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertEqual(0, r.returncode)


class TestDeprecatedOverride(GuardCase):
    """CLAUDE_KERNEL_EDIT=1 keeps working for at least one minor version — it is
    documented and may be in an adopter runbook — but it now LOGS when honoured,
    so remaining use is visible rather than assumed dead. Before this it wrote
    nothing anywhere (verified 2026-08-05: log unchanged, 0 bytes)."""

    def test_override_allows_and_logs(self):
        logpath = os.path.join(self.root, "hooks.log")
        prev_log = os.environ.get("ARCH_HOOKS_LOG")
        prev_edit = os.environ.get("CLAUDE_KERNEL_EDIT")
        os.environ["ARCH_HOOKS_LOG"] = logpath
        os.environ["CLAUDE_KERNEL_EDIT"] = "1"

        def restore():
            for k, v in (("ARCH_HOOKS_LOG", prev_log), ("CLAUDE_KERNEL_EDIT", prev_edit)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)

        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        self.assertIsNone(self.run_guards(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE")))
        with open(logpath, encoding="utf-8") as fh:
            self.assertIn("DEPRECATED override honoured", fh.read())


class TestRelativePathReplay(GuardCase):
    """A relative file_path must resolve against the SESSION's cwd (MP#73).

    Resolved against the hook PROCESS's cwd it is a different file, and the
    failure mode is the quiet one: the replay reads nothing, returns None, and
    every content guard skips by contract — so g4 and g5 stop inspecting anything
    while the log stays empty and the mode still says enforce. g6 already got this
    right. Payloads seen in practice carry absolute paths; these pin the behaviour
    so that staying true is not a matter of luck."""

    def test_relative_path_resolves_against_the_session_cwd(self):
        self._put(os.path.join(self.root, "mp73-fixture.yaml"), "focus: original\n")
        content = hooks._resulting_content(
            "Edit", {"file_path": "mp73-fixture.yaml",
                     "old_string": "focus: original", "new_string": "focus: replaced"},
            self.root)
        self.assertIsNotNone(content)
        self.assertIn("focus: replaced", content)

    def test_the_process_cwd_can_resolve_to_a_DIFFERENT_REAL_FILE(self):
        """The hazard, and it is worse than reading nothing.

        Written first as "without a cwd it returns None" — which failed, and the
        failure was the point: the relative path resolved to a REAL file under the
        repo the suite was run from. A wrong file that parses is far worse than an
        unreadable one, because the guards do not skip — they judge another
        instance's content and report on it with full confidence.

        BUILDS BOTH SIDES ITSELF. The first version of this test read whatever
        `config/STATUS.yaml` happened to sit under the process cwd, so it passed in
        the product repo (whose copy is the TEMPLATE) and FAILED in the silo (whose
        copy is AUTHORITATIVE, so the replay found no anchor and both sides came
        back None). This file is machinery and ships byte-identical to both repos
        and to every adopter — a test that depends on the tree it runs in is the
        exact defect ManifestEnvCase exists to prevent, one directory over."""
        decoy = os.path.join(self.root, "decoy")
        os.makedirs(os.path.join(decoy, "config"))
        self._put(os.path.join(decoy, "config", "STATUS.yaml"),
                  "meta:\n  status: AUTHORITATIVE\nowner: DECOY\n")
        self._put(self.status, STATUS_YAML.replace("focus:", "owner: SESSION\nfocus:"))

        here = os.getcwd()
        os.chdir(decoy)
        self.addCleanup(os.chdir, here)

        payload = {"file_path": os.path.join("config", "STATUS.yaml"),
                   "old_string": "meta:", "new_string": "meta:  # touched"}
        theirs = hooks._resulting_content("Edit", dict(payload))
        ours = hooks._resulting_content("Edit", dict(payload), self.root)

        self.assertIsNotNone(theirs)
        self.assertIsNotNone(ours)
        self.assertNotEqual(theirs, ours)
        self.assertIn("DECOY", theirs)          # process cwd — the wrong instance
        self.assertNotIn("DECOY", ours)
        self.assertIn("SESSION", ours)          # session cwd — the right one

    def test_the_cwd_parameter_is_optional(self):
        """Defaults to None so an adopter's own caller keeps working unchanged."""
        self.assertIsNone(hooks._resulting_content(
            "Edit", {"file_path": "no-such-file-anywhere-mp73.yaml",
                     "old_string": "x", "new_string": "y"}))

    def test_an_absolute_path_ignores_the_cwd(self):
        content = hooks._resulting_content(
            "Edit", {"file_path": self.status,
                     "old_string": 'focus: "harden the guard hooks"',
                     "new_string": 'focus: "ship it"'},
            os.path.join(self.root, "nowhere"))
        self.assertIsNotNone(content)
        self.assertIn('focus: "ship it"', content)


class TestCrashedGuardIsLoud(GuardCase):
    """A guard that throws is a guard that DID NOT RUN (MP#72).

    The dispatcher swallows guard exceptions so a buggy guard can never break the
    user's tool call — correct, and unchanged. What was missing is the other half:
    it did it silently, which is the SILENT BYPASS this file spends hundreds of
    lines converting into loud ones. Under enforce the write lands, settings still
    say enforce, and the log still fills with other guards' catches, so nothing
    looks wrong from any angle.

    Both halves are asserted here on purpose. A test that only checked the call
    still proceeds would have passed before this change and after it."""

    def setUp(self):
        super().setUp()
        self.logfile = os.path.join(self.root, "crash.log")
        for k, v in (("ARCH_HOOKS_LOG", self.logfile),
                     ("ARCH_HOOKS_MODE", "enforce"),
                     ("ARCH_DEBT", "0")):
            prev = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(self._restore_env, k, prev)

    @staticmethod
    def _restore_env(key, prev):
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    def _run_main(self, payload):
        """Drive main() in-process so a raising guard can be injected.

        The subprocess runner used elsewhere cannot reach GUARDS in the child."""
        import io
        import json as _json
        payload = dict(payload, cwd=self.root)
        prev_stdin = sys.stdin
        sys.stdin = io.StringIO(_json.dumps(payload))
        try:
            with self.assertRaises(SystemExit) as caught:
                hooks.main()
        finally:
            sys.stdin = prev_stdin
        return caught.exception.code

    def _install_exploding_guard(self):
        def g99_exploding_guard(tool, path, text, cmd):
            raise RuntimeError("guard exploded")

        prev = list(hooks.GUARDS)
        hooks.GUARDS.insert(0, g99_exploding_guard)
        self.addCleanup(lambda: hooks.GUARDS.__setitem__(slice(None), prev))
        return g99_exploding_guard

    def _log_text(self):
        if not os.path.exists(self.logfile):
            return ""
        with open(self.logfile, encoding="utf-8") as fh:
            return fh.read()

    def test_a_crashing_guard_does_not_break_the_tool_call(self):
        """The original rationale, preserved. This half passed before MP#72 too."""
        self._install_exploding_guard()
        code = self._run_main(write_payload(os.path.join(self.root, "notes.md"), "hello"))
        self.assertEqual(0, code)

    def test_a_crashing_guard_is_logged_by_name(self):
        """The half that was missing. Without it the skip is indistinguishable
        from a guard that ran and found nothing."""
        self._install_exploding_guard()
        self._run_main(write_payload(os.path.join(self.root, "notes.md"), "hello"))
        log = self._log_text()
        self.assertIn("CRASHED", log)
        self.assertIn("g99_exploding_guard", log)
        self.assertIn("guard exploded", log)

    def test_a_clean_run_logs_no_crash(self):
        """The control. A CRASHED line must mean something actually crashed —
        otherwise the log trains its reader to ignore it."""
        self._run_main(write_payload(os.path.join(self.root, "notes.md"), "hello"))
        self.assertNotIn("CRASHED", self._log_text())



class TestManifestSizesExcludeBootstrap(unittest.TestCase):
    """MP#67/#84. The cap governs what EVERY SESSION pays for, and `bootstrap:` is
    not that: genesis reads it once and deletes it at the end, while boot: and
    close: are paid for the life of the instance. One number governing both is
    mis-scoped rather than tight — the shipped template sat 45 bytes from its cap
    while carrying ~5.7 KB of genesis instructions no adopter ever loads, and the
    next real declaration would have been refused for the wrong reason.

    Excluded rather than given an allowance, deliberately: an allowance is a second
    number to pick, justify and let drift. This needs no number."""

    def _sizes(self, text):
        return hooks._manifest_sizes(text)

    def test_a_manifest_without_bootstrap_is_unchanged(self):
        text = "boot:" + chr(10) + "  - a.yaml" + chr(10)
        total, persistent, boot = self._sizes(text)
        self.assertEqual(boot, 0)
        self.assertEqual(total, persistent)

    def test_the_bootstrap_block_is_excluded_from_persistent(self):
        text = ("boot:" + chr(10) + "  - a.yaml" + chr(10)
                + "bootstrap:" + chr(10) + "  steps: [one, two]" + chr(10)
                + "close:" + chr(10) + "  journal: x" + chr(10))
        total, persistent, boot = self._sizes(text)
        self.assertGreater(boot, 0)
        self.assertEqual(persistent, total - boot)
        self.assertNotIn("steps", "")  # sanity: the block really was present

    # ── SYN-108 item 4 ──────────────────────────────────────────────────────
    def test_a_commented_bootstrap_key_is_still_found(self):
        """`^bootstrap:\\s*$` requires the line to END at the key. A trailing comment
        — which YAML allows anywhere and this project writes constantly — made the
        block invisible, so its bytes counted toward the cap it is exempt from and
        the manifest was refused for a reason that was not true.

        THE SAME DEFECT SYN-099 FIXED IN mail.py, at the third of four sites. The
        precedent for getting it right is older than the bug:
        `session_start.py:97` anchors `^boot:[ \\t]*(?:#[^\\n]*)?$`."""
        for line in ("bootstrap:  # genesis only; deleted at close",
                     "bootstrap:\t# tab then comment",
                     "bootstrap:   "):
            with self.subTest(line=line):
                text = ("boot:" + chr(10) + "  - a.yaml" + chr(10)
                        + line + chr(10) + "  steps: [one, two]" + chr(10)
                        + "close:" + chr(10) + "  journal: x" + chr(10))
                total, persistent, boot = self._sizes(text)
                self.assertGreater(boot, 0, line)
                self.assertEqual(persistent, total - boot, line)

    def test_the_bootstrap_body_does_not_start_past_a_blank_line(self):
        """`\\s` spans newlines, so `^bootstrap:\\s*$` could match the key AND the
        blank line under it, starting the block one line late and leaving that
        line counted as persistent. `[ \\t]*` cannot do that."""
        text = ("bootstrap:" + chr(10) + chr(10) + "  a: 1" + chr(10)
                + "close:" + chr(10) + "  b: 2" + chr(10))
        total, persistent, boot = self._sizes(text)
        self.assertNotIn("a: 1", text[:len(text) - boot],
                         "the bootstrap body leaked into the persistent span")

    def test_a_following_top_level_key_is_not_swallowed(self):
        """The block ends at the next top-level key, not at end of file. Swallowing
        close: would exempt the very thing the cap exists to bound."""
        text = ("bootstrap:" + chr(10) + "  a: 1" + chr(10)
                + "close:" + chr(10) + "  b: 2" + chr(10))
        total, persistent, boot = self._sizes(text)
        self.assertIn("close:", text[len("bootstrap:") + boot - len("bootstrap:"):])
        self.assertGreaterEqual(persistent, len(("close:" + chr(10) + "  b: 2" + chr(10)).encode()))

    def test_a_cap_breach_is_judged_on_the_persistent_size(self):
        """The whole point: a manifest whose persistent half fits must be allowed
        even when the bootstrap block pushes the raw total over."""
        boot_block = "bootstrap:" + chr(10) + ("  # filler" + chr(10)) * 200
        text = "boot:" + chr(10) + "  - a.yaml" + chr(10) + boot_block
        total, persistent, boot = self._sizes(text)
        self.assertGreater(total, 1000)
        self.assertLess(persistent, 100)

class DebtSelfWriteCase(unittest.TestCase):
    """A write TARGETING the debt file itself must never be recorded as debt.

    SYN-087, observed live on a cold trial: the recorder upserts this session's
    row on every file-write call, so a close that cleared its own row with a
    file-edit tool — or made any write-tool call AFTER the clear — silently
    restored the row, and the next boot reported phantom debt from a session
    that had closed properly. The debt file is bookkeeping, not work: touching
    it proves nothing about undeposited state, so recording it is always wrong.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="debt_self_")
        os.makedirs(os.path.join(self.root, "config"))
        with open(os.path.join(self.root, "config", "KERNEL.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("meta:\n  status: AUTHORITATIVE\n")
        self.debt = os.path.join(self.root, ".session_debt.tsv")
        for var in ("ARCH_DEBT", "ARCH_DEBT_FILE"):
            prev = os.environ.pop(var, None)
            if prev is not None:
                self.addCleanup(os.environ.__setitem__, var, prev)

    def _payload(self, target):
        p = write_payload(target, "x")
        p["cwd"] = self.root
        p["session_id"] = "sid-under-test"
        return p

    def test_write_to_debt_file_records_nothing(self):
        hooks._record_debt(self._payload(self.debt))
        self.assertFalse(os.path.exists(self.debt))

    def test_clear_then_edit_of_debt_file_does_not_resurrect(self):
        hooks._record_debt(self._payload(os.path.join(self.root, "notes.md")))
        with open(self.debt, encoding="utf-8") as fh:
            self.assertIn("sid-under-test", fh.read())
        with open(self.debt, "w", encoding="utf-8") as fh:  # the close's clear
            fh.write("# header only\n")
        hooks._record_debt(self._payload(self.debt))        # an edit OF the file
        with open(self.debt, encoding="utf-8") as fh:
            self.assertNotIn("sid-under-test", fh.read())

    def test_override_debt_file_name_is_also_skipped(self):
        alt = os.path.join(self.root, "custom_debt.tsv")
        os.environ["ARCH_DEBT_FILE"] = alt
        self.addCleanup(os.environ.pop, "ARCH_DEBT_FILE", None)
        hooks._record_debt(self._payload(alt))
        self.assertFalse(os.path.exists(alt))

    def test_lookalike_debt_file_elsewhere_still_records(self):
        """Lookalike in a PLAIN folder (no instance): real work — the session's
        own liveness row must keep moving, or a second boot reads 'probably
        idle' mid-maintenance."""
        nested = os.path.join(self.root, "product")
        os.makedirs(nested)
        hooks._record_debt(self._payload(os.path.join(nested, ".session_debt.tsv")))
        self.assertTrue(os.path.exists(self.debt))
        with open(self.debt, encoding="utf-8") as fh:
            self.assertIn("sid-under-test", fh.read())

    def test_nested_instances_debt_file_is_bookkeeping_too(self):
        """The manifest's at_close says clear EVERY debt file under the root, so
        a close that edits a NESTED INSTANCE's .session_debt.tsv must not have
        that very write re-upsert the row it is clearing (observed live: the
        silo clearing the product repo's file resurrected the silo row)."""
        nested = os.path.join(self.root, "product")
        os.makedirs(os.path.join(nested, "config"))
        with open(os.path.join(nested, "config", "KERNEL.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("meta:\n  status: AUTHORITATIVE\n")
        hooks._record_debt(self._payload(os.path.join(nested, ".session_debt.tsv")))
        self.assertFalse(os.path.exists(self.debt))

    def test_relative_override_resolves_against_payload_cwd(self):
        """A relative ARCH_DEBT_FILE must resolve against the payload cwd — the
        same base the write target resolves against — or the exact-path
        exemption misses whenever the hook process cwd differs."""
        os.environ["ARCH_DEBT_FILE"] = "custom_debt.tsv"     # relative, deliberately
        self.addCleanup(os.environ.pop, "ARCH_DEBT_FILE", None)
        hooks._record_debt(self._payload(os.path.join(self.root, "custom_debt.tsv")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "custom_debt.tsv")))

    def test_ordinary_write_still_records(self):
        hooks._record_debt(self._payload(os.path.join(self.root, "notes.md")))
        with open(self.debt, encoding="utf-8") as fh:
            self.assertIn("sid-under-test", fh.read())


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_pre_tool_use.py ═══
