#!/usr/bin/env python3
"""Write one OKF node file from a JSON spec on stdin.

Hand-formatting YAML frontmatter in a loop is where distillation goes wrong:
a stray colon in a title, an unquoted date that YAML reads as a sexagesimal,
a slug with a space in it that then needs percent-encoding at every link site.
This helper owns those rules so the agent only supplies content.

Input (stdin, JSON object):

    {
      "bundle":   "path/to/bundle",        # required
      "subdir":   "decisions",             # optional, default "" (bundle root)
      "type":     "Decision",              # required: Decision | Lesson | Concept
      "title":    "Vendor the OKF validator",
      "slug":     "vendor-okf-validator",  # optional, derived from title
      "status":   "draft",                 # optional, default "draft"
      "sources":  ["SHARE-260916"],        # session names or ids
      "distilled_at": "2026-09-16",        # optional, default: today
      "body":     "markdown body",
      "links":    [{"text": "Why", "target": "/decisions/other.md"}],
      "extra":    {"tags": ["okf"]}        # optional extra frontmatter fields
    }

Output: the written path on stdout. Exit 1 on a bad spec.

Pure standard library.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

NODE_TYPES = ("Decision", "Lesson", "Concept")
STATUSES = ("draft", "stable", "deprecated")

# Reserved OKF filenames: an index.md or log.md must never carry frontmatter
# (spec §8/§9, validator rule M4), so a node may never be written to one.
RESERVED = {"index", "log"}


def slugify(text: str) -> str:
    """Lowercase kebab-case ASCII. Keeps link targets free of percent-encoding."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        # A title with no ASCII at all (e.g. all Hangul) still needs a filename.
        slug = "node-" + re.sub(r"[^0-9a-f]", "", hex(abs(hash(text))))[:8]
    return slug[:80].strip("-")


def yaml_scalar(value: Any) -> str:
    """Quote anything YAML could reinterpret. Cheaper than being clever."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def yaml_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(yaml_scalar(item) for item in value) + "]"
    return yaml_scalar(value)


def build_frontmatter(spec: dict[str, Any]) -> str:
    node_type = spec["type"]
    fields: dict[str, Any] = {
        "type": node_type,
        "status": spec.get("status") or "draft",
    }
    title = spec.get("title")
    if title:
        fields["title"] = title
    sources = spec.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    fields["sources"] = list(sources)
    fields["distilled_at"] = spec.get("distilled_at") or dt.date.today().isoformat()
    for key, value in (spec.get("extra") or {}).items():
        if key in ("type", "status", "sources", "distilled_at"):
            continue  # the managed fields above win
        fields[key] = value

    lines = ["---"]
    lines += [f"{key}: {yaml_value(value)}" for key, value in fields.items()]
    lines.append("---")
    return "\n".join(lines)


def build_body(spec: dict[str, Any]) -> str:
    parts = []
    title = spec.get("title")
    if title:
        parts.append(f"# {title}")
    body = (spec.get("body") or "").strip()
    if body:
        parts.append(body)
    links = spec.get("links") or []
    if links:
        rendered = []
        for link in links:
            if isinstance(link, str):
                text, target = link, link
            else:
                target = link.get("target", "")
                text = link.get("text") or target
            if not target:
                continue
            rendered.append(f"- [{text}]({target})")
        if rendered:
            parts.append("## Related\n\n" + "\n".join(rendered))
    return "\n\n".join(parts)


def validate(spec: dict[str, Any]) -> tuple[Path, str]:
    if not spec.get("bundle"):
        raise ValueError("`bundle` is required")
    node_type = spec.get("type")
    if node_type not in NODE_TYPES:
        raise ValueError(f"`type` must be one of {', '.join(NODE_TYPES)}, got {node_type!r}")
    status = spec.get("status") or "draft"
    if status not in STATUSES:
        raise ValueError(f"`status` must be one of {', '.join(STATUSES)}, got {status!r}")
    if not (spec.get("title") or spec.get("slug")):
        raise ValueError("one of `title` or `slug` is required")

    slug = slugify(spec.get("slug") or spec["title"])
    if slug in RESERVED:
        raise ValueError(f"`{slug}.md` is a reserved OKF filename and cannot be a node")

    subdir = (spec.get("subdir") or "").strip("/")
    directory = Path(spec["bundle"]) / subdir if subdir else Path(spec["bundle"])
    return directory / f"{slug}.md", slug


def write_node(spec: dict[str, Any]) -> Path:
    path, _slug = validate(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_frontmatter(spec) + "\n\n" + build_body(spec).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    try:
        spec = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"okf_node: stdin is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(spec, dict):
        print("okf_node: expected a JSON object on stdin", file=sys.stderr)
        return 1
    try:
        path = write_node(spec)
    except (ValueError, OSError) as exc:
        print(f"okf_node: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
