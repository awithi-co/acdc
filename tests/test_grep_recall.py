import sys
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).parent.parent
    / "plugins" / "claude" / "skills" / "recall-context" / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import grep_recall  # noqa: E402


class ParseEventTests(unittest.TestCase):
    def test_parses_user_event(self):
        line = '{"type":"user","timestamp":"2026-04-26T10:00:00.000Z","message":{"role":"user","content":"hi"}}'
        ev = grep_recall.parse_event(line)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.type, "user")
        self.assertEqual(ev.timestamp, "2026-04-26T10:00:00.000Z")

    # spec §8.2: test_invalid_jsonl_lines_skipped (unit-level)
    def test_skips_invalid_json(self):
        self.assertIsNone(grep_recall.parse_event("not json"))

    def test_skips_blank_line(self):
        self.assertIsNone(grep_recall.parse_event(""))
        self.assertIsNone(grep_recall.parse_event("   "))


class ExtractTextTests(unittest.TestCase):
    def _ev(self, raw):
        return grep_recall.Event(type=raw.get("type", ""), timestamp="", raw=raw)

    def test_extracts_user_string_content(self):
        ev = self._ev({"type": "user", "message": {"content": "hello world"}})
        self.assertEqual(grep_recall.extract_text(ev, include_tools=False), "hello world")

    def test_extracts_assistant_text_blocks(self):
        ev = self._ev({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "block one"},
                {"type": "text", "text": "block two"},
            ]},
        })
        out = grep_recall.extract_text(ev, include_tools=False)
        self.assertIn("block one", out)
        self.assertIn("block two", out)

    # spec §8.2: test_event_type_filtering
    def test_excludes_system_event_by_default(self):
        ev = self._ev({"type": "system", "content": "system text"})
        self.assertEqual(grep_recall.extract_text(ev, include_tools=False), "")

    # spec §8.2: test_event_type_filtering
    def test_excludes_attachment_event(self):
        ev = self._ev({"type": "attachment", "content": "att text"})
        self.assertEqual(grep_recall.extract_text(ev, include_tools=False), "")

    # spec §8.2: test_include_tools_flag
    def test_excludes_tool_use_by_default(self):
        ev = self._ev({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
            ]},
        })
        self.assertEqual(grep_recall.extract_text(ev, include_tools=False), "")

    # spec §8.2: test_include_tools_flag
    def test_includes_tool_use_when_flag_set(self):
        ev = self._ev({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la /tmp"}}
            ]},
        })
        out = grep_recall.extract_text(ev, include_tools=True)
        self.assertIn("ls -la /tmp", out)

    def test_extracts_codex_response_item_user(self):
        ev = self._ev({
            "type": "response_item",
            "payload": {"type": "message", "role": "user",
                        "content": [{"type": "text", "text": "codex hi"}]},
        })
        self.assertIn("codex hi", grep_recall.extract_text(ev, include_tools=False))

    def test_extracts_codex_function_call_with_flag(self):
        ev = self._ev({
            "type": "response_item",
            "payload": {"type": "function_call", "name": "shell",
                        "arguments": '{"command":["ls"]}'},
        })
        self.assertEqual(grep_recall.extract_text(ev, include_tools=False), "")
        self.assertIn("ls", grep_recall.extract_text(ev, include_tools=True))


import re


