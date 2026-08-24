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
from datetime import datetime
from importlib import reload
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

    def test_indent_does_not_decide_whether_the_manifest_is_read(self):
        """MP#73. This lookup anchored on EXACTLY two spaces of indent — which is
        only what this project's own manifests happen to use.

        Reindent to four (any YAML formatter, most editor defaults) and the block
        was simply not found: the receipt reported no bulletin, silently, for a
        manifest that declares one. THIS FILE HAS NO PyYAML PATH AT ALL, so the
        regex is not a degraded fallback here — it is the only parser, for every
        adopter, always.

        Tabs are included because a manifest that mixes them is exactly the kind
        of thing a formatter produces and nobody inspects."""
        four = MANIFEST.replace("\n  ", "\n    ")
        tabs = MANIFEST.replace("\n  ", "\n\t")
        self.assertEqual(ss.parse_bulletin_at_boot(four), "ABBA.md")
        self.assertEqual(ss.parse_bulletin_at_boot(tabs), "ABBA.md")

    def test_a_deeper_key_is_still_scoped_to_its_own_block(self):
        """Loosening the anchor must not let the search wander into a sibling
        block — the scoping was the half of the old regex that was right."""
        m = MANIFEST.replace("\n  ", "\n    ")
        self.assertIsNone(ss.parse_bulletin_at_boot(
            m.replace("check_at_boot: true", "check_at_boot: false")))


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


