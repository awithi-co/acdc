"""okf_check.sh runs the vendored validator over a staged copy of a bundle.

The fixture bundle is deliberately imperfect: one well-formed Decision node plus
one link that points nowhere. OKF v0.2 §6.1 tolerates a broken link, so the
default run passes and only `--strict` turns the S3 warning fatal — which is
exactly the distinction these tests pin down.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
FIXTURE = REPO_ROOT / "tests/fixtures/okf_bundle"
SCRIPTS = [
    REPO_ROOT / f"plugins/{agent}/skills/okf-write/scripts/okf_check.sh"
    for agent in ("claude", "codex")
]


@unittest.skipUnless(shutil.which("node"), "node is required by the vendored validator")
class OkfCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name) / "okf_bundle"
        shutil.copytree(FIXTURE, self.bundle)

    def run_check(self, *args, script=SCRIPTS[0]):
        return subprocess.run(
            [str(script), str(self.bundle), *args],
            text=True,
            capture_output=True,
        )

    def test_default_run_passes_and_counts_the_bundle(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 concept(s)", result.stdout)
        self.assertIn("okf_bundle", result.stdout)
        self.assertIn("PASS", result.stdout)

    def test_strict_run_fails_on_the_broken_link(self):
        result = self.run_check("--strict")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("internal link does not resolve", result.stdout)
        self.assertIn("does-not-exist.md", result.stdout)
        self.assertIn("FAIL", result.stdout)

    def test_frontmatter_on_a_non_root_index_is_a_hard_error(self):
        # M4: an index.md is a listing, not a concept.
        index = self.bundle / "decisions" / "index.md"
        index.write_text('---\ntype: "Concept"\n---\n\n' + index.read_text(), encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("M4", result.stdout)

    def test_bundle_is_not_written_to(self):
        before = sorted(p.relative_to(self.bundle) for p in self.bundle.rglob("*"))
        self.run_check()
        after = sorted(p.relative_to(self.bundle) for p in self.bundle.rglob("*"))
        self.assertEqual(before, after, "validator artifacts leaked into the bundle")

    def test_graph_writes_okf_graph_html(self):
        result = self.run_check("--graph")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        graph = self.bundle / "okf-graph.html"
        self.assertTrue(graph.is_file())
        self.assertIn("okf-graph.html", result.stdout)

    def test_missing_bundle_is_a_usage_error(self):
        result = subprocess.run(
            [str(SCRIPTS[0]), str(self.bundle / "nope")],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_both_plugin_copies_behave_identically(self):
        outputs = [self.run_check("--strict", script=script) for script in SCRIPTS]
        self.assertEqual(outputs[0].returncode, outputs[1].returncode)
        self.assertEqual(outputs[0].stdout, outputs[1].stdout)


if __name__ == "__main__":
    unittest.main()
