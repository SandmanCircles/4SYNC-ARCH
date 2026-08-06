#!/usr/bin/env python3
"""
Stdlib unittest suite for meter.py — the boot-cost meter.

First tests in this repo; sets the pattern (mirrors rotate.py's testable-
pure-function style). No network, no third-party deps. Imports meter from the
same scripts/ directory. Run either way:

  python -m unittest test_meter          # from the scripts/ dir
  python scripts/test_meter.py           # from the repo root
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Import meter.py from the same directory as this test, regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meter  # noqa: E402


# A small 4SYNC.yaml-shaped manifest: real inline comments, a 'bootstrap:' key
# that must NOT be mistaken for 'boot:', and all three load lists.
SAMPLE_MANIFEST = """\
sync_version: "1.0"

instance:
  name: "Test Instance"

boot:
  # ordered, read whole in one pass
  - MERGE_PLAN.md            # operational state
  - config/KERNEL.yaml       # identity contract

on_demand:
  - config/REFERENCE.yaml    # deep canon
  - NAMING_CONVENTIONS.md    # load before external output

never_load_whole:
  - config/HISTORY.md        # frozen archive

bootstrap:
  seed:
    file: SEED.md
"""


class TestEstimateTokens(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(meter.estimate_tokens(0), 0)
        self.assertEqual(meter.estimate_tokens(4), 1)
        self.assertEqual(meter.estimate_tokens(400), 100)

    def test_negative_is_zero(self):
        self.assertEqual(meter.estimate_tokens(-10), 0)

    def test_monotonic(self):
        prev = -1
        for n in range(0, 5000, 37):
            cur = meter.estimate_tokens(n)
            self.assertGreaterEqual(cur, prev)
            prev = cur


class TestParseLoadLists(unittest.TestCase):
    def test_extracts_three_lists(self):
        lists = meter.parse_load_lists(SAMPLE_MANIFEST)
        self.assertEqual(lists["boot"], ["MERGE_PLAN.md", "config/KERNEL.yaml"])
        self.assertEqual(lists["on_demand"],
                         ["config/REFERENCE.yaml", "NAMING_CONVENTIONS.md"])
        self.assertEqual(lists["never_load_whole"], ["config/HISTORY.md"])

    def test_bootstrap_key_not_confused_with_boot(self):
        # 'bootstrap:' must not leak its nested items into the boot list.
        lists = meter.parse_load_lists(SAMPLE_MANIFEST)
        self.assertNotIn("SEED.md", lists["boot"])

    def test_missing_lists_are_empty(self):
        lists = meter.parse_load_lists("instance:\n  name: nothing\n")
        self.assertEqual(lists["boot"], [])
        self.assertEqual(lists["on_demand"], [])
        self.assertEqual(lists["never_load_whole"], [])

    def test_line_parser_directly(self):
        # Exercise the dependency-free fallback regardless of whether PyYAML exists.
        lists = meter._parse_load_lists_lines(SAMPLE_MANIFEST)
        self.assertEqual(lists["boot"], ["MERGE_PLAN.md", "config/KERNEL.yaml"])
        self.assertEqual(lists["never_load_whole"], ["config/HISTORY.md"])


class ManifestEnvCase(unittest.TestCase):
    """Pin ARCH_MANIFEST to the fixture's own manifest name for the whole test.

    These fixtures write a manifest literally named `4SYNC.yaml`, then call code
    that resolves `os.environ.get("ARCH_MANIFEST") or "4SYNC.yaml"`. Inheriting an
    ambient value aims that lookup at a file the fixture never wrote.

    The bite: MP#20 tells adopters to rename their manifest off the colliding
    `4SYNC.yaml`, which sets exactly this variable — so every adopter who followed
    the product's own advice broke their suite, with no way to tell those failures
    from real ones. A test must not depend on the environment it happens to run in.
    (TestResolveManifest is deliberately NOT a subclass: it passes env dicts in
    explicitly, which is how the knob's own behaviour should be tested.)"""

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


class TestBuildReport(ManifestEnvCase):
    def test_manifest_env_is_pinned_to_the_fixture(self):
        """Locks the isolation itself: drop ManifestEnvCase and this fails loudly
        instead of the whole suite failing only on machines that set the var."""
        self.assertEqual(os.environ.get("ARCH_MANIFEST"), "4SYNC.yaml")

    def setUp(self):
        super().setUp()
        # Build a tiny fake repo on disk with known file sizes.
        self.root = tempfile.mkdtemp(prefix="meter_test_")
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)

        # Known byte sizes (ASCII => 1 byte/char).
        self.sizes = {
            "4SYNC.yaml": len(SAMPLE_MANIFEST.encode("utf-8")),
            "MERGE_PLAN.md": 800,
            "config/KERNEL.yaml": 1200,
            "config/REFERENCE.yaml": 2000,
            "NAMING_CONVENTIONS.md": 400,
            "config/HISTORY.md": 1600,
        }
        # Write the manifest verbatim, and the referenced files at fixed sizes.
        self._write("4SYNC.yaml", SAMPLE_MANIFEST)
        for rel, n in self.sizes.items():
            if rel == "4SYNC.yaml":
                continue
            self._write(rel, "x" * n)

        self.lists = meter.parse_load_lists(SAMPLE_MANIFEST)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def test_boot_total_matches_known_bytes(self):
        data = meter.build_report_data(self.root, self.lists)
        expected_boot_bytes = (self.sizes["4SYNC.yaml"]
                               + self.sizes["MERGE_PLAN.md"]
                               + self.sizes["config/KERNEL.yaml"])
        self.assertEqual(data["boot_total_bytes"], expected_boot_bytes)
        self.assertEqual(data["boot_total_tokens"],
                         meter.estimate_tokens(self.sizes["4SYNC.yaml"])
                         + meter.estimate_tokens(self.sizes["MERGE_PLAN.md"])
                         + meter.estimate_tokens(self.sizes["config/KERNEL.yaml"]))

    def test_deferred_total_matches_known_bytes(self):
        data = meter.build_report_data(self.root, self.lists)
        expected_deferred_bytes = (self.sizes["config/REFERENCE.yaml"]
                                   + self.sizes["NAMING_CONVENTIONS.md"]
                                   + self.sizes["config/HISTORY.md"])
        self.assertEqual(data["deferred_total_bytes"], expected_deferred_bytes)

    def test_savings_percentage(self):
        data = meter.build_report_data(self.root, self.lists)
        boot_t = data["boot_total_tokens"]
        def_t = data["deferred_total_tokens"]
        expected_pct = def_t / (boot_t + def_t) * 100.0
        self.assertAlmostEqual(data["deferred_pct"], expected_pct, places=6)
        self.assertEqual(data["total_tokens"], boot_t + def_t)

    def test_deferred_tags(self):
        data = meter.build_report_data(self.root, self.lists)
        tags = {r["path"]: r["tag"] for r in data["deferred"]}
        self.assertEqual(tags["config/REFERENCE.yaml"], "on_demand")
        self.assertEqual(tags["config/HISTORY.md"], "never_load_whole")

    def test_missing_file_counts_zero_and_flags(self):
        # Remove a boot file; it must measure 0 and be flagged missing, not crash.
        os.remove(os.path.join(self.root, "config", "KERNEL.yaml"))
        data = meter.build_report_data(self.root, self.lists)
        kernel = next(r for r in data["boot"] if r["path"] == "config/KERNEL.yaml")
        self.assertEqual(kernel["bytes"], 0)
        self.assertTrue(kernel["missing"])

    def test_directory_entry_is_summed_not_reported_missing(self):
        """MP#47/D4. A manifest may defer a whole folder — `tasks/` on a second
        instance holds 98 documents — and this reported `(missing — counted as 0)`,
        which to an adopter reads as a broken install rather than as 98 files
        correctly kept off the boot path."""
        d = os.path.join(self.root, "tasks")
        os.makedirs(d, exist_ok=True)
        for name, size in (("MP-001.md", 300), ("MP-002.md", 700)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("x" * size)
        row = meter._row(self.root, "tasks")
        self.assertFalse(row["missing"])
        self.assertEqual(row["bytes"], 1000)
        self.assertTrue(row["dir"])

    def test_directory_sum_is_recursive(self):
        """`tasks/` holds `tasks/closed/`; a top-level-only sum would under-report
        a deferred folder by most of its contents."""
        nested = os.path.join(self.root, "tasks", "closed")
        os.makedirs(nested, exist_ok=True)
        with open(os.path.join(self.root, "tasks", "live.md"), "w") as f:
            f.write("x" * 100)
        with open(os.path.join(nested, "done.md"), "w") as f:
            f.write("x" * 400)
        self.assertEqual(meter._row(self.root, "tasks")["bytes"], 500)

    def test_directory_sum_skips_vendored_trees(self):
        d = os.path.join(self.root, "site", "node_modules", "pkg")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "huge.js"), "w") as f:
            f.write("x" * 9999)
        with open(os.path.join(self.root, "site", "index.md"), "w") as f:
            f.write("x" * 50)
        self.assertEqual(meter._row(self.root, "site")["bytes"], 50)

    def test_a_genuinely_absent_entry_is_still_missing(self):
        """The control — the missing-file flag must survive the directory fix."""
        row = meter._row(self.root, "no_such_dir")
        self.assertTrue(row["missing"])
        self.assertEqual(row["bytes"], 0)
        self.assertNotIn("dir", row)

    def test_text_report_renders(self):
        report = meter.build_report(self.root, self.lists)
        self.assertIn("4SYNC ARCH", report)
        self.assertIn("BOOT TOTAL", report)
        self.assertIn("DEFERRED TOTAL", report)
        self.assertIn("SAVINGS", report)

    def test_renamed_manifest_is_measured(self):
        # An instance that renamed its manifest: the renamed file must be the one
        # measured into the boot stack, at its real size.
        renamed = "ARCH.yaml"
        self._write(renamed, SAMPLE_MANIFEST)
        data = meter.build_report_data(self.root, self.lists, renamed)
        self.assertEqual(data["manifest"], renamed)
        row = next(r for r in data["boot"] if r["path"] == renamed)
        self.assertEqual(row["bytes"], self.sizes["4SYNC.yaml"])
        self.assertFalse(row["missing"])


