"""The two plugins ship copies, not symlinks. Copies must stay byte-identical.

Plugin installation copies the directory tree, so `plugins/claude` and
`plugins/codex` each carry their own copy of every shared script. Skill names
differ for the handoff pair (resume-codex-session vs resume-claude-session), so
copies are matched by script basename rather than by skill path.
"""

import unittest
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGINS = ("claude", "codex")


def scripts_by_basename(agent):
    found = defaultdict(list)
    root = REPO_ROOT / "plugins" / agent / "skills"
    for path in sorted(root.glob("*/scripts/*.py")):
        found[path.name].append(path)
    return found


class MirrorCopyTests(unittest.TestCase):
    def setUp(self):
        self.by_agent = {agent: scripts_by_basename(agent) for agent in PLUGINS}

    def test_no_duplicate_basename_within_one_plugin(self):
        for agent, found in self.by_agent.items():
            for name, paths in found.items():
                self.assertEqual(
                    len(paths), 1,
                    f"{name} appears {len(paths)} times inside plugins/{agent}: {paths}",
                )

    def test_shared_scripts_are_byte_identical(self):
        claude, codex = (self.by_agent[agent] for agent in PLUGINS)
        shared = sorted(set(claude) & set(codex))
        self.assertTrue(shared, "expected at least one shared script basename")

        for name in shared:
            left, right = claude[name][0], codex[name][0]
            with self.subTest(script=name):
                self.assertEqual(
                    left.read_bytes(), right.read_bytes(),
                    f"copies diverged: {left.relative_to(REPO_ROOT)} != "
                    f"{right.relative_to(REPO_ROOT)}",
                )

    def test_every_script_has_a_counterpart(self):
        claude, codex = (self.by_agent[agent] for agent in PLUGINS)
        self.assertEqual(
            sorted(claude), sorted(codex),
            "every script must exist in both plugins",
        )


if __name__ == "__main__":
    unittest.main()
