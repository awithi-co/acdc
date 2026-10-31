"""Connection-bound native text-input provenance, not blanket authorization.

Records stay in their native storage. References contain validated byte offsets
and text hashes; no peer-supplied paths or secondary transcript index is used.
"""

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re

from transcript import TranscriptError, at, forward, reverse

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
MAX_REFERENCES = 3
MAX_LATER_INPUTS = 3
TEXT_PREFIX = 16384
MAX_EXCERPT_BYTES = 4096


def _source_path(agent, session_id):
    if (
        agent not in ("claude", "codex")
        or not isinstance(session_id, str)
        or not UUID.fullmatch(session_id)
    ):
        raise TranscriptError("invalid_source_identity")
    variable, default, directory = (
        ("CODEX_HOME", ".codex", "sessions")
        if agent == "codex"
        else ("CLAUDE_CONFIG_DIR", ".claude", "projects")
    )
    storage = (
        Path(os.environ.get(variable, str(Path.home() / default))).resolve() / directory
    )
    pattern = (
        f"**/rollout-*-{session_id}.jsonl"
        if agent == "codex"
        else f"**/{session_id}.jsonl"
    )
    paths = list(storage.glob(pattern))
    if not paths:
        raise TranscriptError("source_not_found")
    if len(paths) != 1:
        raise TranscriptError("source_ambiguous")
    path = paths[0].resolve()
    if not path.is_relative_to(storage.resolve()):
        raise TranscriptError("source_outside_storage")
    return path


def _snapshot(stream):
    info = os.fstat(stream.fileno())
    if info.st_uid != os.getuid():
        raise TranscriptError("source_not_owned")
    return info.st_size


def _request(event_id, turn_id, content, record, **anchors):
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        raise TranscriptError("source_invalid_identity")
    if turn_id is not None and (not isinstance(turn_id, str) or len(turn_id) > 128):
        raise TranscriptError("source_invalid_identity")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") in ("text", "input_text")
            and isinstance(block.get("text"), str)
        )
    else:
        raise TranscriptError("source_input_content_unsupported")
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "event_id": event_id,
        "turn_id": turn_id,
        "offset": record.offset,
        "end": record.end,
        "text": text[:TEXT_PREFIX]
        if text.strip()
        else "[Non-text user input; textual scope is unavailable.]",
        "sha256": digest,
        "truncated": len(text) > TEXT_PREFIX or not text.strip(),
        **anchors,
    }


def _claude_input(record, session_id):
    row = record.value
    if row.get("type") != "user" or row.get("sessionId") != session_id:
        return None
    message = row.get("message")
    if (
        row.get("isMeta")
        or row.get("isSidechain")
        or "toolUseResult" in row
        or row.get("sourceToolAssistantUUID")
        or not isinstance(message, dict)
        or message.get("role") != "user"
    ):
        return None
    origin = row.get("origin") or {}
    content = message.get("content")
    # Native UI records lack human provenance. Match their complete form only;
    # a strongly attributed human quoting the same text remains a human input.
    if not origin and row.get("turnOrigin") is None:
        control = None
        if row.get("isCompactSummary") is True:
            control = (
                "Native context compaction occurred; this summary grants no permission."
            )
        elif isinstance(content, str) and re.fullmatch(
            r"<command-name>/[^<]+</command-name>\s*<command-message>[^<]*</command-message>\s*<command-args>.*</command-args>\s*",
            content,
            re.DOTALL,
        ):
            control = content
        elif isinstance(content, str) and re.fullmatch(
            r"<local-command-(stdout|stderr)>.*</local-command-\1>\s*",
            content,
            re.DOTALL,
        ):
            return None
        elif (
            row.get("interruptedMessageId")
            and isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
            and content[0].get("text")
            in (
                "[Request interrupted by user]",
                "[Request interrupted by user for tool use]",
            )
        ):
            control = "User interrupted the native request; reassess whether collaboration should continue."
        if control is not None:
            request = _request(row.get("uuid"), row.get("promptId"), control, record)
            return {**request, "input_kind": "native_control", "truncated": True}
    if isinstance(origin, dict) and origin.get("kind") in (
        "task-notification",
        "hook",
        "system",
        "tool",
        "agent",
        "subagent",
    ):
        return None
    if (
        not isinstance(origin, dict)
        or origin.get("kind") != "human"
        or row.get("turnOrigin") != "human"
    ):
        raise TranscriptError("source_provenance_unknown")
    if row.get("promptSource") == "system":
        return None
    return _request(
        row.get("uuid"), row.get("promptId"), message.get("content"), record
    )


