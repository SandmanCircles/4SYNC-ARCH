#!/usr/bin/env python3
"""
mail.py — cross-project mail between ARCH instances, with nothing shared.

WHY THIS EXISTS

Two ARCH instances on one machine, and a note meant for the other one has no
destination except the human carrying it. `ABBA.md` is per-instance, and the g6
fence stops any session writing into another instance — correctly. Every prior
design put a shared board somewhere and then argued about who could reach it.

This one creates nothing shared. Each instance already has `inbox/` (material
arrives) and `outbox/` (the outbound side), so mail is three ordinary file
operations:

  send    a session drops an addressed file in ITS OWN outbox/
  pull    a session copies anything addressed to it from a peer's outbox/
          into ITS OWN inbox/
  sweep   the sender deletes its copy once the file appears in the addressee's
          inbox/ — AN EMPTY OUTBOX IS THE DELIVERY RECEIPT, the only one
          available and sufficient

NOTHING CROSSES THE g6 FENCE, and no carve-out is requested. Every operation is
either a READ of a peer's outbox (reads have never been fenced) or a WRITE
inside the instance that owns the file. MP#36 is untouched. An earlier draft
argued a courier ought to be permitted; that argument is deliberately not
carried forward, because a design needing no carve-out beats one with a good
case for one.

IT NEVER READS THE MAIL. Addressing is in the filename and nothing here opens a
message body. No model in the loop, nothing to judge, nothing to get wrong.

ADDRESSING — matched against DECLARED NAMES, never parsed by position:

    <FROM>-<TO>-<YYYY-MM-DD>-<subject>.md

Positional parsing would break on any project whose name contains a hyphen
(`4SYNC-ARCH` is one), so this matches the literal `<peer>-<me>-` prefix using
names both instances declare, AND requires the date immediately after it. The
date is what disambiguates: without it, `4SYNC-ARCH-COWORK-...` reads equally as
`4SYNC -> ARCH` and `4SYNC-ARCH -> COWORK`. A project name is not a date.

DECLARE IT (in your manifest, alongside `close:`):

    mail:
      name: 4SYNC              # what other instances address you as
      peers: [../Coworker]     # paths; each peer declares its own name

Peers are DECLARED rather than globbed from the parent directory, so instances
that are not siblings on disk still work — and so nothing is reached by accident.

Cowork participates fully at `send` and by reading its own `inbox/`. It cannot
pull or sweep; any Claude Code session in the same project does those. The worst
case is an outbox that accumulates while nobody opens that project in Claude
Code, which is a delay and not a loss.

Usage:
    python scripts/mail.py pull  --dir .          # dry run
    python scripts/mail.py pull  --dir . --apply
    python scripts/mail.py sweep --dir . --apply

Dry run by default, like every other writer here. Nothing makes a network call.
"""
import argparse
import os
import re
import shutil
import sys

MANIFEST_DEFAULT = "4SYNC.yaml"


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def manifest_path(root, manifest_name=None):
    """This instance's manifest, or a PEER's — which is the hard half.

    A PEER'S MANIFEST NAME IS UNKNOWABLE FROM OUTSIDE. Genesis renames every
    instance's manifest to `<PROJECT>.yaml` (MP#20), so a 4SYNC instance's
    neighbour is `4CITE.yaml`, and `ARCH_MANIFEST` describes THIS instance, never
    theirs. The first version of this function used the local name for both and
    silently reported every correctly-configured peer as "not opted in" — found
    live, minutes after the peer was declared correctly.

    So: use the declared name when a file is actually there, and otherwise
    DISCOVER it — the one `*.yaml` at the root that is an ARCH manifest. Discovery
    requires `sync_version:`, so a stray `docker-compose.yaml` never becomes a peer.
    """
    named = os.path.join(root, os.environ.get("ARCH_MANIFEST")
                         or manifest_name or MANIFEST_DEFAULT)
    if os.path.exists(named):
        return named
    try:
        candidates = sorted(f for f in os.listdir(root) if f.endswith((".yaml", ".yml")))
    except OSError:
        return named
    for fn in candidates:
        p = os.path.join(root, fn)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        if re.search(r"(?m)^sync_version:", head):
            return p
    return named


def mail_config(root, manifest_name=None):
    """Return (name, [peer paths]) from the manifest's `mail:` block.

    Undeclared returns (None, []) — an instance that has not opted in does
    nothing, rather than guessing a name or reaching a directory nobody named.
    """
    p = manifest_path(root, manifest_name)
    if not os.path.exists(p):
        return None, []
    text = _read(p)
    try:
        import yaml  # type: ignore
        block = (yaml.safe_load(text) or {}).get("mail") or {}
        name = block.get("name")
        peers = block.get("peers") or []
        if isinstance(peers, str):
            peers = [peers]
        return (str(name).strip() if name else None,
                [str(x).strip() for x in peers if str(x).strip()])
    except Exception:  # noqa: BLE001 — no PyYAML, or the manifest is not valid yaml
        pass
    # Regex fallback: PyYAML-absent is the modal adopter install (MP#73), and
    # this must not silently return "undeclared" on those boxes.
    name, peers = None, []
    m = re.search(r"(?ms)^mail:\s*$(.*?)(?=^\S|\Z)", text)
    if m:
        body = m.group(1)
        n = re.search(r"^\s+name:\s*[\"']?([^\"'\s#]+)", body, re.M)
        if n:
            name = n.group(1)
        inline = re.search(r"^\s+peers:\s*\[([^\]]*)\]", body, re.M)
        if inline:
            peers = [x.strip().strip("\"'") for x in inline.group(1).split(",") if x.strip()]
        else:
            blk = re.search(r"^\s+peers:\s*$((?:\s+-\s*.+$)+)", body, re.M)
            if blk:
                peers = [re.sub(r"^\s*-\s*", "", l).strip().strip("\"'")
                         for l in blk.group(1).splitlines() if l.strip()]
    return name, peers