class FindMatchesTests(unittest.TestCase):
    def _evs(self, *types_and_texts):
        out = []
        for t, txt in types_and_texts:
            out.append(grep_recall.Event(
                type=t, timestamp="",
                raw={"type": t, "message": {"content": txt}}
                if t in ("user", "assistant") else {"type": t, "content": txt},
            ))
        return out

    # spec §8.2: test_query_matches_text_content
    def test_finds_substring_in_user_event(self):
        events = self._evs(("user", "we will use ACDC"), ("assistant", "okay"))
        pattern = re.compile("acdc", re.IGNORECASE)
        self.assertEqual(grep_recall.find_matches(events, pattern, include_tools=False), [0])

    # spec §8.2: test_query_excludes_uuid_collisions
    def test_excludes_uuid_collision(self):
        # The UUID lives in raw metadata, not in the searchable text content.
        events = [grep_recall.Event(
            type="user", timestamp="",
            raw={"type": "user",
                 "sessionId": "eeeeeeee-acdc-7e55-e555-555555555555",
                 "message": {"content": "hello world"}},
        )]
        pattern = re.compile("acdc", re.IGNORECASE)
        self.assertEqual(grep_recall.find_matches(events, pattern, include_tools=False), [])

    def test_finds_in_assistant_text_blocks(self):
        events = [grep_recall.Event(
            type="assistant", timestamp="",
            raw={"type": "assistant",
                 "message": {"content": [{"type": "text", "text": "ACDC channel"}]}},
        )]
        pattern = re.compile("channel", re.IGNORECASE)
        self.assertEqual(grep_recall.find_matches(events, pattern, include_tools=False), [0])

    # spec §8.2: test_event_type_filtering (search-level)
    def test_skips_filtered_event_types(self):
        events = self._evs(("system", "ACDC system text"), ("attachment", "ACDC attached"))
        pattern = re.compile("acdc", re.IGNORECASE)
        self.assertEqual(grep_recall.find_matches(events, pattern, include_tools=False), [])


class MergeSegmentsTests(unittest.TestCase):
    def _events_n(self, n):
        return [grep_recall.Event(type="user", timestamp=f"t{i}",
                                   raw={"type": "user", "message": {"content": f"e{i}"}})
                for i in range(n)]

    # spec §8.2: test_window_size
    def test_window_size_3(self):
        events = self._events_n(20)
        hits = [10]
        segs = grep_recall.merge_into_segments(events, hits, window=3)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].lo, 7)
        self.assertEqual(segs[0].hi, 13)

    # spec §8.2: test_window_size (boundary case)
    def test_window_clipped_at_start(self):
        events = self._events_n(20)
        hits = [1]
        segs = grep_recall.merge_into_segments(events, hits, window=3)
        self.assertEqual(segs[0].lo, 0)
        self.assertEqual(segs[0].hi, 4)

    # spec §8.2: test_window_size (boundary case)
    def test_window_clipped_at_end(self):
        events = self._events_n(10)
        hits = [9]
        segs = grep_recall.merge_into_segments(events, hits, window=3)
        self.assertEqual(segs[0].lo, 6)
        self.assertEqual(segs[0].hi, 9)

    # spec §8.2: test_adjacent_segments_merge
    def test_adjacent_hits_merge(self):
        events = self._events_n(30)
        hits = [10, 13]
        segs = grep_recall.merge_into_segments(events, hits, window=3)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].lo, 7)
        self.assertEqual(segs[0].hi, 16)
        self.assertEqual(segs[0].hits, [10, 13])

    # spec §8.2: test_adjacent_segments_merge (negative case)
    def test_distant_hits_separate(self):
        events = self._events_n(30)
        hits = [5, 25]
        segs = grep_recall.merge_into_segments(events, hits, window=3)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].hits, [5])
        self.assertEqual(segs[1].hits, [25])

    # spec §8.2: test_max_segments_truncation
    def test_max_segments_truncates(self):
        events = self._events_n(100)
        hits = [10, 30, 50, 70, 90]
        segs = grep_recall.merge_into_segments(events, hits, window=3, max_segments=3)
        self.assertEqual(len(segs), 3)


