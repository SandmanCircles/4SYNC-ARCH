#!/usr/bin/env python3
"""
Stdlib unittest suite for meter.py — the boot-cost meter.

First tests in this repo; sets the pattern (mirrors rotate.py's testable-
pure-function style). No network, no third-party deps. Imports meter from the
same scripts/ directory. Run either way:

  python -m unittest test_meter          # from the scripts/ dir
  python scripts/test_meter.py           # from the repo root
"""

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


class TestBuildReport(unittest.TestCase):
    def setUp(self):
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
    # Same close block, no `agents:` — the board is inert and must NOT be charged.
    INERT = SAMPLE_MANIFEST + """
close:
  bulletin:
    file: ABBA.md
    check_at_boot: true
"""

    def test_live_board_is_detected(self):
        self.assertEqual(meter.bulletin_boot_file(self.LIVE), "ABBA.md")

    def test_inert_board_is_not_charged(self):
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

    def test_no_duplicate_when_also_declared_in_boot(self):
        dup = self.LIVE.replace("  - config/KERNEL.yaml", "  - config/KERNEL.yaml\n  - ABBA.md")
        self.assertEqual(meter.parse_load_lists(dup)["boot"].count("ABBA.md"), 1)

    def test_line_fallback_matches_yaml_path(self):
        # Exercise the dependency-free fallback directly, regardless of whether
        # PyYAML is installed — otherwise this branch is never covered here.
        self.assertEqual(meter._bulletin_from_lines(self.LIVE), "ABBA.md")
        self.assertIsNone(meter._bulletin_from_lines(self.INERT))
        self.assertIsNone(meter._bulletin_from_lines(SAMPLE_MANIFEST))

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


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_meter.py ═══