"""okf_node.py writes node files an OKF validator will accept."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "plugins/claude/skills/okf-distill/scripts/okf_node.py"

sys.path.insert(0, str(SCRIPT.parent))

import okf_node  # noqa: E402


class SlugTests(unittest.TestCase):
    def test_kebab_case_ascii(self):
        self.assertEqual(okf_node.slugify("Vendor the OKF Validator!"), "vendor-the-okf-validator")

    def test_collapses_and_trims_separators(self):
        self.assertEqual(okf_node.slugify("  a --  b // c  "), "a-b-c")

    def test_non_ascii_title_still_yields_a_filename(self):
        slug = okf_node.slugify("지식 그래프")
        self.assertTrue(slug)
        self.assertRegex(slug, r"^[a-z0-9-]+$")

    def test_length_is_bounded(self):
        self.assertLessEqual(len(okf_node.slugify("word " * 100)), 80)


class RelativeTargetTests(unittest.TestCase):
    """Links are written file-relative: OKF resolves both forms, Obsidian one."""

    def test_root_relative_to_sibling_directory(self):
        self.assertEqual(
            okf_node.relative_target("/concepts/acp.md", "decisions"),
            "../concepts/acp.md",
        )

    def test_root_relative_within_the_same_directory(self):
        self.assertEqual(okf_node.relative_target("/decisions/b.md", "decisions"), "b.md")

    def test_root_relative_from_the_bundle_root(self):
        self.assertEqual(okf_node.relative_target("/a.md", ""), "a.md")

    def test_root_relative_up_to_the_bundle_root(self):
        self.assertEqual(okf_node.relative_target("/index.md", "concepts"), "../index.md")

    def test_nested_subdirectory(self):
        self.assertEqual(okf_node.relative_target("/a/b/c.md", "a/x"), "../b/c.md")

    def test_already_relative_targets_are_left_alone(self):
        for target in ("b.md", "../x/y.md", "./c.md", "https://example.org/z.md"):
            with self.subTest(target=target):
                self.assertEqual(okf_node.relative_target(target, "decisions"), target)


class ExplicitSlugTests(unittest.TestCase):
    """Obsidian labels a graph node with its filename, so the filename is prose."""

    def test_hangul_slug_is_kept_verbatim(self):
        self.assertEqual(okf_node.clean_explicit_slug("워크트리-주인-하나"), "워크트리-주인-하나")

    def test_a_trailing_md_is_dropped(self):
        self.assertEqual(okf_node.clean_explicit_slug("거울-사본.md"), "거울-사본")

    def test_path_separators_are_refused(self):
        for bad in ("a/b", "a\\b", ".hidden", "", "   "):
            with self.subTest(slug=bad):
                with self.assertRaises(ValueError):
                    okf_node.clean_explicit_slug(bad)

    def test_reserved_names_are_still_refused_case_insensitively(self):
        for bad in ("index", "Index", "LOG"):
            with self.subTest(slug=bad):
                with self.assertRaises(ValueError):
                    okf_node.write_node({"bundle": "/tmp", "type": "Concept", "slug": bad})


class EncodeTargetTests(unittest.TestCase):
    def test_hangul_is_percent_encoded(self):
        self.assertEqual(
            okf_node.encode_target("../lessons/워크트리-주인-하나.md"),
            "../lessons/%EC%9B%8C%ED%81%AC%ED%8A%B8%EB%A6%AC-%EC%A3%BC%EC%9D%B8-%ED%95%98%EB%82%98.md",
        )

    def test_a_space_is_encoded(self):
        self.assertEqual(okf_node.encode_target("a b.md"), "a%20b.md")

    def test_plain_ascii_is_untouched(self):
        self.assertEqual(okf_node.encode_target("../concepts/brief.md"), "../concepts/brief.md")

    def test_an_already_encoded_target_is_not_encoded_twice(self):
        self.assertEqual(okf_node.encode_target("a%20b.md"), "a%20b.md")

    def test_external_urls_are_left_alone(self):
        self.assertEqual(
            okf_node.encode_target("https://example.org/a b.md"), "https://example.org/a b.md"
        )


class WriteNodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, **overrides):
        spec = {
            "bundle": str(self.bundle),
            "subdir": "decisions",
            "type": "Decision",
            "title": "Vendor the validator",
            "sources": ["SHARE-260916", "019dc08e"],
            "generated_at": "2026-09-16T21:00:00+09:00",
            "body": "**Decision.** Ship a copy.",
            "links": [{"text": "Bundle root", "target": "/concepts/bundle-root.md"}],
        }
        spec.update(overrides)
        return okf_node.write_node(spec)

    def test_path_and_frontmatter(self):
        path = self.write()
        self.assertEqual(path, self.bundle / "decisions" / "vendor-the-validator.md")
        text = path.read_text(encoding="utf-8")
        head, _, _body = text.partition("\n---\n")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn('type: "Decision"', head)
        self.assertIn('status: "draft"', head)
        # OKF v0.2 §5.1: a source entry REQUIRES `resource`, a URI. A bare list
        # of strings parses to an empty list, so the validator checks nothing.
        self.assertIn('sources:\n', head)
        self.assertIn('  - resource: "claude-session://SHARE-260916"', head)
        self.assertIn('  - resource: "claude-session://019dc08e"', head)
        self.assertNotIn('sources: [', head)
        # §5.2
        self.assertIn('generated: { by: "acdc okf-distill", at: "2026-09-16T21:00:00+09:00" }', head)
        self.assertNotIn("distilled_at", head)

    def test_body_carries_heading_and_links(self):
        text = self.write().read_text(encoding="utf-8")
        self.assertIn("# Vendor the validator", text)
        self.assertIn("**Decision.** Ship a copy.", text)
        self.assertIn("- [Bundle root](../concepts/bundle-root.md)", text)

    def test_generated_at_defaults_to_an_iso_timestamp_with_offset(self):
        import datetime as dt
        import re

        text = self.write(generated_at=None).read_text(encoding="utf-8")
        stamp = re.search(r'at: "([^"]+)"', text).group(1)
        parsed = dt.datetime.fromisoformat(stamp)
        self.assertIsNotNone(parsed.tzinfo, "an ISO 8601 instant carries its offset")
        self.assertEqual(parsed.date(), dt.date.today())

    def test_codex_sessions_get_their_own_uri_scheme(self):
        text = self.write(agent="codex", sources=["019dc08e"]).read_text(encoding="utf-8")
        self.assertIn('resource: "codex-session://019dc08e"', text)

    def test_unknown_agent_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(agent="gemini")

    def test_a_source_dict_passes_through_and_leads_with_resource(self):
        text = self.write(
            sources=[{"title": "SHARE-260916", "id": "ce49daed", "resource": "claude-session://x"}]
        ).read_text(encoding="utf-8")
        block = text.split("sources:\n", 1)[1]
        self.assertTrue(block.startswith('  - resource: "claude-session://x"\n'))
        self.assertIn('    id: "ce49daed"', block)
        self.assertIn('    title: "SHARE-260916"', block)

    def test_a_source_dict_without_resource_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(sources=[{"title": "no uri"}])

    def test_a_comma_in_a_source_title_survives(self):
        """Inline `{a: 1, b: 2}` would split on that comma; block form does not."""
        text = self.write(
            sources=[{"resource": "claude-session://x", "title": "one, two"}]
        ).read_text(encoding="utf-8")
        self.assertIn('    title: "one, two"', text)

    def test_colon_in_title_is_quoted(self):
        """An unquoted `a: b` in frontmatter is a YAML mapping, not a title."""
        text = self.write(title="Retry: only on 5xx").read_text(encoding="utf-8")
        self.assertIn('title: "Retry: only on 5xx"', text)

    def test_reserved_filenames_are_refused(self):
        # M4: index.md / log.md must carry no frontmatter, so a node can never be one.
        for reserved in ("index", "Log"):
            with self.subTest(slug=reserved):
                with self.assertRaises(ValueError):
                    self.write(slug=reserved)

    def test_a_custom_type_without_a_role_is_refused(self):
        """A name outside the defaults must say which of the three roles it plays."""
        with self.assertRaises(ValueError) as caught:
            self.write(type="State")
        self.assertIn("role", str(caught.exception))

    def test_bundle_vocabulary_replaces_the_default_type_name(self):
        """A bundle that calls its lessons `Finding` keeps doing so."""
        text = self.write(type="Finding", role="lesson").read_text(encoding="utf-8")
        self.assertIn('type: "Finding"', text)
        self.assertNotIn("Lesson", text)

    def test_role_is_an_instruction_not_frontmatter(self):
        text = self.write(type="Finding", role="lesson").read_text(encoding="utf-8")
        self.assertNotIn("role:", text)

    def test_unknown_role_is_refused(self):
        for role in ("state", "status", ""):
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    self.write(type="Finding", role=role or None)

    def test_missing_type_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(type=None)

    def test_extra_carries_a_bundle_s_own_fields(self):
        """Neighbouring documents may share fields OKF knows nothing about."""
        text = self.write(
            extra={"lifecycle": "current", "succeeded_by": "../decisions/newer.md"}
        ).read_text(encoding="utf-8")
        self.assertIn('lifecycle: "current"', text)
        self.assertIn('succeeded_by: "../decisions/newer.md"', text)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            self.write(status="wip")

    def test_extra_fields_do_not_override_managed_ones(self):
        text = self.write(extra={"tags": ["okf", "acdc"], "status": "stable"}).read_text()
        self.assertIn('tags: ["okf", "acdc"]', text)
        self.assertIn('status: "draft"', text)
        self.assertNotIn('status: "stable"', text)

    def test_subdir_is_optional(self):
        path = self.write(subdir=None)
        self.assertEqual(path.parent, self.bundle)

    def test_related_heading_follows_the_bundle_language(self):
        """The heading sits in the body, so it cannot stay English by default."""
        text = self.write(related_heading="관련").read_text(encoding="utf-8")
        self.assertIn("## 관련", text)
        self.assertNotIn("## Related", text)

    def test_related_heading_defaults_to_english(self):
        self.assertIn("## Related", self.write().read_text(encoding="utf-8"))

    def test_no_written_link_is_root_relative(self):
        text = self.write(
            links=[
                {"text": "Sibling", "target": "/decisions/other.md"},
                {"text": "Cousin", "target": "/concepts/x.md"},
                {"text": "Index", "target": "/index.md"},
            ]
        ).read_text(encoding="utf-8")
        self.assertNotIn("](/", text)
        self.assertIn("- [Sibling](other.md)", text)
        self.assertIn("- [Cousin](../concepts/x.md)", text)
        self.assertIn("- [Index](../index.md)", text)

    def test_a_hangul_named_node_links_percent_encoded(self):
        path = self.write(
            slug="워크트리-주인-하나",
            title="워크트리 단일 소유",
            links=[{"text": "본체로서의 브리프", "target": "/concepts/본체로서의-브리프.md"}],
        )
        self.assertEqual(path.name, "워크트리-주인-하나.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("](../concepts/%EB", text)
        self.assertNotIn("](../concepts/본체", text)
        self.assertIn("# 워크트리 단일 소유", text)

    def test_cli_reads_json_from_stdin(self):
        spec = {
            "bundle": str(self.bundle),
            "type": "Lesson",
            "title": "Distil decisions, never state",
            "body": "**Rule.** ...",
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps(spec),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        written = Path(result.stdout.strip())
        self.assertTrue(written.is_file())
        self.assertIn('type: "Lesson"', written.read_text(encoding="utf-8"))

    def test_cli_rejects_bad_spec_without_traceback(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input='{"bundle": "x"}',
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
