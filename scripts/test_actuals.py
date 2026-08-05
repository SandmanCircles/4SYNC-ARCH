#!/usr/bin/env python3
"""
Stdlib unittest suite for actuals.py — measured session cost from transcripts.

Follows test_meter.py's pattern: no network, no third-party deps, imports the
module from the same scripts/ directory. Run either way:

  python -m unittest test_actuals          # from the scripts/ dir
  python scripts/test_actuals.py           # from the repo root

THE TEST THAT MATTERS MOST is TestPrivacy. actuals.py's entire justification
for being safe to run, safe to keep, and safe to ship is that it reads usage
integers and never message content. That is a CLAIM in a docstring until
something checks it, and it is exactly the kind of claim that rots silently
when a later change adds "just one more useful field." So the fixtures plant
a distinctive secret in every place real content lives — user text, assistant
text, and tool results — and the suite asserts it appears in no output.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actuals  # noqa: E402


# Planted in every content-bearing field the fixtures build. If this string
# ever reaches an output, a privacy test fails and names the field.
SECRET = "CLIENT-CONFIDENTIAL-DO-NOT-EMIT-8842"


def usage(fresh=2, cc=0, cr=1000, out=100):
    return {
        "input_tokens": fresh,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "output_tokens": out,
    }


def assistant_record(u, ts="2026-08-01T10:00:00Z", cwd="C:\\proj",
                     branch="main", version="2.0.1"):
    """An assistant turn, shaped like a real record: usage nested under
    `message`, metadata at the top level, and content present alongside it."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "cwd": cwd,
        "gitBranch": branch,
        "version": version,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": "reply about " + SECRET}],
            "usage": u,
        },
    }


def user_record(ts="2026-08-01T09:59:00Z", cwd="C:\\proj"):
    """A user turn. Carries content and NO usage — must be skipped entirely."""
    return {
        "type": "user",
        "timestamp": ts,
        "cwd": cwd,
        "message": {"role": "user", "content": "please do " + SECRET},
    }


def tool_result_record(ts="2026-08-01T09:59:30Z"):
    """A tool result — in real transcripts this is where file contents and
    command output land, so it is the highest-risk record for a leak."""
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "file body: " + SECRET}]},
    }


def write_transcript(path, usages, cwd="C:\\proj", branch="main",
                     interleave_content=True, trailing_garbage=False):
    """Build a transcript file. `usages` is a list of usage dicts, one per
    assistant turn, in order."""
    with open(path, "w", encoding="utf-8") as fh:
        for i, u in enumerate(usages):
            if interleave_content:
                fh.write(json.dumps(user_record()) + "\n")
                fh.write(json.dumps(tool_result_record()) + "\n")
            ts = "2026-08-01T10:%02d:00Z" % min(i, 59)
            fh.write(json.dumps(assistant_record(u, ts=ts, cwd=cwd,
                                                 branch=branch)) + "\n")
        if trailing_garbage:
            # A half-written final line: the newest transcript is the session
            # CURRENTLY RUNNING and can be caught mid-write.
            fh.write('{"type":"assistant","message":{"usage":{"input_')


