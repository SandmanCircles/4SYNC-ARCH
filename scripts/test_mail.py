#!/usr/bin/env python3
"""Tests for mail.py — MP#84.

The load-bearing ones are the fence tests and the receipt test. This moves files
between two instances on one disk, so the properties that matter are: it only ever
writes inside the instance it was pointed at, and it only deletes on confirmed
arrival — because AN EMPTY OUTBOX IS THE DELIVERY RECEIPT, and a sweep that
deleted on anything weaker would make an empty outbox mean nothing.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mail  # noqa: E402


class MailCase(unittest.TestCase):
    """Two instances on one disk, each with its own manifest, inbox and outbox."""

    def setUp(self):
        super().setUp()
        # ARCH_MANIFEST is pinned because the fixtures write a literal 4SYNC.yaml
        # and the lookup honours the ambient variable — CI's renamed-manifest leg
        # caught exactly this in MP#79's first suite.
        prev = os.environ.get("ARCH_MANIFEST")
        os.environ["ARCH_MANIFEST"] = "4SYNC.yaml"
        self.addCleanup(lambda: os.environ.__setitem__("ARCH_MANIFEST", prev)
                        if prev is not None else os.environ.pop("ARCH_MANIFEST", None))
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="arch-mail-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def instance(self, name, peers=(), declare=True):
        d = os.path.join(self.root, name)
        for sub in ("inbox", "outbox"):
            os.makedirs(os.path.join(d, sub))
        lines = ["instance:", "  name: " + name]
        if declare:
            lines += ["mail:", "  name: " + name,
                      "  peers: [" + ", ".join(peers) + "]"]
        with open(os.path.join(d, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("".join(l + chr(10) for l in lines))
        return d

    def put(self, inst, box, filename, body="hello"):
        p = os.path.join(inst, box, filename)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def ls(self, inst, box):
        return sorted(f for f in os.listdir(os.path.join(inst, box)) if f != ".gitkeep")


class TestAddressing(MailCase):

    def test_a_hyphenated_project_name_still_matches(self):
        """4SYNC-ARCH contains a hyphen. Positional parsing would read its second
        field as a recipient; matching declared names literally cannot."""
        self.assertTrue(mail.addressed_to(
            "4SYNC-ARCH-COWORK-2026-08-15-note.md", "4SYNC-ARCH", "COWORK"))
        self.assertFalse(mail.addressed_to(
            "4SYNC-ARCH-COWORK-2026-08-15-note.md", "4SYNC", "ARCH"))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(mail.addressed_to("cowork-4sync-2026-08-15-x.md", "COWORK", "4SYNC"))

    def test_mail_for_someone_else_is_not_mine(self):
        self.assertFalse(mail.addressed_to("COWORK-OTHER-2026-08-15-x.md", "COWORK", "4SYNC"))

    def test_an_undeclared_side_never_matches(self):
        self.assertFalse(mail.addressed_to("A-B-2026-08-15-x.md", None, "B"))


class TestPull(MailCase):

    def test_it_copies_only_what_is_addressed_to_it(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        self.put(b, "outbox", "BETA-ALPHA-2026-08-15-for-alpha.md")
        self.put(b, "outbox", "BETA-GAMMA-2026-08-15-not-for-alpha.md")
        actions, _ = mail.pull(a, apply=True)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.ls(a, "inbox"), ["BETA-ALPHA-2026-08-15-for-alpha.md"])

    def test_dry_run_writes_nothing(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        self.put(b, "outbox", "BETA-ALPHA-2026-08-15-x.md")
        actions, _ = mail.pull(a)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.ls(a, "inbox"), [])

    def test_it_never_writes_into_the_peer(self):
        """The fence property. A pull reads the peer and writes only at home."""
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        self.put(b, "outbox", "BETA-ALPHA-2026-08-15-x.md")
        before = (self.ls(b, "inbox"), self.ls(b, "outbox"))
        mail.pull(a, apply=True)
        self.assertEqual((self.ls(b, "inbox"), self.ls(b, "outbox")), before,
                         "pull modified the peer instance")

    def test_pulling_twice_does_not_duplicate(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        self.put(b, "outbox", "BETA-ALPHA-2026-08-15-x.md")
        mail.pull(a, apply=True)
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(actions, [])
        self.assertTrue(any("already in inbox" in n for n in notes), notes)

    def test_an_undeclared_instance_does_nothing(self):
        a = self.instance("ALPHA", declare=False)
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(actions, [])
        self.assertTrue(any("declares no" in n for n in notes), notes)

    def test_a_peer_without_an_outbox_is_skipped_not_failed(self):
        a = self.instance("ALPHA", peers=["../GONE"])
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(actions, [])
        self.assertTrue(any("skipped" in n for n in notes), notes)


class TestSweepIsTheDeliveryReceipt(MailCase):

    def test_it_deletes_only_after_the_file_appears_in_their_inbox(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        fn = "ALPHA-BETA-2026-08-15-x.md"
        self.put(a, "outbox", fn)
        actions, notes = mail.sweep(a, apply=True)
        self.assertEqual(actions, [], "swept before delivery")
        self.assertEqual(self.ls(a, "outbox"), [fn])
        self.assertTrue(any("undelivered" in n for n in notes), notes)

        self.put(b, "inbox", fn)                      # delivery happens
        actions, _ = mail.sweep(a, apply=True)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.ls(a, "outbox"), [], "an empty outbox is the receipt")

    def test_sweep_never_touches_the_peer(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        fn = "ALPHA-BETA-2026-08-15-x.md"
        self.put(a, "outbox", fn)
        self.put(b, "inbox", fn)
        mail.sweep(a, apply=True)
        self.assertEqual(self.ls(b, "inbox"), [fn], "sweep deleted the delivered copy")

    def test_dry_run_deletes_nothing(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        fn = "ALPHA-BETA-2026-08-15-x.md"
        self.put(a, "outbox", fn)
        self.put(b, "inbox", fn)
        actions, _ = mail.sweep(a)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.ls(a, "outbox"), [fn])


class TestRoundTrip(MailCase):

    def test_send_pull_sweep(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.instance("BETA", peers=["../ALPHA"])
        fn = "ALPHA-BETA-2026-08-15-round-trip.md"
        self.put(a, "outbox", fn, body="the message")
        mail.pull(b, apply=True)                       # B collects
        self.assertEqual(self.ls(b, "inbox"), [fn])
        mail.sweep(a, apply=True)                      # A confirms and clears
        self.assertEqual(self.ls(a, "outbox"), [])
        with open(os.path.join(b, "inbox", fn), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "the message")


class TestConfigParsing(MailCase):

    def test_inline_and_block_peer_lists_both_parse(self):
        d = os.path.join(self.root, "BLOCKY")
        os.makedirs(d)
        with open(os.path.join(d, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("mail:" + chr(10) + "  name: BLOCKY" + chr(10)
                     + "  peers:" + chr(10) + "    - ../ONE" + chr(10) + "    - ../TWO" + chr(10))
        self.assertEqual(mail.mail_config(d), ("BLOCKY", ["../ONE", "../TWO"]))



class TestPeerManifestDiscovery(MailCase):
    """A PEER'S MANIFEST NAME IS UNKNOWABLE FROM OUTSIDE, and assuming it is the
    defect this class exists for. MP#20 has genesis rename every instance's manifest
    to <PROJECT>.yaml, so the neighbour of a 4SYNC instance is 4CITE.yaml, not
    4SYNC.yaml. The first implementation looked for its OWN manifest name inside the
    peer, found nothing, and reported "no declared name, skipped" — which reads as
    "the peer has not opted in" and would have stayed silent forever.

    Found live against the real neighbour on 2026-08-15, minutes after Michael
    declared the block correctly and it still did not resolve."""

    def renamed(self, name, manifest, peers=(), prologue=""):
        d = os.path.join(self.root, name)
        for sub in ("inbox", "outbox"):
            os.makedirs(os.path.join(d, sub))
        lines = ["sync_version: " + chr(34) + "1.0" + chr(34),
                 "boot:", "  - config/KERNEL.yaml",
                 "mail:", "  name: " + name,
                 "  peers: [" + ", ".join(peers) + "]"]
        with open(os.path.join(d, manifest), "w", encoding="utf-8") as fh:
            fh.write(prologue + "".join(l + chr(10) for l in lines))
        return d

    def test_a_peer_with_a_renamed_manifest_is_found(self):
        b = self.renamed("BETA", "BETA.yaml", peers=["../ALPHA"])
        self.assertEqual(mail.peer_name(b), "BETA")

    def test_mail_arrives_from_a_peer_whose_manifest_is_renamed(self):
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.renamed("BETA", "BETA.yaml", peers=["../ALPHA"])
        self.put(b, "outbox", "BETA-ALPHA-2026-08-15-renamed.md")
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(len(actions), 1, notes)
        self.assertEqual(self.ls(a, "inbox"), ["BETA-ALPHA-2026-08-15-renamed.md"])

    def test_a_peer_manifest_is_found_behind_a_prologue_past_4kb(self):
        """The read window must serve the 16,384-byte manifest cap, not a 4 KB guess.

        A prologue well INSIDE the declared cap used to push `sync_version:` past a
        fixed 4,096-byte read, and the peer then resolved as "not opted in" — which
        fails as the SILENT MAIL-DROP that MP#84 names as this feature's one silent
        failure mode. Nothing raises; the mail simply never arrives.

        Same defect SYN-090 fixed in rotate.py and wire_hooks.py. That sweep CITED
        this function as its prior art ("the same anchoring mail.py's peer-detect
        already uses") and left it behind, so the argument written into rotate.py's
        docstring — that 4 KB undercuts the very cap it exists to serve — went
        unapplied in the one place it was borrowed FROM. Found by the SYN-091 ultra
        review, which is the first instrument to look at mail.py since.
        """
        a = self.instance("ALPHA", peers=["../BETA"])
        b = self.renamed("BETA", "BETA.yaml", peers=["../ALPHA"],
                         prologue="# " + "pad " * 1400 + chr(10))
        self.assertGreater(os.path.getsize(os.path.join(b, "BETA.yaml")), 4096)
        self.assertEqual(mail.peer_name(b), "BETA")
        self.put(b, "outbox", "BETA-ALPHA-2026-08-19-prologue.md")
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(len(actions), 1, notes)

    def test_a_directory_with_no_arch_manifest_is_still_skipped(self):
        """Discovery must not turn any stray yaml into a peer."""
        a = self.instance("ALPHA", peers=["../PLAIN"])
        d = os.path.join(self.root, "PLAIN")
        os.makedirs(os.path.join(d, "outbox"))
        with open(os.path.join(d, "docker-compose.yaml"), "w", encoding="utf-8") as fh:
            fh.write("services: {}" + chr(10))
        actions, notes = mail.pull(a, apply=True)
        self.assertEqual(actions, [])
        self.assertTrue(any("skipped" in n for n in notes), notes)

class TestTheShippedPlaceholderIsNotAName(MailCase):
    """Found in the pre-v1.1.1 repo scan, and it had two heads.

    The shipped manifest wrote `name: [PROJECT]` unquoted, which YAML reads as a
    LIST — truthy, so an adopter who never filled it in would have sent mail from a
    project literally called `['PROJECT']`, and the mistake would surface at the far
    end of somebody else's inbox. Meanwhile the PyYAML-absent fallback read the same
    line as the STRING `[PROJECT]`, so the two paths disagreed about the same file —
    and PyYAML-absent is the modal adopter install."""

    def _write(self, name_line):
        d = os.path.join(self.root, "inst")
        os.makedirs(d)
        with open(os.path.join(d, "4SYNC.yaml"), "w", encoding="utf-8") as fh:
            fh.write("instance:\n  name: X\nmail:\n  name: %s\n  peers: []\n" % name_line)
        return d

    def test_a_bracketed_placeholder_is_undeclared(self):
        self.assertIsNone(mail.mail_config(self._write('"[PROJECT]"'))[0])

    def test_an_unquoted_bracketed_placeholder_is_undeclared_too(self):
        """The exact line the product shipped. YAML makes it a list; it still is
        not a name."""
        self.assertIsNone(mail.mail_config(self._write("[PROJECT]"))[0])

    def test_a_real_name_still_reads(self):
        self.assertEqual(mail.mail_config(self._write("4SYNC"))[0], "4SYNC")

    def test_a_quoted_real_name_still_reads(self):
        self.assertEqual(mail.mail_config(self._write('"4SYNC"'))[0], "4SYNC")

    def test_an_unfilled_instance_pulls_nothing_and_says_why(self):
        actions, notes = mail.pull(self._write("[PROJECT]"), apply=True)
        self.assertEqual(actions, [])
        self.assertTrue(any("no `mail.name`" in n for n in notes), notes)

    def test_the_shipped_manifest_itself_declares_no_usable_name(self):
        """Against the real file: a fresh clone must not be addressable until
        somebody names it."""
        product = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.exists(os.path.join(product, "4SYNC.yaml")):
            self.skipTest("shipped manifest not present")
        self.assertIsNone(mail.mail_config(product)[0])


class TestANameIsNotAReceipt(MailCase):
    """`sweep` deletes our only copy of a message on the strength of a file
    existing in the addressee's inbox. Two ways that was not quite a receipt."""

    def _pair(self):
        a = self.instance("A", peers=["../B"])
        b = self.instance("B", peers=["../A"])
        return a, b

    def test_a_same_named_file_with_different_bytes_is_not_delivery(self):
        a, b = self._pair()
        self.put(a, "outbox", "A-B-2026-08-15-note.md", "the real message")
        self.put(b, "inbox", "A-B-2026-08-15-note.md", "truncated")
        actions, notes = mail.sweep(a, apply=True)
        self.assertEqual(actions, [])
        self.assertIn("A-B-2026-08-15-note.md", self.ls(a, "outbox"))
        self.assertTrue(any("differs" in n for n in notes), notes)

    def test_an_identical_copy_is_delivery(self):
        a, b = self._pair()
        self.put(a, "outbox", "A-B-2026-08-15-note.md", "same")
        self.put(b, "inbox", "A-B-2026-08-15-note.md", "same")
        actions, _ = mail.sweep(a, apply=True)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.ls(a, "outbox"), [])

    def test_pull_leaves_no_part_file_behind(self):
        """The copy lands under a temporary name and is renamed into place, so an
        interrupted write cannot leave something that reads as delivered."""
        a, b = self._pair()
        self.put(a, "outbox", "A-B-2026-08-15-note.md", "body")
        mail.pull(b, apply=True)
        self.assertEqual(self.ls(b, "inbox"), ["A-B-2026-08-15-note.md"])
        self.assertFalse([f for f in os.listdir(os.path.join(b, "inbox"))
                          if f.endswith(".part")])


if __name__ == "__main__":
    unittest.main()

# ═══ EOF test_mail.py ═══
