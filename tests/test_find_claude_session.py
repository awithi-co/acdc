import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "claude_home"
SCRIPT_DIR = (
    Path(__file__).parent.parent
    / "plugins"
    / "codex"
    / "skills"
    / "resume-claude-session"
    / "scripts"
)

sys.path.insert(0, str(SCRIPT_DIR))
import find_claude_session  # noqa: E402


ARCHIVED_UUID = "cccccccc-3333-4c33-c333-333333333333"
UNTITLED_UUID = "dddddddd-4444-4d44-d444-444444444444"
LIVE_UUID = "eeeeeeee-5555-4e55-e555-555555555555"


class FindClaudeSessionTests(unittest.TestCase):
    def collect(self, query, cwd_hint=""):
        return find_claude_session.collect_candidates(
            FIXTURES, query, cwd_hint, max_project_files=20
        )

    def test_finds_by_live_name(self):
        candidates = self.collect("live-name")
        session_ids = [c.session_id for c in candidates]
        self.assertIn(LIVE_UUID, session_ids)

    def test_finds_by_archived_title(self):
        candidates = self.collect("archived-name")
        session_ids = [c.session_id for c in candidates]
        self.assertIn(ARCHIVED_UUID, session_ids)

    def test_finds_by_full_uuid(self):
        candidates = self.collect(UNTITLED_UUID)
        self.assertEqual([c.session_id for c in candidates], [UNTITLED_UUID])

    def test_finds_by_uuid_prefix(self):
        candidates = self.collect("dddddddd")
        self.assertEqual([c.session_id for c in candidates], [UNTITLED_UUID])

    def test_returns_empty_for_unknown_name(self):
        self.assertEqual(self.collect("no-such-session"), [])

    def test_returns_empty_for_nonexistent_uuid(self):
        self.assertEqual(self.collect("ffffffff-ffff-4fff-ffff-ffffffffffff"), [])

    def test_finds_by_uppercase_uuid(self):
        candidates = self.collect(UNTITLED_UUID.upper())
        self.assertEqual([c.session_id for c in candidates], [UNTITLED_UUID])


if __name__ == "__main__":
    unittest.main()
