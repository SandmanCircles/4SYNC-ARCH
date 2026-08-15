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

    def renamed(self, name, manifest, peers=()):
        d = os.path.join(self.root, name)
        for sub in ("inbox", "outbox"):
            os.makedirs(os.path.join(d, sub))
        lines = ["sync_version: " + chr(34) + "1.0" + chr(34),
                 "mail:", "  name: " + name,
                 "  peers: [" + ", ".join(peers) + "]"]
        with open(os.path.join(d, manifest), "w", encoding="utf-8") as fh:
            fh.write("".join(l + chr(10) for l in lines))
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

if __name__ == "__main__":
    unittest.main()