class TestCustomConfigDir(EnvCase):
    """SYN-108 item 2 — `ARCH_CONFIG_DIR` was honoured by one hook and not the other.

    `pre_tool_use.py` reads the env var; this file hardcoded `CONFIG_DIR = "config"`.
    An instance with a renamed config dir therefore got GUARDS but a permanently
    silent boot receipt: `_instance_root` walked to the filesystem root, found
    nothing, and returned None — which this hook renders as no output at all,
    indistinguishable from a hook that was never wired.

    That is the exact failure this codebase lectures adopters about, in the file
    whose whole job is to announce that the machinery is alive."""

    def _instance(self, config_dirname):
        root = tempfile.mkdtemp(prefix="ss_cfg_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, config_dirname))
        for rel, text in (("4SYNC.yaml", MANIFEST),
                          ("MERGE_PLAN.md", "x" * 800),
                          (config_dirname + "/KERNEL.yaml",
                           "meta:\n  file: KERNEL.yaml\n# ═══ EOF KERNEL.yaml ═══\n")):
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        return root

    def test_a_renamed_config_dir_is_still_found(self):
        os.environ["ARCH_CONFIG_DIR"] = "conf"
        self.addCleanup(os.environ.pop, "ARCH_CONFIG_DIR", None)
        reload(ss)
        root = self._instance("conf")
        self.assertEqual(os.path.realpath(ss._instance_root(root)),
                         os.path.realpath(root))

    def test_the_default_is_unchanged_when_the_var_is_absent(self):
        os.environ.pop("ARCH_CONFIG_DIR", None)
        reload(ss)
        root = self._instance("config")
        self.assertEqual(os.path.realpath(ss._instance_root(root)),
                         os.path.realpath(root))

    def test_both_hooks_resolve_the_same_root(self):
        """The point of the item, not just the symptom. Guards and receipt must
        agree about where the instance is, or one of them is protecting or
        reporting on a directory the other has never heard of."""
        os.environ["ARCH_CONFIG_DIR"] = "conf"
        self.addCleanup(os.environ.pop, "ARCH_CONFIG_DIR", None)
        reload(ss)
        here = os.path.dirname(os.path.abspath(ss.__file__))
        sys.path.insert(0, here)
        import pre_tool_use as guard
        reload(guard)
        root = self._instance("conf")
        self.assertEqual(os.path.realpath(ss._instance_root(root)),
                         os.path.realpath(guard._instance_root(root, strict=True)))


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


class TestBootGrowth(InstanceCase):
    """MP#62. A close-time size report fired at every close for five days while
    the file it named grew 72%, and it was never acted on — a warning delivered
    to a session that is trying to finish loses to finishing. The same sentence
    at boot reaches a session with the whole session ahead of it."""

    LEDGER = 40_000        # fixed sizes so both gates are exercised deterministically

    def setUp(self):
        super().setUp()
        self._write("MERGE_PLAN.md", "x" * self.LEDGER)

    def _series(self, files, ts="2026-08-10T12:00:47"):
        self._write(os.path.join("metrics", "roc_series.jsonl"),
                    json.dumps({"ts": ts, "files": files}) + "\n")

    def test_growth_past_both_gates_is_named(self):
        self._series({"MERGE_PLAN.md": self.LEDGER // 2})     # +20,000 B, +100%
        r = self._receipt()
        self.assertIn("BOOT FILES GREW", r)
        self.assertIn("MERGE_PLAN.md", r)

    def test_no_series_is_silent(self):
        """An adopter who has never run the meter has no series. A check they
        cannot satisfy is a false alarm they learn to ignore."""
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_shrinkage_is_never_reported_as_growth(self):
        self._series({"MERGE_PLAN.md": self.LEDGER * 2})
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_identical_size_is_silent(self):
        self._series({"MERGE_PLAN.md": self.LEDGER})
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_small_absolute_growth_is_not_news(self):
        """Both gates must trip. A file that grew 100 B is noise at any percent."""
        self._series({"MERGE_PLAN.md": self.LEDGER - 100})
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_large_absolute_but_small_percent_is_not_news(self):
        """+1,500 B clears the byte gate; 3.9% does not clear the percent gate."""
        self._series({"MERGE_PLAN.md": self.LEDGER - 1500})
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_env_can_lower_the_percent_gate(self):
        self._series({"MERGE_PLAN.md": self.LEDGER - 1500})
        with mock.patch.dict(os.environ, {ss.GROWTH_PCT_ENV: "0.1"}):
            self.assertIn("BOOT FILES GREW", self._receipt())

    def test_junk_env_value_does_not_lose_the_check(self):
        self._series({"MERGE_PLAN.md": self.LEDGER // 2})
        with mock.patch.dict(os.environ, {ss.GROWTH_PCT_ENV: "banana"}):
            self.assertIn("BOOT FILES GREW", self._receipt())

    def test_corrupt_series_never_fails_the_boot(self):
        self._write(os.path.join("metrics", "roc_series.jsonl"), "{not json\n")
        r = self._receipt()
        self.assertIn("BOOT RECEIPT", r)
        self.assertNotIn("BOOT FILES GREW", r)

    def test_last_row_wins(self):
        self._write(os.path.join("metrics", "roc_series.jsonl"),
                    json.dumps({"ts": "old",
                                "files": {"MERGE_PLAN.md": self.LEDGER}}) + "\n"
                    + json.dumps({"ts": "new",
                                  "files": {"MERGE_PLAN.md": self.LEDGER // 2}}) + "\n")
        self.assertIn("BOOT FILES GREW", self._receipt())

    def test_a_file_absent_from_the_baseline_is_not_growth(self):
        """No baseline means no comparison, never a 100% jump."""
        self._series({"config/KERNEL.yaml": 10})
        self.assertNotIn("BOOT FILES GREW", self._receipt())

    def test_the_scanned_bulletin_is_never_compared(self):
        """REGRESSION, found on the first live run. meter.py logs the bulletin at
        its SCAN estimate; the receipt knows only the whole-file size. Comparing
        them reported +1135% on a file nobody had touched. A scanned file has no
        comparable baseline, so it is excluded rather than approximated."""
        self._write("ABBA.md", "y" * 40_000)
        self._series({"ABBA.md": 1_322, "MERGE_PLAN.md": self.LEDGER})
        r = self._receipt()
        self.assertNotIn("BOOT FILES GREW", r)

    def test_the_timestamp_of_the_baseline_travels(self):
        self._series({"MERGE_PLAN.md": self.LEDGER // 2}, ts="2026-08-10T12:00:47")
        self.assertIn("2026-08-10T12:00:47", self._receipt())

    def test_the_advice_names_the_remedy_not_just_the_number(self):
        self._series({"MERGE_PLAN.md": self.LEDGER // 2})
        self.assertIn("ON-DEMAND", self._receipt())


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

    def test_the_caveats_travel_with_any_row(self):
        """'last_activity is not activity' has to arrive WITH the warning, or the
        reader treats an idle-looking row as an absent session.

        TWO caveats now, not one (SYN-110 defect 1). The original covered writes the
        hook cannot see — git commands. The second covers a whole SURFACE the hook
        does not run on, where the file records nothing at all; without it a reader
        takes "not listed" as evidence of absence, which is how a hookless seat's
        claim gets taken while it is working."""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self._debt([f"abcd1234-x\t{now}\t{now}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("observed BY A HOOK", r)
        self.assertIn("no hooks", r)
        self.assertIn("not evidence of absence", r)

    # ── SYN-110 defect 2: naive local stamps, read as local ─────────────────
    #
    # Reported by an adopter against v1.1.4. Self-consistent on one machine in one
    # zone, which is why it survived; it breaks the moment an instance is used from
    # two machines in different zones — `live_within` then subtracts a stamp written
    # in one zone from a clock read in another. Both directions are wrong and one is
    # dangerous: a genuinely live session reading as STALE is how two sessions end
    # up editing the same ledger believing they are alone.

    def test_an_offset_aware_row_in_another_zone_reads_correctly(self):
        """THE CASE NO SAME-ZONE TEST CAN PROVE, and the reason this row exists.

        A stamp written 2 minutes ago on a machine 8 hours away is RECENT. Read as
        naive local it looks 8 hours old and the claim reads as abandoned."""
        from datetime import datetime, timedelta, timezone
        far = timezone(timedelta(hours=-8))
        recent_there = (datetime.now(timezone.utc) - timedelta(minutes=2)
                        ).astimezone(far).isoformat(timespec="seconds")
        self._debt([f"abcd1234-x\t{recent_there}\t{recent_there}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("LIVE", r)
        self.assertNotIn("UNDEPOSITED", r)

    def test_an_offset_aware_row_that_is_genuinely_old_still_reads_old(self):
        """The control in the other direction — the fix must not make everything live."""
        from datetime import datetime, timedelta, timezone
        far = timezone(timedelta(hours=+9))
        old_there = (datetime.now(timezone.utc) - timedelta(days=1)
                     ).astimezone(far).isoformat(timespec="seconds")
        self._debt([f"abcd1234-x\t{old_there}\t{old_there}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("UNDEPOSITED", r)
        self.assertNotIn("CONTESTED", r)

    def test_existing_naive_rows_keep_working(self):
        """MIGRATION IS NOT OPTIONAL: every adopter has a file full of naive rows,
        and they must keep meaning what they meant. A naive stamp parses to a
        tz-naive datetime whose .timestamp() interprets it as LOCAL — which is
        exactly the old behaviour, so one reader covers both eras."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._debt([f"abcd1234-x\t{now}\t{now}\tC:\\proj\tunwrapped"])
        self.assertIn("LIVE", self._receipt())

    def test_a_file_mixing_both_eras_is_read_row_by_row(self):
        """The real shape during migration: old rows written before the upgrade,
        new rows after, in one file."""
        from datetime import datetime, timedelta, timezone
        naive_old = time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.localtime(time.time() - 86400))
        aware_new = datetime.now().astimezone().isoformat(timespec="seconds")
        self._debt([f"aaaaaaaa-x\t{naive_old}\t{naive_old}\tC:\\proj\tunwrapped",
                    f"bbbbbbbb-x\t{aware_new}\t{aware_new}\tC:\\proj\tunwrapped"])
        r = self._receipt()
        self.assertIn("LIVE", r)
        self.assertIn("UNDEPOSITED", r)

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


class SessionIdMismatchCase(InstanceCase):
    """SYN-090. The recorder keys debt rows by the hook PAYLOAD's session id;
    `debt.py --clear` defaults to $CLAUDE_CODE_SESSION_ID. A nested or scripted
    run inherits its parent's env value, the two diverge, and the close clears a
    row that does not exist while the real one sits untouched — reported as
    "cleared nothing", which reads like success. The receipt is where the id can
    be SOURCED; the alternative is reading the debt file and picking a row, and
    picking wrong deletes a live session's evidence."""

    def _receipt_with(self, session_id, env_sid):
        prev = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if prev is not None:
            self.addCleanup(os.environ.__setitem__, "CLAUDE_CODE_SESSION_ID", prev)
        if env_sid is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = env_sid
            self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        with open(os.path.join(self.root, "4SYNC.yaml"), encoding="utf-8") as fh:
            text = fh.read()
        return ss.build_receipt(self.root, "4SYNC.yaml", text, "announce", session_id)[0]

    def test_matching_ids_say_nothing(self):
        """SILENT IN THE ORDINARY CASE. A warning printed at every boot is one
        the reader stops seeing — this file has already made that mistake once."""
        out = self._receipt_with("abc123", "abc123")
        self.assertNotIn("MISMATCH", out)
        self.assertNotIn("Debt row id", out)

    def test_diverging_ids_warn_and_give_the_command(self):
        out = self._receipt_with("payload-id", "inherited-parent-id")
        self.assertIn("SESSION ID MISMATCH", out)
        self.assertIn("payload-id", out)
        self.assertIn("inherited-parent-id", out)
        self.assertIn("--session payload-id", out)

    def test_absent_env_surfaces_the_id_because_clear_will_refuse(self):
        out = self._receipt_with("payload-id", None)
        self.assertIn("Debt row id for this session: payload-id", out)
        self.assertIn("refuses rather than guess", out)

    def test_no_payload_id_is_not_an_error(self):
        """A hook payload without session_id must not produce a warning about
        nothing — the receipt never fails a boot over a report."""
        out = self._receipt_with(None, "env-only")
        self.assertNotIn("MISMATCH", out)
        self.assertNotIn("Debt row id", out)


if __name__ == "__main__":
    unittest.main()
# ═══ EOF test_session_start.py ═══