class TestIterUsage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "s.jsonl")

    def test_yields_only_assistant_usage(self):
        write_transcript(self.path, [usage(), usage(), usage()])
        self.assertEqual(len(list(actuals.iter_usage(self.path))), 3)

    def test_skips_malformed_lines(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json at all\n")
            fh.write("\n")
            fh.write("[1,2,3]\n")                       # valid JSON, not a dict
            fh.write(json.dumps({"type": "system"}) + "\n")   # no message
            fh.write(json.dumps({"message": "a string"}) + "\n")  # message not a dict
            fh.write(json.dumps(assistant_record(usage())) + "\n")
        self.assertEqual(len(list(actuals.iter_usage(self.path))), 1)

    def test_tolerates_half_written_final_line(self):
        """A partial last line must not lose the complete records before it."""
        write_transcript(self.path, [usage(), usage()], trailing_garbage=True)
        self.assertEqual(len(list(actuals.iter_usage(self.path))), 2)

    def test_missing_file_is_empty_not_fatal(self):
        self.assertEqual(list(actuals.iter_usage(os.path.join(self.dir, "nope.jsonl"))), [])

    def test_metadata_carried(self):
        write_transcript(self.path, [usage()], cwd="C:\\somewhere", branch="feat/x")
        _u, meta = list(actuals.iter_usage(self.path))[0]
        self.assertEqual(meta["cwd"], "C:\\somewhere")
        self.assertEqual(meta["branch"], "feat/x")


class TestSummarise(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "abcdef1234.jsonl")

    def test_none_when_no_usage_records(self):
        """An aborted session, or a format this version cannot read. Both are
        'nothing to say' — not an error, and not a zero row."""
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(user_record()) + "\n")
        self.assertIsNone(actuals.summarise(self.path))

    def test_sums_and_turns(self):
        write_transcript(self.path, [usage(2, 100, 1000, 50),
                                     usage(3, 200, 2000, 60)])
        r = actuals.summarise(self.path)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["input_fresh"], 5)
        self.assertEqual(r["cache_creation"], 300)
        self.assertEqual(r["cache_read_raw"], 3000)
        self.assertEqual(r["output"], 110)

    def test_peak_turn_is_max_not_sum(self):
        """peak_turn is the largest SINGLE turn, not the session total — the
        distinction the field was renamed to make unambiguous."""
        write_transcript(self.path, [usage(0, 0, 500), usage(0, 0, 9000),
                                     usage(0, 0, 700)])
        self.assertEqual(actuals.summarise(self.path)["peak_turn"], 9000)

    def test_weighted_estimate_discounts_cache_reads(self):
        write_transcript(self.path, [usage(100, 200, 10000, 0)])
        r = actuals.summarise(self.path)
        self.assertEqual(r["input_weighted_est"],
                         int(100 + 200 + 10000 * actuals.CACHE_READ_WEIGHT))

    def test_raw_and_weighted_both_present(self):
        """Both must ship. A raw cache_read sum overstates cost badly if read
        as tokens-you-paid-for; the weighted figure alone hides the volume."""
        write_transcript(self.path, [usage()])
        r = actuals.summarise(self.path)
        self.assertIn("cache_read_raw", r)
        self.assertIn("input_weighted_est", r)

    def test_kind_main_vs_agent(self):
        write_transcript(self.path, [usage()])
        self.assertEqual(actuals.summarise(self.path)["kind"], "main")
        agent = os.path.join(self.dir, "agent-xyz123.jsonl")
        write_transcript(agent, [usage()])
        self.assertEqual(actuals.summarise(agent)["kind"], "agent")

    def test_timestamps_span_first_to_last(self):
        write_transcript(self.path, [usage(), usage(), usage()])
        r = actuals.summarise(self.path)
        self.assertLess(r["first_ts"], r["last_ts"])


