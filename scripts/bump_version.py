"""Set the release version everywhere it is declared and date the changelog entry.

Usage: python3 scripts/bump_version.py 0.8.0

The plugins install independently, so each one carries its own copy of the
version. This script rewrites all of them together and renames the
CHANGELOG's "## Unreleased" heading to "## <version> — <today>".
"""

import argparse
import datetime
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"\d+\.\d+\.\d+")
JSON_FILES = (
    ".claude-plugin/marketplace.json",
    "plugins/claude/.claude-plugin/plugin.json",
    "plugins/codex/.codex-plugin/plugin.json",
    "plugins/codex/plugin.json",
)
PYTHON_FILES = (
    "plugins/claude/mcp/version.py",
    "plugins/codex/mcp/version.py",
)
JSON_VERSION = re.compile(r'("version":\s*")(\d+\.\d+\.\d+)(")')
PYTHON_VERSION = re.compile(r'^(VERSION = ")(\d+\.\d+\.\d+)(")$', re.MULTILINE)
UNRELEASED = re.compile(r"^## Unreleased[ \t]*$", re.MULTILINE)


def declared_versions(root=ROOT):
    """Map each version file to the version it declares."""
    found = {}
    for name, pattern in [(f, JSON_VERSION) for f in JSON_FILES] + [
        (f, PYTHON_VERSION) for f in PYTHON_FILES
    ]:
        matches = pattern.findall((root / name).read_text())
        if len(matches) != 1:
            raise ValueError(f"{name}: expected one version, found {len(matches)}")
        found[name] = matches[0][1]
    return found


def parse(version):
    return tuple(int(part) for part in version.split("."))


def bump(version, root=ROOT, today=None):
    if not SEMVER.fullmatch(version):
        raise ValueError(f"{version!r} is not MAJOR.MINOR.PATCH")
    current = set(declared_versions(root).values())
    if len(current) != 1:
        raise ValueError(f"Version files disagree: {sorted(current)}; fix them first")
    (old,) = current
    if parse(version) <= parse(old):
        raise ValueError(f"{version} must be greater than the current {old}")
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text()
    if len(UNRELEASED.findall(text)) != 1:
        raise ValueError("CHANGELOG.md needs exactly one '## Unreleased' section")

    # Validate everything before writing anything.
    today = today or datetime.date.today().isoformat()
    changelog.write_text(UNRELEASED.sub(f"## {version} — {today}", text, count=1))
    for name, pattern in [(f, JSON_VERSION) for f in JSON_FILES] + [
        (f, PYTHON_VERSION) for f in PYTHON_FILES
    ]:
        path = root / name
        path.write_text(pattern.sub(rf"\g<1>{version}\g<3>", path.read_text(), count=1))
    return old


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="new version, e.g. 0.8.0")
    args = parser.parse_args()
    try:
        old = bump(args.version)
    except ValueError as error:
        print(f"bump_version: {error}", file=sys.stderr)
        return 1
    print(f"{old} -> {args.version}")
    for name in JSON_FILES + PYTHON_FILES + ("CHANGELOG.md",):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