class RenderTests(unittest.TestCase):
    def _setup(self):
        events = [
            grep_recall.Event(type="user", timestamp="t0",
                              raw={"type": "user", "message": {"content": "first"}}),
            grep_recall.Event(type="assistant", timestamp="t1",
                              raw={"type": "assistant",
                                   "message": {"content": [{"type": "text", "text": "ACDC mention"}]}}),
            grep_recall.Event(type="user", timestamp="t2",
                              raw={"type": "user", "message": {"content": "third"}}),
        ]
        seg = grep_recall.Segment(lo=0, hi=2, hits=[1])
        return events, [seg]

    def test_text_render_marks_hits_with_star(self):
        events, segs = self._setup()
        rendered = grep_recall.render_text(events, segs, query="acdc",
                                            transcript_label="t.jsonl")
        self.assertIn("1 matches for 'acdc'", rendered)
        self.assertIn("★", rendered)

    # spec §8.2: test_json_output_format
    def test_json_render_is_valid(self):
        events, segs = self._setup()
        out = grep_recall.render_json(events, segs, query="acdc",
                                       transcript_label="t.jsonl")
        import json as _json
        parsed = _json.loads(out)
        self.assertEqual(parsed["query"], "acdc")
        self.assertEqual(len(parsed["segments"]), 1)
        self.assertEqual(parsed["segments"][0]["hits"], [1])


import datetime as dt

FIXTURES_CLAUDE = Path(__file__).parent / "fixtures" / "claude_home"
FIXTURE_ACDC_PATH = (FIXTURES_CLAUDE / "projects" / "-tmp-fixture"
                     / "eeeeeeee-acdc-7e55-e555-555555555555.jsonl")
FIXTURE_EMPTY_PATH = (FIXTURES_CLAUDE / "projects" / "-tmp-fixture"
                      / "ffffffff-6666-7f66-f666-666666666666.jsonl")


class SelectTranscriptsClaudeTests(unittest.TestCase):
    # spec §8.2: test_scope_current
    def test_current_returns_only_passed_path(self):
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="current",
                                     current_path=FIXTURE_ACDC_PATH),
            agent="claude",
            home=FIXTURES_CLAUDE,
        )
        self.assertEqual(result, [FIXTURE_ACDC_PATH])

    # spec §8.2: test_scope_days
    def test_days_filters_by_mtime(self):
        anchor = dt.datetime(2026, 4, 26, 12, 0, 0)
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="days", days=1, current_path=FIXTURE_ACDC_PATH,
                                     now=anchor),
            agent="claude",
            home=FIXTURES_CLAUDE,
        )
        self.assertIn(FIXTURE_ACDC_PATH, result)
        self.assertNotIn(FIXTURE_EMPTY_PATH, result)

    # spec §8.2: test_scope_days
    def test_days_7_includes_both(self):
        anchor = dt.datetime(2026, 4, 26, 12, 0, 0)
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="days", days=7, current_path=FIXTURE_ACDC_PATH,
                                     now=anchor),
            agent="claude",
            home=FIXTURES_CLAUDE,
        )
        self.assertIn(FIXTURE_ACDC_PATH, result)
        self.assertIn(FIXTURE_EMPTY_PATH, result)

    # spec §8.2: test_scope_since
    def test_since_date_filters(self):
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="since", since=dt.date(2026, 4, 25),
                                     current_path=FIXTURE_ACDC_PATH),
            agent="claude",
            home=FIXTURES_CLAUDE,
        )
        self.assertIn(FIXTURE_ACDC_PATH, result)
        self.assertNotIn(FIXTURE_EMPTY_PATH, result)


FIXTURES_CODEX = Path(__file__).parent / "fixtures" / "codex_home"
FIXTURE_CODEX_ACDC = (FIXTURES_CODEX / "sessions" / "2026" / "04" / "26"
                      / "rollout-2026-04-26T10-00-00-eeeeeeee-acdc-7e55-e555-555555555555.jsonl")
FIXTURE_CODEX_EMPTY = (FIXTURES_CODEX / "sessions" / "2026" / "04" / "20"
                       / "rollout-2026-04-20T12-00-00-ffffffff-6666-7f66-f666-666666666666.jsonl")