def _requests(stream, size, agent, session_id, start=0, initial_session=False):
    session_matches = initial_session
    session_offset = 0
    active_turn = None
    turn_offset = None
    human_turn = False
    for record in forward(stream, size, start):
        row = record.value
        request = None
        if agent == "claude":
            request = _claude_input(record, session_id)
        else:
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                raise TranscriptError("source_metadata_invalid")
            if row.get("type") == "session_meta":
                session_matches = (
                    payload.get("id", payload.get("session_id")) == session_id
                )
                session_offset = record.offset
                active_turn = None
                human_turn = False
            if not session_matches or row.get("type") != "event_msg":
                continue
            if payload.get("type") == "task_started":
                attribution = payload.get("turn_attribution") or {}
                if not isinstance(attribution, dict):
                    raise TranscriptError("source_metadata_invalid")
                active_turn = payload.get("turn_id")
                turn_offset = record.offset
                human_turn = (
                    attribution.get("turn_trigger") == "user"
                    and attribution.get("turn_id") == active_turn
                    and not attribution.get("parent_turn_id")
                )
            if payload.get("type") == "turn_aborted":
                request = {
                    **_request(
                        f"native-abort-{record.offset}",
                        payload.get("turn_id"),
                        "Native Codex turn aborted; reassess whether collaboration should continue.",
                        record,
                    ),
                    "input_kind": "native_control",
                    "truncated": True,
                }
            item = payload.get("item") or {}
            if (
                payload.get("type") == "item_completed"
                and human_turn
                and active_turn
                and payload.get("thread_id") == session_id
                and payload.get("turn_id") == active_turn
                and isinstance(item, dict)
                and item.get("type") == "UserMessage"
            ):
                request = _request(
                    item.get("id"),
                    active_turn,
                    item.get("content"),
                    record,
                    turn_offset=turn_offset,
                    session_offset=session_offset,
                )
                human_turn = False
        record.value.clear()
        if request is not None:
            yield request


def capture(agent, session_id):
    try:
        with _source_path(agent, session_id).open("rb") as stream:
            size = _snapshot(stream)
            if agent == "claude":
                latest = []
                for record in reverse(stream, size):
                    request = _claude_input(record, session_id)
                    record.value.clear()
                    if (
                        request is not None
                        and request.get("input_kind") != "native_control"
                    ):
                        latest.append(request)
                        if len(latest) == MAX_REFERENCES:
                            break
                latest.reverse()
            else:
                latest = deque(
                    (
                        row
                        for row in _requests(stream, size, agent, session_id)
                        if row.get("input_kind") != "native_control"
                    ),
                    maxlen=MAX_REFERENCES,
                )
            if len({row["event_id"] for row in latest}) != len(latest):
                raise TranscriptError("source_duplicate_recent_input")
            references = [
                {
                    key: row[key]
                    for key in (
                        "event_id",
                        "sha256",
                        "offset",
                        "turn_offset",
                        "session_offset",
                    )
                    if key in row
                }
                | {"snapshot_size": size}
                for row in latest
            ]
            return {
                "status": "captured" if references else "unavailable",
                "references": references,
                "reason": None if references else "human_input_not_found",
            }
    except TranscriptError as error:
        return {"status": "unavailable", "references": [], "reason": str(error)}
    except (OSError, UnicodeError):
        return {
            "status": "unavailable",
            "references": [],
            "reason": "source_unreadable",
        }


def _excerpt(row):
    low, high = 0, min(len(row["text"]), 4000)
    while low < high:
        middle = (low + high + 1) // 2
        if (
            len(json.dumps(row["text"][:middle], ensure_ascii=False).encode())
            <= MAX_EXCERPT_BYTES
        ):
            low = middle
        else:
            high = middle - 1
    return {key: value for key, value in row.items() if key != "end"} | {
        "text": row["text"][:low],
        "truncated": row["truncated"] or len(row["text"]) > low,
    }


