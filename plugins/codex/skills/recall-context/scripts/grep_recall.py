#!/usr/bin/env python3
"""grep_recall.py — search agent JSONL transcripts for free-text matches.

Used by the recall-context skill of ACDC. Pure stdlib.
Run with --help for full usage.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    timestamp: str
    raw: dict[str, Any] = field(repr=False)


def parse_event(line: str) -> Event | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return Event(
        type=str(obj.get("type", "")),
        timestamp=str(obj.get("timestamp", "")),
        raw=obj,
    )


TEXT_EVENT_TYPES_CLAUDE = {"user", "assistant"}
TOOL_INPUT_TRUNCATE = 500


def _extract_claude_message(message: Any, include_tools: bool) -> str:
    if message is None:
        return ""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif btype == "tool_use" and include_tools:
            inp = block.get("input")
            try:
                rendered = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                rendered = str(inp)
            parts.append(rendered[:TOOL_INPUT_TRUNCATE])
    return "\n".join(parts)


def _extract_codex_payload(payload: Any, include_tools: bool) -> str:
    if not isinstance(payload, dict):
        return ""
    ptype = payload.get("type")
    if ptype == "message":
        content = payload.get("content")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if isinstance(p, str))
        if isinstance(content, str):
            return content
        return ""
    if ptype == "function_call" and include_tools:
        args = payload.get("arguments", "")
        if isinstance(args, str):
            return args[:TOOL_INPUT_TRUNCATE]
        try:
            return json.dumps(args, ensure_ascii=False)[:TOOL_INPUT_TRUNCATE]
        except (TypeError, ValueError):
            return str(args)[:TOOL_INPUT_TRUNCATE]
    return ""


def extract_text(event: Event, include_tools: bool) -> str:
    raw = event.raw
    if event.type in TEXT_EVENT_TYPES_CLAUDE:
        return _extract_claude_message(raw.get("message"), include_tools)
    if event.type == "response_item":
        return _extract_codex_payload(raw.get("payload"), include_tools)
    return ""


def find_matches(
    events: list[Event], pattern: re.Pattern, include_tools: bool
) -> list[int]:
    """Return indices of events whose extracted text matches `pattern`."""
    hits: list[int] = []
    for i, ev in enumerate(events):
        text = extract_text(ev, include_tools)
        if text and pattern.search(text):
            hits.append(i)
    return hits


@dataclass
class Segment:
    lo: int
    hi: int
    hits: list[int] = field(default_factory=list)


def merge_into_segments(
    events: list[Event],
    hits: list[int],
    window: int,
    max_segments: int | None = None,
) -> list[Segment]:
    if not hits:
        return []
    n = len(events)
    segments: list[Segment] = []
    for h in hits:
        lo = max(0, h - window)
        hi = min(n - 1, h + window)
        if segments and lo <= segments[-1].hi + 1:
            segments[-1].hi = max(segments[-1].hi, hi)
            segments[-1].hits.append(h)
        else:
            segments.append(Segment(lo=lo, hi=hi, hits=[h]))
    if max_segments is not None and len(segments) > max_segments:
        segments = segments[:max_segments]
    return segments


PREVIEW_CHARS = 100


def _preview(event: Event, include_tools: bool) -> str:
    text = extract_text(event, include_tools)
    if not text:
        text = f"<{event.type}>"
    text = text.replace("\n", " ").strip()
    if len(text) > PREVIEW_CHARS:
        text = text[: PREVIEW_CHARS - 1] + "…"
    return text


def render_text(
    events: list[Event],
    segments: list[Segment],
    query: str,
    transcript_label: str,
    include_tools: bool = False,
) -> str:
    total = sum(len(s.hits) for s in segments)
    lines: list[str] = [f"=== {total} matches for '{query}' in {transcript_label} ==="]
    for i, seg in enumerate(segments, 1):
        lines.append("")
        lines.append(f"--- Segment {i} (events L{seg.lo + 1}-L{seg.hi + 1}) ---")
        hits_set = set(seg.hits)
        for idx in range(seg.lo, seg.hi + 1):
            ev = events[idx]
            mark = "★" if idx in hits_set else " "
            lines.append(
                f"  {mark} L{idx + 1:>5} [{ev.timestamp}] [{ev.type}] {_preview(ev, include_tools)}"
            )
    return "\n".join(lines)


def render_json(
    events: list[Event],
    segments: list[Segment],
    query: str,
    transcript_label: str,
    include_tools: bool = False,
) -> str:
    payload = {
        "query": query,
        "transcript": transcript_label,
        "segments": [
            {
                "lo": s.lo,
                "hi": s.hi,
                "hits": s.hits,
                "events": [
                    {
                        "index": idx,
                        "type": events[idx].type,
                        "timestamp": events[idx].timestamp,
                        "preview": _preview(events[idx], include_tools),
                        "is_hit": idx in s.hits,
                    }
                    for idx in range(s.lo, s.hi + 1)
                ],
            }
            for s in segments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


import datetime as _dt
from pathlib import Path


@dataclass
class Scope:
    kind: str  # "current" | "days" | "since"
    current_path: Path | None = None
    days: int = 7
    since: _dt.date | None = None
    now: _dt.datetime | None = None  # injected for tests; defaults to wall clock


def _claude_transcripts(home: Path) -> list[Path]:
    projects = home / "projects"
    if not projects.is_dir():
        return []
    return sorted(projects.glob("*/*.jsonl"))


def _codex_transcripts(home: Path) -> list[Path]:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(sessions.glob("*/*/*/rollout-*.jsonl"))


def _filter_by_mtime(
    paths: list[Path], cutoff: _dt.datetime
) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        try:
            m = _dt.datetime.fromtimestamp(p.stat().st_mtime)
        except FileNotFoundError:
            continue
        if m >= cutoff:
            out.append(p)
    return out


def select_transcripts(
    scope: Scope,
    agent: str,
    home: Path,
    cwd_hint: str | None = None,
) -> list[Path]:
    if scope.kind == "current":
        if scope.current_path is None:
            return []
        return [scope.current_path]

    if agent == "claude":
        all_paths = _claude_transcripts(home)
    elif agent == "codex":
        all_paths = _codex_transcripts(home)
    else:
        raise ValueError(f"unknown agent: {agent}")

    if scope.kind == "days":
        now = scope.now or _dt.datetime.now()
        cutoff = now - _dt.timedelta(days=scope.days)
        result = _filter_by_mtime(all_paths, cutoff)
    elif scope.kind == "since":
        if scope.since is None:
            raise ValueError("scope.since required for kind='since'")
        cutoff = _dt.datetime.combine(scope.since, _dt.time.min)
        result = _filter_by_mtime(all_paths, cutoff)
    else:
        raise ValueError(f"unknown scope.kind: {scope.kind}")

    if scope.current_path is not None and scope.current_path not in result:
        result = [scope.current_path] + result

    if cwd_hint and agent == "claude":
        escaped = cwd_hint.replace("/", "-")
        result.sort(key=lambda p: 0 if escaped in p.parts[-2] else 1)

    return result


import argparse
import sys


def _agent_from_path(path: Path) -> str:
    s = str(path)
    # Real homes
    if "/.claude/" in s:
        return "claude"
    if "/.codex/" in s:
        return "codex"
    # Test fixtures (so days/since scope tests don't misclassify)
    if "/codex_home/" in s:
        return "codex"
    if "/claude_home/" in s:
        return "claude"
    return "claude"


def _agent_home(agent: str) -> Path:
    if agent == "claude":
        return Path.home() / ".claude"
    if agent == "codex":
        return Path.home() / ".codex"
    raise ValueError(f"unknown agent: {agent}")


def _read_events(path: Path) -> list[Event]:
    events: list[Event] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ev = parse_event(line)
                if ev is not None:
                    events.append(ev)
    except OSError as e:
        print(f"error: {path}: {e}", file=sys.stderr)
    return events


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grep_recall.py",
        description="Search agent JSONL transcripts for free-text matches.",
        epilog=(
            "Note: --cwd-hint applies to Claude transcripts only "
            "(Codex partitions by date, not cwd; flag silently ignored on Codex)."
        ),
    )
    p.add_argument("--query", required=True, help="text to search for (regex)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--current", action="store_true",
                   help="search the current session transcript only")
    g.add_argument("--days", type=int, metavar="N",
                   help="search transcripts from the last N days (default 7)")
    g.add_argument("--since", metavar="YYYY-MM-DD",
                   help="search transcripts on or after this date")
    p.add_argument("--current-transcript", metavar="PATH",
                   help="explicit path to the current transcript (passed by skill)")
    p.add_argument("--window", type=int, default=3,
                   help="±N events around each match (default 3)")
    p.add_argument("--max-segments", type=int, default=5,
                   help="cap on number of segments returned (default 5)")
    p.add_argument("--include-tools", action="store_true",
                   help="also search tool_use input (truncated to 500 chars)")
    p.add_argument("--cwd-hint", metavar="PATH",
                   help="prefer transcripts under this cwd (Claude only)")
    p.add_argument("--agent", choices=("claude", "codex"),
                   help="force agent type; otherwise inferred from --current-transcript")
    p.add_argument("--json", action="store_true",
                   help="output JSON instead of human-readable text")
    return p


def _build_scope(args: argparse.Namespace) -> Scope:
    current_path = Path(args.current_transcript) if args.current_transcript else None
    if args.current:
        return Scope(kind="current", current_path=current_path)
    if args.since:
        try:
            since = _dt.date.fromisoformat(args.since)
        except ValueError:
            raise SystemExit(f"error: invalid --since date: {args.since}")
        return Scope(kind="since", since=since, current_path=current_path)
    days = args.days if args.days is not None else 7
    return Scope(kind="days", days=days, current_path=current_path)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    scope = _build_scope(args)

    if args.agent:
        agent = args.agent
    elif args.current_transcript:
        agent = _agent_from_path(Path(args.current_transcript))
    else:
        agent = "claude"

    home = _agent_home(agent)
    transcripts = select_transcripts(scope, agent, home, cwd_hint=args.cwd_hint)
    if not transcripts:
        print("no transcripts found in scope")
        return 0

    pattern = re.compile(re.escape(args.query), re.IGNORECASE)

    rendered_chunks: list[str] = []
    json_chunks: list[dict] = []
    total_hits = 0

    for tpath in transcripts:
        events = _read_events(tpath)
        hits = find_matches(events, pattern, include_tools=args.include_tools)
        if not hits:
            continue
        segments = merge_into_segments(events, hits, window=args.window,
                                        max_segments=args.max_segments)
        total_hits += sum(len(s.hits) for s in segments)
        label = str(tpath)
        if args.json:
            json_chunks.append(json.loads(
                render_json(events, segments, args.query, label,
                            include_tools=args.include_tools)
            ))
        else:
            rendered_chunks.append(
                render_text(events, segments, args.query, label,
                            include_tools=args.include_tools)
            )

    if total_hits == 0:
        print(f"no matches for '{args.query}'")
        return 0

    if args.json:
        print(json.dumps(
            {"query": args.query, "transcripts": json_chunks},
            ensure_ascii=False, indent=2,
        ))
    else:
        print("\n\n".join(rendered_chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
