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
        self.assertIn("4SYNC Token Saving Protocol", report)
        self.assertIn("BOOT TOTAL", report)
        self.assertIn("DEFERRED TOTAL", report)
        self.assertIn("SAVINGS", report)


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_meter.py ═══