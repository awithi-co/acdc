#!/usr/bin/env python3
"""Find Codex session candidates by thread name or session id."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


UUID_FULL_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
UUID_PREFIX_RE = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{1,4})*")


def looks_like_uuid_ish(text: str) -> bool:
    return bool(UUID_FULL_RE.fullmatch(text) or UUID_PREFIX_RE.fullmatch(text))


@dataclass
class Candidate:
    session_id: str
    score: int
    thread_name: str = ""
    updated_at: str = ""
    cwd: str = ""
    rollout_paths: list[Path] = field(default_factory=list)
    shell_snapshots: list[Path] = field(default_factory=list)


def iter_index(codex_home: Path) -> Iterable[dict[str, Any]]:
    index = codex_home / "session_index.jsonl"
    if not index.exists():
        return
    with index.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def locate_rollouts(codex_home: Path, session_id: str) -> list[Path]:
    root = codex_home / "sessions"
    if not root.exists():
        return []
    return sorted(root.rglob(f"*{session_id}*.jsonl"))


def locate_shell_snapshots(codex_home: Path, session_id: str) -> list[Path]:
    root = codex_home / "shell_snapshots"
    if not root.exists():
        return []
    return sorted(root.glob(f"*{session_id}*"))


def first_session_meta(rollout: Path) -> dict[str, Any]:
    try:
        with rollout.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    return payload if isinstance(payload, dict) else {}
    except OSError:
        return {}
    return {}


def score_record(record: dict[str, Any], query: str, cwd_hint: str, cwd: str) -> int:
    query_lower = query.lower()
    name = str(record.get("thread_name") or "")
    session_id = str(record.get("id") or "")

    score = 0
    if name.lower() == query_lower:
        score = 100
    elif query_lower in name.lower():
        score = 75
    elif session_id == query:
        score = 95
    elif session_id.startswith(query):
        score = 85

    if cwd_hint and cwd_hint.lower() in cwd.lower():
        score += 30

    return score


def collect_by_uuid(codex_home: Path, query: str) -> list[Candidate]:
    root = codex_home / "sessions"
    if not root.exists():
        return []
    q = query.lower()
    matches = sorted(root.rglob(f"*{q}*.jsonl"))
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for rollout in matches:
        meta = first_session_meta(rollout)
        session_id = str(meta.get("id") or "").strip()
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        score = 95 if session_id.lower() == q else 85
        candidates.append(
            Candidate(
                session_id=session_id,
                score=score,
                thread_name="",
                updated_at=str(meta.get("timestamp") or ""),
                cwd=str(meta.get("cwd") or ""),
                rollout_paths=locate_rollouts(codex_home, session_id),
                shell_snapshots=locate_shell_snapshots(codex_home, session_id),
            )
        )
    return sorted(
        candidates,
        key=lambda item: (item.score, item.updated_at, item.session_id),
        reverse=True,
    )


def collect_candidates(codex_home: Path, query: str, cwd_hint: str) -> list[Candidate]:
    if looks_like_uuid_ish(query):
        return collect_by_uuid(codex_home, query)

    candidates: list[Candidate] = []
    for record in iter_index(codex_home):
        session_id = str(record.get("id") or "").strip()
        if not session_id:
            continue

        rollout_paths = locate_rollouts(codex_home, session_id)
        meta = first_session_meta(rollout_paths[-1]) if rollout_paths else {}
        cwd = str(meta.get("cwd") or "")
        score = score_record(record, query, cwd_hint, cwd)
        if score == 0:
            continue

        candidates.append(
            Candidate(
                session_id=session_id,
                score=score,
                thread_name=str(record.get("thread_name") or ""),
                updated_at=str(record.get("updated_at") or ""),
                cwd=cwd,
                rollout_paths=rollout_paths,
                shell_snapshots=locate_shell_snapshots(codex_home, session_id),
            )
        )

    return sorted(
        candidates,
        key=lambda item: (item.score, item.updated_at, item.session_id),
        reverse=True,
    )


def print_human(candidates: list[Candidate], limit: int) -> None:
    if not candidates:
        print("No Codex session candidates found.")
        return

    for item in candidates[:limit]:
        print(f"session_id: {item.session_id}")
        print(f"  score: {item.score}")
        print(f"  thread_name: {item.thread_name}")
        print(f"  updated_at: {item.updated_at}")
        print(f"  cwd: {item.cwd}")
        print("  rollouts:")
        for path in item.rollout_paths:
            print(f"    - {path}")
        print("  shell_snapshots:")
        for path in item.shell_snapshots:
            print(f"    - {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Codex thread name or session id")
    parser.add_argument("--codex-home", default="~/.codex", help="Codex home directory")
    parser.add_argument("--cwd-hint", default="", help="Adds +30 score to candidates whose cwd contains this text. Disambiguates same-name sessions across projects.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum candidates to print")
    parser.add_argument("--json", action="store_true", help="Emit JSON array. Useful for jq-piping or extracting a specific field (e.g. rollout_paths) for the next command.")
    args = parser.parse_args()

    codex_home = Path(args.codex_home).expanduser()
    candidates = collect_candidates(codex_home, args.query, args.cwd_hint)
    limited = candidates[: args.limit]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "session_id": item.session_id,
                        "score": item.score,
                        "thread_name": item.thread_name,
                        "updated_at": item.updated_at,
                        "cwd": item.cwd,
                        "rollout_paths": [str(p) for p in item.rollout_paths],
                        "shell_snapshots": [str(p) for p in item.shell_snapshots],
                    }
                    for item in limited
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_human(candidates, args.limit)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
