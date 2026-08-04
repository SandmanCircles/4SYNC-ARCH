#!/usr/bin/env python3
"""
Stdlib unittest suite for session_start.py — the boot receipt hook.

Run either way:
  python -m unittest test_session_start      # from the hooks/ dir
  python hooks/test_session_start.py         # from the repo root
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import session_start as ss  # noqa: E402


MANIFEST = """\
sync_version: "1.0"

instance:
  name: "Test Instance"

boot:
  # ordered, read whole in one pass
  - MERGE_PLAN.md            # operational state
  - config/KERNEL.yaml       # identity contract

on_demand:
  - config/REFERENCE.yaml

session_debt:
  file: .session_debt.tsv
  live_within: 15m

close:
  bulletin:
    file: ABBA.md
    check_at_boot: true
    mode: scan_headers

bootstrap:
  seed:
    file: SEED.md

# ═══ EOF 4SYNC.yaml ═══
"""

NO_BULLETIN_MANIFEST = MANIFEST.replace("check_at_boot: true", "check_at_boot: false")


class EnvCase(unittest.TestCase):
    """Pin the env this hook reads, so a developer's ambient ARCH_* values cannot
    change the result. The bite is documented on ManifestEnvCase in the rotate and
    meter suites: adopters are told to set ARCH_MANIFEST, and every one who did
    broke a suite that inherited it."""

    ENV = {"ARCH_MANIFEST": "4SYNC.yaml", "ARCH_BOOT_MODE": "announce"}

    def setUp(self):
        super().setUp()
        prev = {k: os.environ.get(k) for k in
                ("ARCH_MANIFEST", "ARCH_BOOT_MODE", "ARCH_DEBT_FILE")}
        os.environ.update(self.ENV)
        os.environ.pop("ARCH_DEBT_FILE", None)

        def restore():
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(restore)


class TestParseBootList(unittest.TestCase):
    def test_reads_entries_in_order(self):
        self.assertEqual(ss.parse_boot_list(MANIFEST),
                         ["MERGE_PLAN.md", "config/KERNEL.yaml"])

    def test_bootstrap_does_not_leak_into_boot(self):
        """`bootstrap:` starts with the same five letters as `boot:`. meter.py hit
        this for real; the anchored regex is why this one does not."""
        self.assertNotIn("SEED.md", ss.parse_boot_list(MANIFEST))

    def test_stops_at_the_next_top_level_key(self):
        self.assertNotIn("config/REFERENCE.yaml", ss.parse_boot_list(MANIFEST))

    def test_absent_boot_key_is_empty_not_fatal(self):
        self.assertEqual(ss.parse_boot_list("instance:\n  name: x\n"), [])

    def test_inline_comments_are_stripped(self):
        for item in ss.parse_boot_list(MANIFEST):
            self.assertNotIn("#", item)


class TestBulletinAtBoot(unittest.TestCase):
    """MP#17's defect, guarded in a second tool. The bulletin is read at boot but
    lives under close.bulletin, not boot: — a receipt that reads only boot:
    under-counts, and then it and meter.py disagree about what boot IS."""

    def test_found_when_check_at_boot_is_true(self):
        self.assertEqual(ss.parse_bulletin_at_boot(MANIFEST), "ABBA.md")

    def test_ignored_when_check_at_boot_is_false(self):
        self.assertIsNone(ss.parse_bulletin_at_boot(NO_BULLETIN_MANIFEST))

    def test_absent_bulletin_block_is_none(self):
        self.assertIsNone(ss.parse_bulletin_at_boot("instance:\n  name: x\n"))


class TestLiveWithin(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(ss.parse_live_within_minutes("  live_within: 15m\n"), 15)

    def test_hours_are_converted(self):
        self.assertEqual(ss.parse_live_within_minutes("  live_within: 2h\n"), 120)

    def test_default_when_absent(self):
        self.assertEqual(ss.parse_live_within_minutes("nothing here"), 15)


class InstanceCase(EnvCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="ss_test_")
        os.makedirs(os.path.join(self.root, "config"))
        self._write("4SYNC.yaml", MANIFEST)
        self._write("MERGE_PLAN.md", "x" * 800)
        self._write("config/KERNEL.yaml",
                    "meta:\n  file: KERNEL.yaml\n# ═══ EOF KERNEL.yaml ═══\n")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def _receipt(self, mode="announce"):
        with open(os.path.join(self.root, "4SYNC.yaml"), encoding="utf-8") as fh:
            text = fh.read()
        return ss.build_receipt(self.root, "4SYNC.yaml", text, mode)[0]


class TestInstanceResolution(InstanceCase):
    def test_finds_root_from_a_subfolder(self):
        sub = os.path.join(self.root, "config")
        self.assertEqual(os.path.realpath(ss._instance_root(sub)),
                         os.path.realpath(self.root))

    def test_outside_an_instance_returns_none(self):
        """Strict on purpose. At user-level placement, a cwd fallback would
        announce a boot stack for every unrelated repo on the machine."""
        empty = tempfile.mkdtemp(prefix="ss_empty_")
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertIsNone(ss._instance_root(empty))


class TestReceipt(InstanceCase):
    def test_names_every_boot_file_in_order(self):
        r = self._receipt()
        self.assertLess(r.index("MERGE_PLAN.md"), r.index("config/KERNEL.yaml"))

    def test_the_manifest_itself_counts_as_boot(self):
        """It is read to START boot, so it is part of the cost. meter.py counts it
        the same way, which is why the two numbers agree."""
        self.assertIn("4SYNC.yaml", self._receipt())

    def test_the_scanned_bulletin_is_listed(self):
        r = self._receipt()
        self.assertIn("ABBA.md", r)
        self.assertIn("SCAN", r)

    def test_the_bulletin_is_marked_scan_not_read(self):
        self.assertIn("do not", self._receipt())

    def test_says_boot_is_not_optional(self):
        """The whole payload. A session that skipped boot must not be able to say
        it did not know."""
        self.assertIn("BOOT IS NOT OPTIONAL", self._receipt())

    def test_reports_the_measured_cost(self):
        self.assertIn("800", self._receipt())

    def test_missing_boot_file_is_flagged(self):
        os.remove(os.path.join(self.root, "MERGE_PLAN.md"))
        self.assertIn("MISSING", self._receipt())

    def test_absent_eof_sentinel_is_flagged(self):
        self._write("config/KERNEL.yaml", "meta:\n  file: KERNEL.yaml\n")
        self.assertIn("SENTINEL ABSENT", self._receipt())

    def test_the_manifests_own_sentinel_is_checked_too(self):
        """It is the first file of the stack; a clipped manifest is the worst of
        all the clipped reads, since every other path is derived from it."""
        self._write("4SYNC.yaml", MANIFEST.replace("\n# ═══ EOF 4SYNC.yaml ═══\n", "\n"))
        self.assertIn("SENTINEL ABSENT", self._receipt())

    def test_present_sentinel_is_not_flagged(self):
        self.assertNotIn("SENTINEL ABSENT", self._receipt())

    def test_non_yaml_boot_file_is_never_sentinel_flagged(self):
        """MERGE_PLAN.md has no sentinel and is not supposed to."""
        self.assertIsNone(ss.check_sentinel(os.path.join(self.root, "MERGE_PLAN.md")))

    def test_announce_mode_does_not_inject_content(self):
        self.assertNotIn("BOOT CONTENT", self._receipt("announce"))

    def test_inject_mode_carries_file_bodies(self):
        r = self._receipt("inject")
        self.assertIn("BOOT CONTENT", r)
        self.assertIn("# ═══ EOF KERNEL.yaml ═══", r)

    def test_inject_mode_tells_the_session_not_to_re_read(self):
        self.assertIn("Do not re-read", self._receipt("inject"))


class TestDebtReadings(InstanceCase):
    def _debt(self, rows):
        body = ss.DEBT_HEADER if hasattr(ss, "DEBT_HEADER") else "# header"
        self._write(".session_debt.tsv", "# header\n" + "\n".join(rows) + "\n")

    def test_recent_row_reads_as_live_and_contested(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._debt([f"abcd1234-x\t{now}\t{now}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("LIVE", r)
        self.assertIn("CONTESTED", r)

    def test_old_row_reads_as_undeposited_not_live(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
        self._debt([f"abcd1234-x\t{old}\t{old}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("UNDEPOSITED", r)
        self.assertNotIn("CONTESTED", r)

    def test_the_bash_caveat_travels_with_any_row(self):
        """'last_activity is not activity' has to arrive WITH the warning, or the
        reader treats an idle-looking row as an absent session."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._debt([f"abcd1234-x\t{now}\t{now}\tC:\\proj\tunwrapped"])
        self.assertIn("FILE WRITES only", self._receipt())

    def test_no_debt_file_is_silent(self):
        r = self._receipt()
        self.assertNotIn("LIVE", r)
        self.assertNotIn("UNDEPOSITED", r)

    def test_malformed_row_is_skipped_not_fatal(self):
        self._debt(["garbage", "a\tb"])
        self._receipt()   # must not raise