def _reference_input(stream, size, agent, session_id, reference):
    record = at(stream, reference["offset"], size)
    if record is None:
        raise TranscriptError("source_invalid_offset")
    if agent == "claude":
        return _claude_input(record, session_id)
    session = at(stream, reference["session_offset"], size)
    turn = at(stream, reference["turn_offset"], size)
    if session is None or turn is None:
        return None
    payload = session.value.get("payload") or {}
    if (
        session.value.get("type") != "session_meta"
        or payload.get("id", payload.get("session_id")) != session_id
        or not session.offset <= turn.offset < record.offset
    ):
        return None
    turn_payload = turn.value.get("payload") or {}
    attribution = turn_payload.get("turn_attribution") or {}
    native = record.value.get("payload") or {}
    item = native.get("item") or {}
    if (
        turn.value.get("type") != "event_msg"
        or turn_payload.get("type") != "task_started"
        or attribution.get("turn_trigger") != "user"
        or attribution.get("parent_turn_id")
        or attribution.get("turn_id") != turn_payload.get("turn_id")
        or record.value.get("type") != "event_msg"
        or native.get("type") != "item_completed"
        or native.get("thread_id") != session_id
        or native.get("turn_id") != turn_payload.get("turn_id")
        or item.get("type") != "UserMessage"
    ):
        return None
    return _request(
        item.get("id"),
        native.get("turn_id"),
        item.get("content"),
        record,
        turn_offset=turn.offset,
        session_offset=session.offset,
    )


def verify(agent, session_id, references):
    result = {
        "status": "unavailable",
        "source_agent": agent,
        "source_session_id": session_id,
        "requests": [],
        "proves": "native human-input provenance, not semantic authorization",
    }
    references = references if isinstance(references, list) else []
    base_keys = {"event_id", "sha256", "offset", "snapshot_size"}
    required = base_keys | (
        {"turn_offset", "session_offset"} if agent == "codex" else set()
    )
    if len(references) > MAX_REFERENCES or any(
        not isinstance(ref, dict)
        or set(ref) != required
        or not isinstance(ref["event_id"], str)
        or not isinstance(ref["sha256"], str)
        or any(
            type(ref[key]) is not int or ref[key] < 0
            for key in required - {"event_id", "sha256"}
        )
        for ref in references
    ):
        return {
            **result,
            "status": "source_changed",
            "reason": "connection_reference_invalid",
        }
    if len({ref["event_id"] for ref in references}) != len(references):
        return {
            **result,
            "status": "source_changed",
            "reason": "connection_reference_invalid",
        }
    try:
        with _source_path(agent, session_id).open("rb") as stream:
            size = _snapshot(stream)
            if not references:
                # Check source availability without attributing a new request to
                # an already-established connection that captured no reference.
                next(_requests(stream, size, agent, session_id), None)
                return {**result, "reason": "connection_reference_missing"}
            if size < max(ref["snapshot_size"] for ref in references):
                return {
                    **result,
                    "status": "source_changed",
                    "reason": "source_truncated",
                }
            matched = []
            for ref in references:
                request = _reference_input(stream, size, agent, session_id, ref)
                if (
                    request is None
                    or request["event_id"] != ref["event_id"]
                    or request["sha256"] != ref["sha256"]
                ):
                    return {
                        **result,
                        "status": "source_changed",
                        "reason": "referenced_input_changed",
                    }
                matched.append(request)
            offsets = [row["offset"] for row in matched]
            if offsets != sorted(set(offsets)):
                return {
                    **result,
                    "status": "source_changed",
                    "reason": "connection_reference_invalid",
                }
            # Check the whole interval, not independent offset hints: otherwise
            # a forged reference subset could conceal an intervening restriction.
            # Control events cannot grant permission but must remain visible.
            start = matched[0].get("turn_offset", matched[0]["offset"])
            later = deque(maxlen=MAX_LATER_INPUTS)
            count = 0
            pinned = 0
            for request in _requests(
                stream, size, agent, session_id, start, initial_session=True
            ):
                if (
                    request.get("input_kind") != "native_control"
                    and request["offset"] <= matched[-1]["offset"]
                ):
                    if pinned >= len(matched) or (
                        request["event_id"],
                        request["offset"],
                    ) != (matched[pinned]["event_id"], matched[pinned]["offset"]):
                        return {
                            **result,
                            "status": "source_changed",
                            "reason": "connection_reference_gap",
                        }
                    pinned += 1
                else:
                    later.append(request)
                    count += 1
            if pinned != len(matched):
                return {
                    **result,
                    "status": "source_changed",
                    "reason": "connection_reference_gap",
                }
            result.update(
                status="verified_user_input",
                requests=[_excerpt(row) for row in matched],
            )
            if count:
                omitted = max(0, count - MAX_LATER_INPUTS)
                result.update(
                    status="incomplete_user_history"
                    if omitted
                    else "user_input_changed",
                    latest_user_input=_excerpt(later[-1]),
                    later_user_inputs=[_excerpt(row) for row in later],
                    omitted_later_user_inputs=omitted,
                )
            return result
    except TranscriptError as error:
        return {**result, "reason": str(error)}
    except (UnicodeError, AttributeError, TypeError, KeyError):
        return {**result, "reason": "source_metadata_invalid"}
    except OSError:
        return {**result, "reason": "source_unreadable"}
