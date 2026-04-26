#!/usr/bin/env python3
"""Print a bounded timeline from a Claude Code JSONL transcript."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def compact(text: Any, limit: int) -> str:
    value = str(text or "").replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def load_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append((line_number, obj))
    return rows


def event_text(line_number: int, obj: dict[str, Any], limit: int) -> str | None:
    cwd = obj.get("cwd") or ""
    branch = obj.get("gitBranch") or ""
    prefix = f"LINE {line_number}"
    if cwd:
        prefix += f" | cwd={cwd}"
    if branch:
        prefix += f" | branch={branch}"

    obj_type = obj.get("type")
    if obj_type == "last-prompt":
        return f"{prefix} | LAST: {compact(obj.get('lastPrompt'), limit)}"

    message = obj.get("message") or {}
    if obj_type == "user":
        content = message.get("content")
        if isinstance(content, str):
            return f"{prefix} | USER: {compact(content, limit)}"
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    parts.append(compact(item.get("content"), limit))
            if parts:
                return f"{prefix} | TOOL_RESULT: {' | '.join(parts)[:limit]}"

    if message.get("role") == "assistant":
        parts = []
        for item in message.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(f"ASSIST: {compact(item.get('text'), limit)}")
            elif item.get("type") == "tool_use":
                tool_input = item.get("input") or {}
                command = (
                    tool_input.get("command")
                    or tool_input.get("file_path")
                    or tool_input.get("path")
                    or ""
                )
                desc = tool_input.get("description") or ""
                parts.append(
                    f"TOOL {item.get('name')} | {compact(desc, 160)} | {compact(command, limit)}"
                )
        if parts:
            return f"{prefix} | " + " || ".join(parts)

    return None


def select_rows(
    rows: list[tuple[int, dict[str, Any]]],
    from_line: int | None,
    tail: int,
    max_events: int,
) -> list[tuple[int, dict[str, Any]]]:
    if from_line is not None:
        return [(line, obj) for line, obj in rows if line >= from_line][:max_events]
    if tail > 0:
        return rows[-tail:]
    return rows[:max_events]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", help="Claude JSONL transcript path")
    parser.add_argument("--from-line", type=int, default=None, help="Summarize rows from this line onwards (capped by --max-events). Overrides --tail.")
    parser.add_argument("--tail", type=int, default=120, help="Last N rows (default 120). Use --tail 0 with --max-events for head mode (first N rows).")
    parser.add_argument("--limit", type=int, default=700, help="Max chars per event; longer content is truncated with '...'. Independent axis from --max-events (which caps event count).")
    parser.add_argument("--max-events", type=int, default=160, help="Event count cap for --from-line and --tail 0 modes. --tail >0 ignores this.")
    args = parser.parse_args()

    rows = load_rows(Path(args.transcript).expanduser())
    selected = select_rows(rows, args.from_line, args.tail, args.max_events)
    for line_number, obj in selected:
        text = event_text(line_number, obj, args.limit)
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
