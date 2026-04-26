import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "codex_home"
SCRIPT_DIR = (
    Path(__file__).parent.parent
    / "plugins"
    / "claude"
    / "skills"
    / "resume-codex-session"
    / "scripts"
)

sys.path.insert(0, str(SCRIPT_DIR))
import find_codex_session  # noqa: E402


INDEXED_UUID = "aaaaaaaa-1111-7a11-a111-111111111111"
UNINDEXED_UUID = "bbbbbbbb-2222-7b22-b222-222222222222"


class FindCodexSessionTests(unittest.TestCase):
    def collect(self, query, cwd_hint=""):
        return find_codex_session.collect_candidates(FIXTURES, query, cwd_hint)

    def test_finds_by_exact_name(self):
        candidates = self.collect("identity-fixture")
        self.assertEqual([c.session_id for c in candidates], [INDEXED_UUID])

    def test_finds_by_full_uuid_indexed(self):
        candidates = self.collect(INDEXED_UUID)
        self.assertEqual([c.session_id for c in candidates], [INDEXED_UUID])

    def test_finds_by_full_uuid_index_miss(self):
        candidates = self.collect(UNINDEXED_UUID)
        self.assertEqual([c.session_id for c in candidates], [UNINDEXED_UUID])

    def test_finds_by_uuid_prefix_index_miss(self):
        candidates = self.collect("bbbbbbbb")
        self.assertEqual([c.session_id for c in candidates], [UNINDEXED_UUID])

    def test_returns_empty_for_unknown_name(self):
        self.assertEqual(self.collect("no-such-session"), [])

    def test_returns_empty_for_nonexistent_uuid(self):
        self.assertEqual(self.collect("ffffffff-ffff-7fff-ffff-ffffffffffff"), [])

    def test_finds_by_uppercase_uuid(self):
        candidates = self.collect(UNINDEXED_UUID.upper())
        self.assertEqual([c.session_id for c in candidates], [UNINDEXED_UUID])


if __name__ == "__main__":
    unittest.main()
