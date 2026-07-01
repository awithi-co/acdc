#!/usr/bin/env python3
"""Print a bounded timeline from a Codex rollout JSONL file.

A Codex rollout is NOT always one working session: resuming can fork a thread,
replaying the ancestor's full history into the new file (a burst of rows that
share the fork instant), and a single thread id accumulates work across days.
Use --segments to map the file (lineage + activity segments) before reading,
then --segment N (or "last") to summarize just the working session you mean.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPLAY_WINDOW_SECONDS = 60.0
DEFAULT_GAP_HOURS = 3.0


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


def parse_row_ts(obj: dict[str, Any]) -> datetime | None:
    raw = obj.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def session_metas(rows: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    return [
        (line, obj["payload"])
        for line, obj in rows
        if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict)
    ]


def replay_prefix_end(rows: list[tuple[int, dict[str, Any]]]) -> int:
    """Index (exclusive) of the fork-replay burst at the start of a forked rollout.

    On fork, the ancestor's history is copied in with every row stamped at the
    fork instant, so the inherited prefix is the contiguous run of rows whose
    timestamp stays within REPLAY_WINDOW_SECONDS of the first row's.
    """
    if len(session_metas(rows)) < 2:
        return 0
    fork_ts = parse_row_ts(rows[0][1])
    if fork_ts is None:
        return 0
    end = 0
    for index, (_, obj) in enumerate(rows):
        ts = parse_row_ts(obj)
        if ts is not None and (ts - fork_ts).total_seconds() >= REPLAY_WINDOW_SECONDS:
            break
        end = index + 1
    return end


@dataclass
class Segment:
    number: int
    inherited: bool
    rows: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    start_ts: datetime | None = None
    end_ts: datetime | None = None

    @property
    def start_line(self) -> int:
        return self.rows[0][0]

    @property
    def end_line(self) -> int:
        return self.rows[-1][0]

    def first_user_message(self) -> str:
        for _, obj in self.rows:
            payload = obj.get("payload")
            if (
                obj.get("type") == "event_msg"
                and isinstance(payload, dict)
                and payload.get("type") == "user_message"
            ):
                return compact(payload.get("message"), 120)
        return ""

    def user_message_count(self) -> int:
        return sum(
            1
            for _, obj in self.rows
            if obj.get("type") == "event_msg"
            and isinstance(obj.get("payload"), dict)
            and obj["payload"].get("type") == "user_message"
        )


def build_segments(
    rows: list[tuple[int, dict[str, Any]]], gap_hours: float
) -> list[Segment]:
    if not rows:
        return []
    segments: list[Segment] = []
    replay_end = replay_prefix_end(rows)
    if replay_end > 0:
        inherited = Segment(number=0, inherited=True, rows=rows[:replay_end])
        inherited.start_ts = parse_row_ts(rows[0][1])
        inherited.end_ts = parse_row_ts(rows[replay_end - 1][1])
        segments.append(inherited)

    current: Segment | None = None
    previous_ts: datetime | None = None
    for line, obj in rows[replay_end:]:
        ts = parse_row_ts(obj)
        is_break = (
            current is None
            or (
                ts is not None
                and previous_ts is not None
                and (ts - previous_ts).total_seconds() > gap_hours * 3600
            )
        )
        if is_break:
            current = Segment(number=len(segments) if segments and segments[0].inherited else len(segments) + 1, inherited=False)
            current.start_ts = ts
            segments.append(current)
        current.rows.append((line, obj))
        if ts is not None:
            current.end_ts = ts
            previous_ts = ts
    return segments


def fmt_ts(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "?"


def print_segments(path: Path, rows: list[tuple[int, dict[str, Any]]], gap_hours: float) -> None:
    metas = session_metas(rows)
    print(f"ROLLOUT: {path}")
    if metas:
        own = metas[0][1]
        print(f"SESSION: {own.get('id') or own.get('session_id')} created={own.get('timestamp')} cwd={own.get('cwd')}")
        forked_from = own.get("forked_from_id")
        if forked_from:
            print(f"forked_from: {forked_from}")
        for _, ancestor in metas[1:]:
            print(
                f"embedded ancestor meta: {ancestor.get('id') or ancestor.get('session_id')}"
                f" created={ancestor.get('timestamp')}"
            )
    segments = build_segments(rows, gap_hours)
    if not segments:
        print("no timestamped rows found")
        return
    print(f"segments (split on gaps > {gap_hours:g}h; a multi-day thread = multiple working sessions):")
    for segment in segments:
        label = "inherited replay (ancestor history)" if segment.inherited else "live"
        first_user = segment.first_user_message()
        print(
            f"SEGMENT {segment.number} | {label} | lines {segment.start_line}-{segment.end_line}"
            f" | {fmt_ts(segment.start_ts)} -> {fmt_ts(segment.end_ts)}"
            f" | user_msgs={segment.user_message_count()}"
            + (f" | first_user: {first_user}" if first_user else "")
        )


def resolve_segment(segments: list[Segment], selector: str) -> Segment | None:
    if not segments:
        return None
    if selector == "last":
        return segments[-1]
    try:
        wanted = int(selector)
    except ValueError:
        return None
    for segment in segments:
        if segment.number == wanted:
            return segment
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", help="Codex rollout JSONL path")
    parser.add_argument("--from-line", type=int, default=None, help="Summarize rows from this line onwards (capped by --max-events). Overrides --tail.")
    parser.add_argument("--tail", type=int, default=120, help="Last N rows (default 120). Use --tail 0 with --max-events for head mode (first N rows).")
    parser.add_argument("--limit", type=int, default=700, help="Max chars per event; longer content is truncated with '...'. Independent axis from --max-events (which caps event count).")
    parser.add_argument("--max-events", type=int, default=160, help="Event count cap for --from-line and --tail 0 modes. --tail >0 ignores this.")
    parser.add_argument("--segments", action="store_true", help="List lineage (fork ancestry) and activity segments instead of events. Run this FIRST on long rollouts to find which working session you mean.")
    parser.add_argument("--segment", default=None, metavar="N|last", help="Summarize only segment N from --segments numbering ('last' = most recent). Overrides --from-line/--tail.")
    parser.add_argument("--gap-hours", type=float, default=DEFAULT_GAP_HOURS, help=f"Idle gap that starts a new segment (default {DEFAULT_GAP_HOURS:g}h).")
    args = parser.parse_args()

    path = Path(args.rollout).expanduser()
    rows = load_rows(path)

    if args.segments:
        print_segments(path, rows, args.gap_hours)
        return 0

    if args.segment is not None:
        segment = resolve_segment(build_segments(rows, args.gap_hours), args.segment)
        if segment is None:
            print(f"segment {args.segment!r} not found; run --segments to list")
            return 1
        selected = segment.rows[: args.max_events]
    else:
        selected = select_rows(rows, args.from_line, args.tail, args.max_events)

    for line_number, obj in selected:
        text = event_text(line_number, obj, args.limit)
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