class SelectTranscriptsCodexTests(unittest.TestCase):
    # spec §8.2: test_scope_days (Codex)
    def test_codex_days_1(self):
        anchor = dt.datetime(2026, 4, 26, 12, 0, 0)
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="days", days=1, now=anchor),
            agent="codex",
            home=FIXTURES_CODEX,
        )
        self.assertIn(FIXTURE_CODEX_ACDC, result)
        self.assertNotIn(FIXTURE_CODEX_EMPTY, result)

    # spec §8.2: test_scope_days (Codex)
    def test_codex_days_30(self):
        anchor = dt.datetime(2026, 4, 26, 12, 0, 0)
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="days", days=30, now=anchor),
            agent="codex",
            home=FIXTURES_CODEX,
        )
        self.assertIn(FIXTURE_CODEX_ACDC, result)
        self.assertIn(FIXTURE_CODEX_EMPTY, result)

    def test_codex_cwd_hint_silently_ignored(self):
        # Codex partitions by date, not cwd. cwd_hint must not crash or filter.
        anchor = dt.datetime(2026, 4, 26, 12, 0, 0)
        result = grep_recall.select_transcripts(
            scope=grep_recall.Scope(kind="days", days=30, now=anchor),
            agent="codex",
            home=FIXTURES_CODEX,
            cwd_hint="/some/path",
        )
        self.assertIn(FIXTURE_CODEX_ACDC, result)
        self.assertIn(FIXTURE_CODEX_EMPTY, result)


import json
import subprocess


SCRIPT_PATH = SCRIPT_DIR / "grep_recall.py"


class CliIntegrationTests(unittest.TestCase):
    def _run(self, *args):
        proc = subprocess.run(
            ["python3", str(SCRIPT_PATH), *args],
            capture_output=True, text=True,
        )
        return proc

    def test_help_works(self):
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--query", proc.stdout)

    def test_finds_matches_in_current_transcript(self):
        proc = self._run(
            "--query", "ACDC",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("matches for 'ACDC'", proc.stdout)
        self.assertIn("★", proc.stdout)

    # spec §8.2: test_no_matches
    def test_no_matches_exit_zero(self):
        proc = self._run(
            "--query", "no-such-string-anywhere",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no matches", proc.stdout)

    # spec §8.2: test_invalid_jsonl_lines_skipped
    def test_invalid_jsonl_lines_skipped(self):
        proc = self._run(
            "--query", "hello",
            "--current-transcript", str(FIXTURE_EMPTY_PATH),
            "--current",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("matches for 'hello'", proc.stdout)

    # spec §8.2: test_json_output_format (integration)
    def test_json_output_parses(self):
        proc = self._run(
            "--query", "ACDC",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
            "--json",
        )
        self.assertEqual(proc.returncode, 0)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed["query"], "ACDC")
        self.assertGreaterEqual(len(parsed["transcripts"]), 1)
        self.assertGreaterEqual(len(parsed["transcripts"][0]["segments"]), 1)

    def test_uuid_collision_excluded_end_to_end(self):
        proc = self._run(
            "--query", "acdc",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
            "--json",
        )
        parsed = json.loads(proc.stdout)
        for tr in parsed["transcripts"]:
            for seg in tr["segments"]:
                for ev in seg["events"]:
                    if ev["is_hit"]:
                        self.assertIn(ev["type"], ("user", "assistant"))

    def test_include_tools_flag_finds_command(self):
        proc = self._run(
            "--query", "mv agents-bridge",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
            "--include-tools",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("mv agents-bridge", proc.stdout)

    # spec §8.2: test_max_segments_truncation (integration)
    def test_max_segments_truncates(self):
        proc = self._run(
            "--query", "ACDC",
            "--current-transcript", str(FIXTURE_ACDC_PATH),
            "--current",
            "--window", "0",
            "--max-segments", "1",
            "--json",
        )
        parsed = json.loads(proc.stdout)
        self.assertEqual(len(parsed["transcripts"][0]["segments"]), 1)


if __name__ == "__main__":
    unittest.main()
