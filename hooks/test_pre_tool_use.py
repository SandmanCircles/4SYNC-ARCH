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
import os
import shutil
import sys
import tempfile
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
        other = tempfile.mkdtemp(prefix="sync-hooks-other-")
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, "config"))
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
        other = tempfile.mkdtemp(prefix="sync-hooks-other-")
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, "config"))
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

    def test_warn_mode_is_unchanged_for_askable_guards(self):
        """MP#44 changes what `enforce` does. `warn` must be byte-identical, which
        is what makes this safe to ship mid-adoption."""
        kernel = os.path.join(self.root, "config", "KERNEL.yaml")
        self._put(kernel, "meta:\n  status: AUTHORITATIVE\n")
        r = self._run(edit_payload(kernel, "AUTHORITATIVE", "TEMPLATE"), "warn")
        self.assertEqual(0, r.returncode)
        self.assertEqual("", r.stdout.strip(), "warn must not emit ask JSON")

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


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_pre_tool_use.py ═══
