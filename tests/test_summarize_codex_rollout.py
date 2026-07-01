import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).parent.parent
    / "plugins"
    / "claude"
    / "skills"
    / "resume-codex-session"
    / "scripts"
)

sys.path.insert(0, str(SCRIPT_DIR))
import summarize_codex_rollout  # noqa: E402


OWN_UUID = "cccccccc-3333-7c33-c333-333333333333"
ANCESTOR_UUID = "dddddddd-4444-7d44-d444-444444444444"

FORK_TS = "2026-06-26T00:22:39.100Z"


def _row(ts, obj_type, payload):
    return json.dumps({"timestamp": ts, "type": obj_type, "payload": payload})


def _user(ts, message):
    return _row(ts, "event_msg", {"type": "user_message", "message": message})


def _agent(ts, message):
    return _row(ts, "event_msg", {"type": "agent_message", "message": message})


def forked_rollout_lines():
    """Forked rollout: replay burst (inherited history) then two live work segments 12h apart."""
    return [
        _row(
            FORK_TS,
            "session_meta",
            {
                "id": OWN_UUID,
                "session_id": OWN_UUID,
                "forked_from_id": ANCESTOR_UUID,
                "timestamp": "2026-06-26T00:22:38.766Z",
                "cwd": "/tmp/project",
            },
        ),
        _row(
            FORK_TS,
            "session_meta",
            {
                "id": ANCESTOR_UUID,
                "session_id": ANCESTOR_UUID,
                "timestamp": "2026-06-23T07:13:25.925Z",
                "cwd": "/tmp/project",
            },
        ),
        # replay burst: inherited rows share the fork instant
        _user("2026-06-26T00:22:39.101Z", "ancestor task request"),
        _agent("2026-06-26T00:22:39.102Z", "ancestor answer"),
        # live segment 1: two minutes after the fork (well under the gap threshold)
        _user("2026-06-26T00:24:39.000Z", "day1 task request"),
        _agent("2026-06-26T00:25:00.000Z", "day1 answer"),
        # live segment 2: 12h later
        _user("2026-06-26T12:25:00.000Z", "day2 task request"),
        _agent("2026-06-26T12:26:00.000Z", "day2 answer"),
    ]


def plain_rollout_lines():
    """Non-forked rollout: single meta, two segments 12h apart."""
    return [
        _row(
            "2026-06-23T07:13:26.000Z",
            "session_meta",
            {
                "id": ANCESTOR_UUID,
                "session_id": ANCESTOR_UUID,
                "timestamp": "2026-06-23T07:13:25.925Z",
                "cwd": "/tmp/project",
            },
        ),
        _user("2026-06-23T07:14:00.000Z", "morning task"),
        _agent("2026-06-23T07:15:00.000Z", "morning answer"),
        _user("2026-06-23T19:15:00.000Z", "evening task"),
        _agent("2026-06-23T19:16:00.000Z", "evening answer"),
    ]


class SegmentationTestCase(unittest.TestCase):
    def write_rollout(self, lines):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.addCleanup(Path(tmp.name).unlink)
        tmp.write("\n".join(lines) + "\n")
        tmp.close()
        return tmp.name

    def run_cli(self, *argv):
        buffer = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["summarize_codex_rollout.py", *argv]
        try:
            with redirect_stdout(buffer):
                summarize_codex_rollout.main()
        finally:
            sys.argv = old_argv
        return buffer.getvalue()


class SegmentsListingTests(SegmentationTestCase):
    def test_forked_rollout_reports_lineage_and_inherited_replay(self):
        path = self.write_rollout(forked_rollout_lines())
        output = self.run_cli(path, "--segments")
        self.assertIn(ANCESTOR_UUID, output)
        self.assertIn("forked_from", output)
        self.assertIn("inherited", output)

    def test_forked_rollout_splits_live_segments_after_replay(self):
        path = self.write_rollout(forked_rollout_lines())
        output = self.run_cli(path, "--segments")
        self.assertIn("day1 task request", output)
        self.assertIn("day2 task request", output)
        day1_line = next(l for l in output.splitlines() if "day1 task request" in l)
        day2_line = next(l for l in output.splitlines() if "day2 task request" in l)
        self.assertNotEqual(day1_line, day2_line)

    def test_plain_rollout_has_no_inherited_label(self):
        path = self.write_rollout(plain_rollout_lines())
        output = self.run_cli(path, "--segments")
        self.assertNotIn("inherited", output)
        self.assertNotIn("forked_from", output)
        self.assertIn("morning task", output)
        self.assertIn("evening task", output)


class SegmentSelectionTests(SegmentationTestCase):
    def test_segment_last_prints_only_final_live_segment(self):
        path = self.write_rollout(forked_rollout_lines())
        output = self.run_cli(path, "--segment", "last")
        self.assertIn("day2 task request", output)
        self.assertNotIn("day1 task request", output)
        self.assertNotIn("ancestor task request", output)

    def test_segment_index_prints_that_segment(self):
        path = self.write_rollout(forked_rollout_lines())
        output = self.run_cli(path, "--segment", "1")
        self.assertIn("day1 task request", output)
        self.assertNotIn("day2 task request", output)
        self.assertNotIn("ancestor task request", output)


if __name__ == "__main__":
    unittest.main()