def peer_name(peer_root):
    """A peer's declared mail name, read from ITS manifest. A read, never a write."""
    return mail_config(peer_root)[0]


def addressed_to(filename, sender, recipient):
    """True when `filename` is mail from `sender` to `recipient`.

    Matched as a literal `<sender>-<recipient>-` prefix followed by the date,
    case-insensitively. NOT parsed by position: `4SYNC-ARCH` contains a hyphen,
    and a positional split would read its second field as a recipient.
    """
    if not (sender and recipient):
        return False
    # THE DATE IS PART OF THE MATCH, and it is what disambiguates. A bare prefix
    # test cannot tell `4SYNC -> ARCH` from `4SYNC-ARCH -> COWORK`: both read as
    # "4SYNC-ARCH-..." at the front. Requiring the date to sit immediately after
    # the recipient resolves it, because a project name is not a date. Found by a
    # test asserting the hyphen case, which the first implementation failed.
    prefix = "%s-%s-" % (sender.lower(), recipient.lower())
    name = filename.lower()
    if not name.startswith(prefix):
        return False
    return re.match(r"^\d{4}-\d{2}-\d{2}(-|\.)", name[len(prefix):]) is not None


def _dir(root, name):
    d = os.path.join(root, name)
    return d if os.path.isdir(d) else None


def pull(root, apply=False, manifest_name=None):
    """Copy anything addressed to this instance out of each declared peer's outbox."""
    me, peers = mail_config(root, manifest_name)
    actions, notes = [], []
    if not me:
        return [], ["mail: this instance declares no `mail.name` — nothing to pull"]
    inbox = os.path.join(root, "inbox")
    for peer in peers:
        peer_root = peer if os.path.isabs(peer) else os.path.normpath(os.path.join(root, peer))
        their = peer_name(peer_root)
        out = _dir(peer_root, "outbox")
        if not their or not out:
            notes.append("  ? %s — no declared name or no outbox/, skipped" % peer)
            continue
        for fn in sorted(os.listdir(out)):
            if fn == ".gitkeep" or not addressed_to(fn, their, me):
                continue
            dest = os.path.join(inbox, fn)
            if os.path.exists(dest):
                notes.append("  = already in inbox/  %s" % fn)
                continue
            actions.append((os.path.join(out, fn), dest, their))
    if apply:
        if actions and not os.path.isdir(inbox):
            os.makedirs(inbox)
        for src, dest, _ in actions:
            shutil.copyfile(src, dest)
    return actions, notes


def sweep(root, apply=False, manifest_name=None):
    """Delete our copy of anything now present in the addressee's inbox.

    AN EMPTY OUTBOX IS THE DELIVERY RECEIPT. Deleting only on confirmed arrival
    is what makes that true; deleting on a timer would make an empty outbox mean
    nothing at all.
    """
    me, peers = mail_config(root, manifest_name)
    actions, notes = [], []
    if not me:
        return [], ["mail: this instance declares no `mail.name` — nothing to sweep"]
    out = _dir(root, "outbox")
    if not out:
        return [], ["mail: no outbox/ in this instance — nothing to sweep"]
    known = {}
    for peer in peers:
        peer_root = peer if os.path.isabs(peer) else os.path.normpath(os.path.join(root, peer))
        n = peer_name(peer_root)
        if n:
            known[n.lower()] = peer_root
    for fn in sorted(os.listdir(out)):
        if fn == ".gitkeep":
            continue
        for their_name, peer_root in known.items():
            if not addressed_to(fn, me, their_name):
                continue
            if os.path.exists(os.path.join(peer_root, "inbox", fn)):
                actions.append((os.path.join(out, fn), their_name))
            else:
                notes.append("  · undelivered   %s" % fn)
            break
        else:
            notes.append("  ? no declared peer named in %s" % fn)
    if apply:
        for path, _ in actions:
            os.remove(path)
    return actions, notes


def main():
    # Windows consoles default to cp1252; the em dash and tick/cross glyphs in the
    # output would arrive mangled or raise on print. Reconfigure early. Matches the
    # form already in meter.py, actuals.py, arch_build.py and wire_hooks.py — this
    # was the house pattern in four scripts and absent from five (MP#84).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Cross-project mail between ARCH instances.")
    ap.add_argument("command", choices=["pull", "sweep"])
    ap.add_argument("--dir", default=os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")),
        help="the instance to operate on (default: this one)")
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this is a dry run")
    args = ap.parse_args()

    if args.command == "pull":
        actions, notes = pull(args.dir, apply=args.apply)
        for _src, dest, frm in actions:
            print("  %-6s %s  (from %s)" % ("pull" if args.apply else "would",
                                            os.path.basename(dest), frm))
    else:
        actions, notes = sweep(args.dir, apply=args.apply)
        for path, to in actions:
            print("  %-6s %s  (delivered to %s)" % ("swept" if args.apply else "would",
                                                    os.path.basename(path), to))
    for n in notes:
        print(n)
    if not actions:
        print("mail: nothing to do")
    elif not args.apply:
        print("mode: dry-run — pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
