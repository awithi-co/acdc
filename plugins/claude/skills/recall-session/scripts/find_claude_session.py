#!/usr/bin/env python3
"""Find Claude Code session candidates by name or session id."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


NAME_KEYS = {"name", "customTitle", "agentName"}
CWD_KEYS = {"cwd", "workingDirectory", "workspace", "projectPath"}
TIME_KEYS = {"updatedAt", "lastUpdatedAt", "timestamp", "createdAt"}

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
UUID_PREFIX_RE = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{1,4})*")


def looks_like_uuid(text: str) -> bool:
    return bool(UUID_RE.fullmatch(text))


def looks_like_uuid_ish(text: str) -> bool:
    return looks_like_uuid(text) or bool(UUID_PREFIX_RE.fullmatch(text))


@dataclass
class Candidate:
    session_id: str
    score: int
    source: Path
    matched: str = ""
    name: str = ""
    cwd: str = ""
    updated_at: str = ""
    transcript_paths: list[Path] = field(default_factory=list)
    task_paths: list[Path] = field(default_factory=list)


def iter_search_roots(claude_home: Path) -> Iterable[Path]:
    for path in (
        claude_home / "history.jsonl",
        claude_home / "sessions",
        claude_home / "projects",
    ):
        if path.exists():
            yield path


def iter_matching_records(
    claude_home: Path,
    query: str,
    max_files: int,
) -> Iterable[tuple[Path, Any]]:
    escaped_query = re.escape(query)
    pattern = rf'"(name|customTitle|agentName)"\s*:\s*"[^"]*{escaped_query}[^"]*"'
    roots = [str(path) for path in iter_search_roots(claude_home)]
    if not roots:
        return

    try:
        result = subprocess.run(
            ["rg", "--line-number", "--ignore-case", pattern, *roots],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return

    parsed_json_files: set[Path] = set()
    seen_files: set[Path] = set()
    for line in result.stdout.splitlines():
        try:
            raw_path, _line_number, text = line.split(":", 2)
        except ValueError:
            continue

        path = Path(raw_path)
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            continue

        seen_files.add(path)
        if len(seen_files) > max_files:
            return

        if path.suffix.lower() == ".jsonl":
            try:
                yield path, json.loads(text)
            except json.JSONDecodeError:
                continue
        elif path not in parsed_json_files:
            parsed_json_files.add(path)
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    yield path, json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue


def walk(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def first_text(obj: Any, keys: set[str]) -> str:
    for key, value in walk(obj):
        if key in keys and isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
    return ""


def first_session_id(obj: Any) -> str:
    session_id = first_text(obj, {"sessionId", "session_id"})
    if session_id:
        return session_id

    candidate_id = first_text(obj, {"id"})
    if looks_like_uuid(candidate_id):
        return candidate_id
    return ""


def all_text(obj: Any, keys: set[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for key, value in walk(obj):
        if key in keys and isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def score_record(obj: Any, query: str, cwd_hint: str) -> tuple[int, str, str]:
    query_lower = query.lower()
    cwd_hint_lower = cwd_hint.lower()
    names = all_text(obj, NAME_KEYS)
    cwd = first_text(obj, CWD_KEYS)

    score = 0
    matched = ""
    for name in names:
        lower = name.lower()
        if lower == query_lower:
            score = max(score, 100)
            matched = f"name={name}"
        elif query_lower in lower:
            score = max(score, 70)
            matched = f"name~{name}"

    if score == 0:
        raw = json.dumps(obj, ensure_ascii=False).lower()
        if query_lower in raw:
            score = 25
            matched = "raw-text"

    if cwd_hint_lower and cwd_hint_lower in cwd.lower():
        score += 30

    return score, matched, cwd


def locate_related_files(claude_home: Path, session_id: str) -> tuple[list[Path], list[Path]]:
    transcript_paths = sorted((claude_home / "projects").rglob(f"{session_id}.jsonl"))
    task_dir = claude_home / "tasks" / session_id
    task_paths: list[Path] = []
    if task_dir.exists():
        task_paths = sorted(path for path in task_dir.iterdir() if path.is_file())
    return transcript_paths, task_paths


def collect_by_uuid(claude_home: Path, query: str) -> list[Candidate]:
    projects = claude_home / "projects"
    if not projects.exists():
        return []
    q = query.lower()
    matches = sorted(projects.rglob(f"{q}*.jsonl"))
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for transcript in matches:
        session_id = transcript.stem
        if not looks_like_uuid(session_id) or session_id in seen:
            continue
        seen.add(session_id)
        transcript_paths, task_paths = locate_related_files(claude_home, session_id)
        score = 95 if session_id.lower() == q else 85
        candidates.append(
            Candidate(
                session_id=session_id,
                score=score,
                source=transcript,
                matched="uuid",
                name="",
                cwd="",
                updated_at="",
                transcript_paths=transcript_paths,
                task_paths=task_paths,
            )
        )
    return sorted(
        candidates,
        key=lambda item: (item.score, item.updated_at, item.session_id),
        reverse=True,
    )


def collect_candidates(
    claude_home: Path,
    query: str,
    cwd_hint: str,
    max_project_files: int,
) -> list[Candidate]:
    if looks_like_uuid_ish(query):
        return collect_by_uuid(claude_home, query)

    by_id: dict[str, Candidate] = {}

    for path, record in iter_matching_records(claude_home, query, max_project_files):
        session_id = first_session_id(record)
        if not session_id:
            continue

        score, matched, cwd = score_record(record, query, cwd_hint)
        if score == 0:
            continue

        name = first_text(record, NAME_KEYS)
        updated_at = first_text(record, TIME_KEYS)

        existing = by_id.get(session_id)
        if existing is None or score > existing.score:
            by_id[session_id] = Candidate(
                session_id=session_id,
                score=score,
                source=path,
                matched=matched,
                name=name,
                cwd=cwd,
                updated_at=updated_at,
            )
        elif existing and updated_at > existing.updated_at:
            existing.updated_at = updated_at

    return sorted(
        by_id.values(),
        key=lambda item: (item.score, item.updated_at, item.session_id),
        reverse=True,
    )


def enrich_related_files(claude_home: Path, candidates: list[Candidate], limit: int) -> None:
    for item in candidates[:limit]:
        if item.transcript_paths or item.task_paths:
            continue
        item.transcript_paths, item.task_paths = locate_related_files(claude_home, item.session_id)


def print_text(candidates: list[Candidate], limit: int) -> None:
    if not candidates:
        print("No Claude session candidates found.")
        return

    for index, item in enumerate(candidates[:limit], start=1):
        print(f"## Candidate {index}")
        print(f"score: {item.score}")
        print(f"sessionId: {item.session_id}")
        if item.name:
            print(f"name: {item.name}")
        if item.cwd:
            print(f"cwd: {item.cwd}")
        if item.updated_at:
            print(f"updatedAt: {item.updated_at}")
        print(f"matched: {item.matched}")
        print(f"source: {item.source}")
        print("transcripts:")
        for path in item.transcript_paths[:5]:
            print(f"  - {path}")
        if not item.transcript_paths:
            print("  - none found")
        print("tasks:")
        for path in item.task_paths[:10]:
            print(f"  - {path}")
        if not item.task_paths:
            print("  - none found")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Claude session name or id (UUID or UUID prefix)")
    parser.add_argument(
        "--claude-home",
        default=os.path.expanduser("~/.claude"),
        help="Claude storage directory, default: ~/.claude",
    )
    parser.add_argument("--cwd-hint", default="", help="Adds +30 score to candidates whose cwd contains this text. Disambiguates same-name sessions across projects.")
    parser.add_argument("--limit", type=int, default=8, help="Maximum candidates to print")
    parser.add_argument(
        "--max-project-files",
        type=int,
        default=20,
        help="Maximum project transcript files to parse after ripgrep narrows them",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON array. Useful for jq-piping or extracting a specific field (e.g. transcript_paths) for the next command.")
    args = parser.parse_args()

    claude_home = Path(args.claude_home).expanduser()
    candidates = collect_candidates(
        claude_home,
        args.query,
        args.cwd_hint,
        args.max_project_files,
    )
    enrich_related_files(claude_home, candidates, args.limit)

    if args.json:
        print(
            json.dumps(
                [candidate.__dict__ for candidate in candidates[: args.limit]],
                default=str,
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_text(candidates, args.limit)

    return 0 if candidates else 1


if __name__ == "__main__":
    raise SystemExit(main())