class TestPrefixTrace(unittest.TestCase):
    """The trace exists because session TOTALS cannot answer a before/after —
    they are dominated by how long the session ran. Sampling the same turn
    index on both sides is what controls for that."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "s.jsonl")

    def test_samples_at_declared_turn_indices(self):
        # 45 turns, each with a distinct, identifiable prefix size.
        write_transcript(self.path, [usage(0, 0, 1000 * n) for n in range(1, 46)])
        trace = actuals.summarise(self.path)["prefix_trace"]
        self.assertEqual(sorted(int(k) for k in trace), [1, 5, 10, 20, 40])
        self.assertEqual(trace["1"], 1000)
        self.assertEqual(trace["10"], 10000)
        self.assertEqual(trace["40"], 40000)

    def test_short_session_omits_later_samples(self):
        """A 7-turn session has no turn 10. The key must be ABSENT rather than
        zero — a zero would average into a comparison as if it were measured."""
        write_transcript(self.path, [usage() for _ in range(7)])
        trace = actuals.summarise(self.path)["prefix_trace"]
        self.assertIn("5", trace)
        self.assertNotIn("10", trace)
        self.assertNotIn("40", trace)

    def test_trace_counts_assistant_turns_only(self):
        """Interleaved user/tool records must not advance the turn index, or
        the same turn number means different things in different sessions."""
        write_transcript(self.path, [usage(0, 0, 111), usage(0, 0, 222),
                                     usage(0, 0, 333), usage(0, 0, 444),
                                     usage(0, 0, 555)])
        self.assertEqual(actuals.summarise(self.path)["prefix_trace"]["5"], 555)


class TestProjectMatching(unittest.TestCase):
    """Matching is on the `cwd` recorded INSIDE transcripts, not on Claude
    Code's directory-name encoding, which is undocumented and version-
    dependent."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "projects")
        for name, cwd in (("proj-a", os.path.join(self.dir, "A")),
                          ("proj-b", os.path.join(self.dir, "B"))):
            d = os.path.join(self.root, name)
            os.makedirs(d)
            write_transcript(os.path.join(d, "s1.jsonl"), [usage()], cwd=cwd)
        self._real = actuals.transcript_root
        actuals.transcript_root = lambda: self.root

    def tearDown(self):
        actuals.transcript_root = self._real

    def test_matches_only_the_requested_instance(self):
        hits = actuals.project_dirs(False, os.path.join(self.dir, "A"))
        self.assertEqual([os.path.basename(h) for h in hits], ["proj-a"])

    def test_all_returns_every_project(self):
        hits = actuals.project_dirs(True, os.path.join(self.dir, "A"))
        self.assertEqual(sorted(os.path.basename(h) for h in hits),
                         ["proj-a", "proj-b"])

    def test_subdirectory_of_instance_still_matches(self):
        """A worktree or nested repo runs with a cwd BELOW the instance root
        and is still that instance's session."""
        d = os.path.join(self.root, "proj-a-worktree")
        os.makedirs(d)
        write_transcript(os.path.join(d, "s.jsonl"), [usage()],
                         cwd=os.path.join(self.dir, "A", "nested", "deep"))
        hits = actuals.project_dirs(False, os.path.join(self.dir, "A"))
        self.assertEqual(len(hits), 2)

    def test_unrelated_instance_excluded(self):
        hits = actuals.project_dirs(False, os.path.join(self.dir, "B"))
        self.assertEqual([os.path.basename(h) for h in hits], ["proj-b"])

    def test_missing_transcript_root_is_empty_not_fatal(self):
        actuals.transcript_root = lambda: os.path.join(self.dir, "does-not-exist")
        self.assertEqual(actuals.project_dirs(True, self.dir), [])


