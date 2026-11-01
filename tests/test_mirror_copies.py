"""The plugins install independently, so they ship copies that must stay identical."""

from pathlib import Path
import unittest

PLUGINS = Path(__file__).parents[1] / "plugins"


def shared_files(agent):
    # Handoff skills differ in name between plugins, so scripts match by basename.
    root = PLUGINS / agent
    paths = [p for p in root.glob("mcp/*") if p.is_file()]
    paths += [p for p in root.glob("skills/*/scripts/**/*") if p.is_file()]
    return {p.name: p for p in paths if p.suffix != ".pyc"}


class MirrorCopyTests(unittest.TestCase):
    def test_plugins_ship_identical_copies(self):
        claude, codex = shared_files("claude"), shared_files("codex")
        self.assertTrue(claude)
        self.assertEqual(sorted(claude), sorted(codex))
        for key, path in claude.items():
            with self.subTest(file=key):
                self.assertEqual(path.read_bytes(), codex[key].read_bytes())


if __name__ == "__main__":
    unittest.main()
