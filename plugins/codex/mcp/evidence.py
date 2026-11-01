"""A session's recent human inputs, read directly from its native transcript.

Only attributed human turns count, so hook, queued and peer text never appears here.
"""

from collections import deque
import json
import os
from pathlib import Path

from relay import UUID
from transcript import TranscriptError, forward, reverse

MAX_INPUTS = 20
MAX_TOTAL_BYTES = 16384
MAX_INPUT_BYTES = 4096


def _source_path(agent, session_id):
    if agent not in ("claude", "codex") or not UUID.fullmatch(str(session_id)):
        raise TranscriptError("invalid_source_identity")
    variable, default, directory, pattern = (
        ("CODEX_HOME", ".codex", "sessions", f"**/rollout-*-{session_id}.jsonl")
        if agent == "codex"
        else ("CLAUDE_CONFIG_DIR", ".claude", "projects", f"**/{session_id}.jsonl")
    )
    home = Path(os.environ.get(variable, str(Path.home() / default)))
    paths = list((home / directory).glob(pattern))
    if not paths:
        raise TranscriptError("source_not_found")
    return max(paths, key=lambda path: path.stat().st_mtime)


def _size(text):
    return len(json.dumps(text, ensure_ascii=False).encode())


def _text(content):
    if isinstance(content, list):
        content = "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") in ("text", "input_text")
            and isinstance(block.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        return None
    text = content[:MAX_INPUT_BYTES]
    while _size(text) > MAX_INPUT_BYTES:
        text = text[: len(text) * 3 // 4]
    return text


def _claude_input(row, session_id):
    message = row.get("message")
    origin = row.get("origin")
    if (
        row.get("type") == "user"
        and row.get("sessionId") == session_id
        and isinstance(message, dict)
        and isinstance(origin, dict)
        and origin.get("kind") == "human"
        and row.get("turnOrigin") == "human"
        and row.get("promptSource") != "system"
        and not row.get("isMeta")
        and not row.get("isSidechain")
        and "toolUseResult" not in row
        and not row.get("sourceToolAssistantUUID")
    ):
        return _text(message.get("content"))
    return None


def _codex_inputs(stream, size, session_id):
    """Yield inputs from user-triggered turns; queued turns are not human input."""
    in_session = False
    active_turn = None
    for row in forward(stream, size):
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            # A forked transcript starts with its parent's meta and turns.
            in_session = payload.get("id", payload.get("session_id")) == session_id
            active_turn = None
        if not in_session or row.get("type") != "event_msg":
            continue
        if payload.get("type") == "task_started":
            attribution = payload.get("turn_attribution")
            active_turn = (
                payload.get("turn_id")
                if isinstance(attribution, dict)
                and attribution.get("turn_trigger") == "user"
                and not attribution.get("parent_turn_id")
                else None
            )
        elif payload.get("type") == "item_completed" and active_turn:
            item = payload.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "UserMessage"
                and payload.get("thread_id") == session_id
                and payload.get("turn_id") == active_turn
            ):
                active_turn = None
                text = _text(item.get("content"))
                if text:
                    yield text


def _newest_first(agent, stream, size, session_id):
    if agent == "claude":
        for row in reverse(stream, size):
            text = _claude_input(row, session_id)
            if text:
                yield text
    else:
        yield from reversed(
            deque(_codex_inputs(stream, size, session_id), maxlen=MAX_INPUTS)
        )


def recent_requests(agent, session_id):
    """Return recent human inputs, oldest first, within a fixed size budget."""
    try:
        with _source_path(agent, session_id).open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            requests, total = [], 0
            for text in _newest_first(agent, stream, size, session_id):
                total += _size(text)
                if len(requests) == MAX_INPUTS or total > MAX_TOTAL_BYTES:
                    break
                requests.append(text)
    except TranscriptError as error:
        return {"requests": [], "reason": str(error)}
    except OSError:
        return {"requests": [], "reason": "source_unreadable"}
    return {"requests": requests[::-1]}