class TestAppendSeries(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def rows(self, *ids):
        return [{"project": "p", "session": i, "turns": 1} for i in ids]

    def test_creates_and_appends(self):
        n, path = actuals.append_series(self.dir, self.rows("a", "b"))
        self.assertEqual(n, 2)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(len(open(path, encoding="utf-8").read().splitlines()), 2)

    def test_rerun_is_idempotent(self):
        """Re-running the extractor must not duplicate sessions. The whole
        point is that it can be run repeatedly, including at every close."""
        actuals.append_series(self.dir, self.rows("a", "b"))
        n, path = actuals.append_series(self.dir, self.rows("a", "b"))
        self.assertEqual(n, 0)
        self.assertEqual(len(open(path, encoding="utf-8").read().splitlines()), 2)

    def test_only_new_sessions_added(self):
        actuals.append_series(self.dir, self.rows("a"))
        n, path = actuals.append_series(self.dir, self.rows("a", "b", "c"))
        self.assertEqual(n, 2)
        self.assertEqual(len(open(path, encoding="utf-8").read().splitlines()), 3)

    def test_same_session_id_in_two_projects_both_kept(self):
        """Dedupe is keyed on (project, session), not session alone — two
        instances can hold transcripts with the same short id."""
        actuals.append_series(self.dir, [{"project": "p1", "session": "dup"}])
        n, _ = actuals.append_series(self.dir, [{"project": "p2", "session": "dup"}])
        self.assertEqual(n, 1)

    def test_existing_rows_preserved_verbatim(self):
        actuals.append_series(self.dir, [{"project": "p", "session": "a", "keep": 42}])
        actuals.append_series(self.dir, self.rows("b"))
        path = os.path.join(self.dir, actuals.SERIES_REL)
        first = json.loads(open(path, encoding="utf-8").read().splitlines()[0])
        self.assertEqual(first["keep"], 42)

    def test_no_tmp_file_left_behind(self):
        actuals.append_series(self.dir, self.rows("a"))
        path = os.path.join(self.dir, actuals.SERIES_REL)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_every_line_is_valid_json(self):
        actuals.append_series(self.dir, self.rows("a", "b", "c"))
        path = os.path.join(self.dir, actuals.SERIES_REL)
        for line in open(path, encoding="utf-8").read().splitlines():
            json.loads(line)


class TestRender(unittest.TestCase):
    def test_empty_says_so_without_failing(self):
        """No transcripts is not an error — the format may have changed, or
        they may simply have been pruned."""
        out = actuals.render([])
        self.assertIn("No usable transcripts found", out)

    def test_labels_measured_vs_estimated(self):
        """meter.py's numbers are estimates and these are measurements. The
        two must never be silently merged, so the header says which is which."""
        out = actuals.render([])
        full = actuals.render([{
            "session": "a", "kind": "main", "turns": 3, "peak_turn": 100,
            "cache_creation": 1, "cache_read_raw": 2, "output": 3,
            "input_weighted_est": 4, "prefix_trace": {}}])
        self.assertIn("MEASURED", full)
        self.assertIn("meter.py", full + out)

    def test_peak_not_called_a_context_window(self):
        """Renamed deliberately: it is the largest single-turn input, which is
        an upper bound on resident context, not a proven window size."""
        full = actuals.render([{
            "session": "a", "kind": "main", "turns": 3, "peak_turn": 100,
            "cache_creation": 1, "cache_read_raw": 2, "output": 3,
            "input_weighted_est": 4, "prefix_trace": {}}])
        self.assertIn("peak turn", full)
        self.assertNotIn("peak context", full)


class TestPrivacy(unittest.TestCase):
    """READ-ONLY, USAGE FIELDS ONLY. Transcripts hold the full verbatim
    conversation including tool results, which is where file contents and
    command output land. The fixtures plant SECRET in user text, assistant
    text, and a tool result; none of it may reach any output."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "projects", "proj")
        os.makedirs(self.root)
        write_transcript(os.path.join(self.root, "s.jsonl"),
                         [usage(), usage()], cwd=self.dir)
        self._real = actuals.transcript_root
        actuals.transcript_root = lambda: os.path.dirname(self.root)

    def tearDown(self):
        actuals.transcript_root = self._real

    def test_secret_present_in_fixture(self):
        """Guards the guard: if the fixture stopped containing SECRET, every
        other test in this class would pass vacuously."""
        body = open(os.path.join(self.root, "s.jsonl"), encoding="utf-8").read()
        self.assertIn(SECRET, body)
        self.assertGreaterEqual(body.count(SECRET), 3)

    def test_summarise_row_carries_no_content(self):
        row = actuals.summarise(os.path.join(self.root, "s.jsonl"))
        self.assertNotIn(SECRET, json.dumps(row))

    def test_rendered_table_carries_no_content(self):
        rows = actuals.collect(True, self.dir)
        self.assertNotIn(SECRET, actuals.render(rows))

    def test_series_file_carries_no_content(self):
        rows = actuals.collect(True, self.dir)
        _n, path = actuals.append_series(self.dir, rows)
        self.assertNotIn(SECRET, open(path, encoding="utf-8").read())

    def test_row_fields_are_scalars_only(self):
        """A defence against the likely future regression: someone adds a
        field that happens to carry content. Every value must be a scalar or
        a dict of ints (the trace) — never free text from the transcript."""
        row = actuals.summarise(os.path.join(self.root, "s.jsonl"))
        allowed_text = {"session", "kind", "cwd", "branch", "version",
                        "first_ts", "last_ts"}
        for k, v in row.items():
            if k in allowed_text:
                continue
            if k == "prefix_trace":
                self.assertTrue(all(isinstance(x, int) for x in v.values()))
                continue
            self.assertIsInstance(v, int, "field %r is not an integer" % k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