class TestBulletinBootFile(unittest.TestCase):
    """ABBA.md is read at session start but declared under close.bulletin, not in
    boot: — the meter must charge for it, but only when the board is live."""

    LIVE = SAMPLE_MANIFEST + """
agents:
  self: [declared, env:ARCH_AGENT, shell, ask]
  roster: ABBA.md

close:
  bulletin:
    file: ABBA.md
    check_at_boot: true
    archive_done_after_days: 10
"""
    # `check_at_boot: true` but NO `agents:` block. This used to be treated as an
    # inert board and dropped from the count — the regression MP#30 removed. It is
    # a real shape: an instance carrying check_at_boot AND a CLAUDE.md telling every
    # session to read the board, but no agents: block. The old gate silently dropped
    # 11,611 tokens there, on the one instance whose number justified a migration.
    NO_AGENTS = SAMPLE_MANIFEST + """
close:
  bulletin:
    file: ABBA.md
    check_at_boot: true
"""
    # Genuinely inert: the board is declared but not read at boot.
    INERT = SAMPLE_MANIFEST + """
close:
  bulletin:
    file: ABBA.md
    check_at_boot: false
"""

    def test_live_board_is_detected(self):
        self.assertEqual(meter.bulletin_boot_file(self.LIVE), "ABBA.md")

    def test_check_at_boot_is_the_gate_not_an_agents_block(self):
        """The whole of MP#30's meter fix. `agents:` was a proxy for 'is this board
        live' that held on one instance and nowhere else; the manifest states the
        fact directly, so read the fact rather than a correlate of it."""
        self.assertEqual(meter.bulletin_boot_file(self.NO_AGENTS), "ABBA.md")
        self.assertEqual(meter._bulletin_from_lines(self.NO_AGENTS), "ABBA.md")

    def test_inert_board_is_not_charged(self):
        """check_at_boot: false — declared but never opened at boot."""
        self.assertIsNone(meter.bulletin_boot_file(self.INERT))

    def test_no_close_block_at_all(self):
        self.assertIsNone(meter.bulletin_boot_file(SAMPLE_MANIFEST))

    def test_bulletin_lands_in_the_boot_list(self):
        lists = meter.parse_load_lists(self.LIVE)
        self.assertIn("ABBA.md", lists["boot"])
        # and it does not displace the declared entries
        self.assertIn("MERGE_PLAN.md", lists["boot"])
        self.assertIn("config/KERNEL.yaml", lists["boot"])

    def test_inert_board_absent_from_boot_list(self):
        self.assertNotIn("ABBA.md", meter.parse_load_lists(self.INERT)["boot"])

    def test_no_agents_block_still_lands_in_the_boot_list(self):
        self.assertIn("ABBA.md", meter.parse_load_lists(self.NO_AGENTS)["boot"])

    def test_no_duplicate_when_also_declared_in_boot(self):
        dup = self.LIVE.replace("  - config/KERNEL.yaml", "  - config/KERNEL.yaml\n  - ABBA.md")
        self.assertEqual(meter.parse_load_lists(dup)["boot"].count("ABBA.md"), 1)

    def test_line_fallback_matches_yaml_path(self):
        # Exercise the dependency-free fallback directly, regardless of whether
        # PyYAML is installed — otherwise this branch is never covered here.
        self.assertEqual(meter._bulletin_from_lines(self.LIVE), "ABBA.md")
        self.assertIsNone(meter._bulletin_from_lines(self.INERT))
        self.assertIsNone(meter._bulletin_from_lines(SAMPLE_MANIFEST))

    def test_scan_mode_detection_and_pricing(self):
        """A scanned board is priced as header index + one agent's own mail. Charging
        the whole file reports a cost the protocol stopped paying."""
        scan = self.LIVE.replace("    check_at_boot: true",
                                 "    check_at_boot: true\n    mode: scan_headers")
        self.assertFalse(meter.bulletin_scan_enabled(self.LIVE))
        self.assertTrue(meter.bulletin_scan_enabled(scan))

        root = tempfile.mkdtemp(prefix="meter_scan_")
        try:
            board = ["# Board\n"]
            for i in range(20):
                board.append(f"### [{i}] To: Someone · From: X · 2026-07-31 · Status: OPEN\n")
                board.append("body " * 200 + "\n")
            with open(os.path.join(root, "ABBA.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(board))
            full = meter.measure_file(root, "ABBA.md")
            scanned = meter.measure_bulletin_scan(root, "ABBA.md")
            self.assertLess(scanned, full // 2, "scan must be far cheaper than the full read")
            self.assertGreater(scanned, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_pricing_falls_back_when_no_headers_parse(self):
        """Same posture as the scan itself: if the headers don't parse, pay for the
        whole file rather than silently under-report it."""
        root = tempfile.mkdtemp(prefix="meter_scan2_")
        try:
            with open(os.path.join(root, "ABBA.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# Board\n\nno parseable headers here at all\n")
            self.assertEqual(meter.measure_bulletin_scan(root, "ABBA.md"),
                             meter.measure_file(root, "ABBA.md"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_line_fallback_strips_inline_comment(self):
        withc = self.LIVE.replace("    file: ABBA.md", "    file: ABBA.md   # the board")
        self.assertEqual(meter._bulletin_from_lines(withc), "ABBA.md")


class TestResolveManifest(unittest.TestCase):
    """ARCH_MANIFEST — the same knob the g5 boring-guard reads."""

    def test_default_when_unset(self):
        self.assertEqual(meter.resolve_manifest({}), meter.MANIFEST_DEFAULT)

    def test_env_override_wins(self):
        self.assertEqual(meter.resolve_manifest({"ARCH_MANIFEST": "ARCH.yaml"}),
                         "ARCH.yaml")

    def test_blank_falls_back_to_default(self):
        # Empty or whitespace-only must not yield a manifest named "" or "  ".
        for blank in ("", "   ", "\t"):
            self.assertEqual(meter.resolve_manifest({"ARCH_MANIFEST": blank}),
                             meter.MANIFEST_DEFAULT)

    def test_case_is_preserved(self):
        # The hook lowercases (it compares); the meter must not (it opens).
        # On a case-sensitive filesystem, lowercasing here would fail to open.
        self.assertEqual(meter.resolve_manifest({"ARCH_MANIFEST": "MyProject.YAML"}),
                         "MyProject.YAML")


class TestSeriesRow(unittest.TestCase):
    """The row schema. This is the part that cannot be changed later without
    orphaning every row already written, so it is pinned by tests."""

    def _data(self):
        return {
            "manifest": "4SYNC.yaml",
            "bytes_per_token": 4,
            "boot": [{"path": "MERGE_PLAN.md", "bytes": 800, "tokens": 200, "missing": False},
                     {"path": "gone.md", "bytes": 0, "tokens": 0, "missing": True}],
            "deferred": [{"path": "REF.yaml", "bytes": 400, "tokens": 100, "missing": False}],
            "boot_total_bytes": 800, "boot_total_tokens": 200,
            "deferred_total_bytes": 400, "deferred_total_tokens": 100,
            "deferred_pct": 33.3333,
        }

    def test_per_file_bytes_are_recorded(self):
        """The wedge chart's whole requirement: WHICH file grew, not just that
        boot did. Unrecoverable from a scalar after the fact."""
        row = meter.series_row(self._data())
        self.assertEqual(row["files"]["MERGE_PLAN.md"], 800)

    def test_missing_files_are_omitted_not_zeroed(self):
        """A zero would read as 'this file was empty', which is a different and
        false claim from 'this file did not exist at that commit'."""
        self.assertNotIn("gone.md", meter.series_row(self._data())["files"])

    def test_per_file_tokens_are_not_stored(self):
        """Tokens are bytes // divisor. Storing both invites a series whose two
        halves disagree the first time the divisor is tuned."""
        row = meter.series_row(self._data())
        self.assertIsInstance(row["files"]["MERGE_PLAN.md"], int)
        self.assertEqual(set(row["files"].keys()), {"MERGE_PLAN.md"})

    def test_commit_and_note_are_carried(self):
        row = meter.series_row(self._data(), commit="abc1234", note="after trim")
        self.assertEqual(row["commit"], "abc1234")
        self.assertEqual(row["note"], "after trim")

    def test_absent_commit_is_null_not_fatal(self):
        self.assertIsNone(meter.series_row(self._data())["commit"])

    def test_row_is_json_serialisable(self):
        json.dumps(meter.series_row(self._data()))


class TestAppendSeries(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="meter_series_")
        self.path = os.path.join(self.root, "metrics", "roc_series.jsonl")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_the_directory(self):
        meter.append_series(self.path, {"ts": "t", "boot_bytes": 1})
        self.assertTrue(os.path.isfile(self.path))

    def test_appends_never_overwrites(self):
        """The series has no backfill. A write path that can truncate destroys
        the only copy of data that cannot be regenerated."""
        for i in range(3):
            meter.append_series(self.path, {"ts": f"t{i}", "boot_bytes": i})
        with open(self.path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([r["boot_bytes"] for r in rows], [0, 1, 2])

    def test_one_json_object_per_line(self):
        meter.append_series(self.path, {"ts": "t", "nested": {"a": 1}})
        meter.append_series(self.path, {"ts": "u", "nested": {"b": 2}})
        with open(self.path, encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        for l in lines:
            json.loads(l)

    def test_a_changed_boot_stack_does_not_break_earlier_rows(self):
        """Why JSONL and not TSV: the stack being measured is itself what
        changes, so fixed columns break exactly when the series gets useful."""
        meter.append_series(self.path, {"ts": "t", "files": {"a.md": 1}})
        meter.append_series(self.path, {"ts": "u", "files": {"a.md": 1, "b.md": 2},
                                        "new_field": True})
        with open(self.path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(rows[0]["files"], {"a.md": 1})
        self.assertEqual(len(rows[1]["files"]), 2)


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_meter.py ═══