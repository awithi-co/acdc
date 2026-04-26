#!/usr/bin/env python3
"""Print a bounded timeline from a Codex rollout JSONL file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def compact(value: Any, limit: int) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


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


def text_parts_from_message(message: dict[str, Any], limit: int) -> list[str]:
    parts: list[str] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if text is None:
            text = item.get("content")
        if text:
            parts.append(compact(text, limit))
    return parts


def is_control_message(role: str, parts: list[str]) -> bool:
    if role.lower() in {"developer", "system"}:
        return True
    if not parts:
        return False
    first = parts[0].lstrip()
    return first.startswith("# AGENTS.md instructions for") or first.startswith("<skill>")


def event_from_response_item(line_number: int, payload: dict[str, Any], limit: int) -> str | None:
    item_type = payload.get("type")
    if item_type == "message":
        role = str(payload.get("role") or "message")
        parts = text_parts_from_message(payload, limit)
        if is_control_message(role, parts):
            return None
        if parts:
            return f"LINE {line_number} | {role.upper()}: {' | '.join(parts)}"
    if item_type == "function_call":
        name = payload.get("name") or "tool"
        args = payload.get("arguments") or ""
        return f"LINE {line_number} | TOOL_CALL {name}: {compact(args, limit)}"
    if item_type == "function_call_output":
        output = payload.get("output") or ""
        return f"LINE {line_number} | TOOL_OUTPUT: {compact(output, limit)}"
    return None


def event_from_event_msg(line_number: int, payload: dict[str, Any], limit: int) -> str | None:
    event_type = payload.get("type") or "event"
    if event_type == "agent_message":
        return f"LINE {line_number} | AGENT: {compact(payload.get('message'), limit)}"
    if event_type == "user_message":
        return f"LINE {line_number} | USER: {compact(payload.get('message'), limit)}"
    if event_type == "thread_name_updated":
        return f"LINE {line_number} | THREAD_NAME: {compact(payload, limit)}"
    if event_type in {
        "task_started",
        "exec_command_begin",
        "exec_command_end",
        "turn_diff",
        "session_configured",
        "token_count",
    }:
        return f"LINE {line_number} | EVENT {event_type}: {compact(payload, limit)}"
    return None


def event_text(line_number: int, obj: dict[str, Any], limit: int) -> str | None:
    obj_type = obj.get("type")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None

    if obj_type == "session_meta":
        fields = {
            "id": payload.get("id"),
            "timestamp": payload.get("timestamp"),
            "cwd": payload.get("cwd"),
            "cli_version": payload.get("cli_version"),
            "originator": payload.get("originator"),
            "source": payload.get("source"),
        }
        return f"LINE {line_number} | SESSION_META: {compact(fields, limit)}"

    if obj_type == "turn_context":
        fields = {
            "cwd": payload.get("cwd"),
            "model": payload.get("model"),
            "approval_policy": payload.get("approval_policy"),
            "sandbox_policy": payload.get("sandbox_policy"),
            "current_date": payload.get("current_date"),
            "timezone": payload.get("timezone"),
        }
        return f"LINE {line_number} | TURN_CONTEXT: {compact(fields, limit)}"

    if obj_type == "event_msg":
        return event_from_event_msg(line_number, payload, limit)

    if obj_type == "response_item":
        return event_from_response_item(line_number, payload, limit)

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", help="Codex rollout JSONL path")
    parser.add_argument("--from-line", type=int, default=None, help="Summarize rows from this line onwards (capped by --max-events). Overrides --tail.")
    parser.add_argument("--tail", type=int, default=120, help="Last N rows (default 120). Use --tail 0 with --max-events for head mode (first N rows).")
    parser.add_argument("--limit", type=int, default=700, help="Max chars per event; longer content is truncated with '...'. Independent axis from --max-events (which caps event count).")
    parser.add_argument("--max-events", type=int, default=160, help="Event count cap for --from-line and --tail 0 modes. --tail >0 ignores this.")
    args = parser.parse_args()

    rows = load_rows(Path(args.rollout).expanduser())
    selected = select_rows(rows, args.from_line, args.tail, args.max_events)
    for line_number, obj in selected:
        text = event_text(line_number, obj, args.limit)
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
