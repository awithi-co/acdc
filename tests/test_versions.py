"""Every plugin copy declares the same release, and the bump script keeps it so."""

import importlib.util
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "bump_version", ROOT / "scripts/bump_version.py"
)
bump_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump_version)

RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$", re.MULTILINE)


class VersionConsistencyTests(unittest.TestCase):
    def test_all_version_files_declare_the_same_version(self):
        versions = bump_version.declared_versions()
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_latest_changelog_release_matches_the_declared_version(self):
        (version,) = set(bump_version.declared_versions().values())
        releases = RELEASE_HEADING.findall((ROOT / "CHANGELOG.md").read_text())
        self.assertTrue(releases, "CHANGELOG.md has no dated release heading")
        self.assertEqual(releases[0], version)


class BumpVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in bump_version.JSON_FILES + bump_version.PYTHON_FILES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)
        (self.version,) = set(bump_version.declared_versions(self.root).values())
        major, minor, _ = bump_version.parse(self.version)
        self.next = f"{major}.{minor + 1}.0"
        self.write_changelog("## Unreleased\n\n### Added\n\n- Something.\n\n")

    def write_changelog(self, top):
        (self.root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n{top}## {self.version} — 2026-01-01\n\n- Old.\n"
        )

    def snapshot(self):
        return {p: p.read_text() for p in self.root.rglob("*") if p.is_file()}

    def test_bump_updates_every_file_and_dates_the_unreleased_entry(self):
        old = bump_version.bump(self.next, self.root, today="2026-11-03")
        self.assertEqual(old, self.version)
        versions = bump_version.declared_versions(self.root)
        self.assertEqual(set(versions.values()), {self.next})
        self.assertEqual(len(versions), 6)
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertIn(f"## {self.next} — 2026-11-03\n\n### Added", changelog)
        self.assertNotIn("Unreleased", changelog)

    def test_rejected_bumps_leave_every_file_untouched(self):
        cases = {
            "not semver": lambda: bump_version.bump("1.0", self.root),
            "same version": lambda: bump_version.bump(self.version, self.root),
            "older version": lambda: bump_version.bump("0.0.1", self.root),
        }
        for label, call in cases.items():
            with self.subTest(label):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(self.snapshot(), before)

    def test_missing_unreleased_section_is_rejected(self):
        self.write_changelog("")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Unreleased"):
            bump_version.bump(self.next, self.root)
        self.assertEqual(self.snapshot(), before)

    def test_disagreeing_files_are_rejected(self):
        path = self.root / bump_version.PYTHON_FILES[0]
        path.write_text(path.read_text().replace(self.version, "9.9.9"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "disagree"):
            bump_version.bump("10.0.0", self.root)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