class TestMainContract(InstanceCase):
    def _run(self, cwd, env=None):
        e = dict(os.environ)
        e.update(env or {})
        e["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, os.path.abspath(ss.__file__)],
            input=json.dumps({"cwd": cwd, "session_id": "test-session"}),
            capture_output=True, text=True, env=e)

    def test_emits_sessionstart_additional_context(self):
        out = self._run(self.root)
        self.assertEqual(out.returncode, 0)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("BOOT IS NOT OPTIONAL",
                      payload["hookSpecificOutput"]["additionalContext"])

    def test_outside_an_instance_prints_nothing_and_exits_zero(self):
        empty = tempfile.mkdtemp(prefix="ss_empty_")
        self.addCleanup(shutil.rmtree, empty, True)
        out = self._run(empty)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")

    def test_off_mode_is_silent(self):
        out = self._run(self.root, {"ARCH_BOOT_MODE": "off"})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")

    def test_garbage_stdin_never_fails_the_session(self):
        e = dict(os.environ)
        e["PYTHONIOENCODING"] = "utf-8"
        out = subprocess.run([sys.executable, os.path.abspath(ss.__file__)],
                             input="not json at all", capture_output=True,
                             text=True, env=e)
        self.assertEqual(out.returncode, 0)

    def test_config_dir_without_a_manifest_is_not_our_instance(self):
        other = tempfile.mkdtemp(prefix="ss_other_")
        os.makedirs(os.path.join(other, "config"))
        self.addCleanup(shutil.rmtree, other, True)
        out = self._run(other)
        self.assertEqual(out.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_session_start.py ═══
