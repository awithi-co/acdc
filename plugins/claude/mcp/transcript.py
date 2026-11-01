"""Bounded JSONL reads of native transcripts. Unreadable lines are skipped."""

from collections import deque
import json

CHUNK_SIZE = 64 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024


class TranscriptError(ValueError):
    """A diagnostic reason that is safe to expose without transcript contents."""


def _decode(raw):
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def forward(stream, size):
    """Read records oldest first, up to a snapshot size."""
    stream.seek(0)
    offset = 0
    while offset < size:
        raw = stream.readline(min(MAX_RECORD_BYTES + 1, size - offset))
        if not raw:
            return
        if len(raw) > MAX_RECORD_BYTES:
            raise TranscriptError("source_record_too_large")
        offset += len(raw)
        record = _decode(raw)
        if record is not None:
            yield record


def reverse(stream, size):
    """Read records newest first, assembling at most one bounded line."""
    cursor = size
    parts = deque()
    length = 0
    while cursor:
        start = max(0, cursor - CHUNK_SIZE)
        stream.seek(start)
        chunk = stream.read(cursor - start)
        cursor = start
        while True:
            split = chunk.rfind(b"\n")
            piece = chunk[split + 1 :] if split >= 0 else chunk
            parts.appendleft(piece)
            length += len(piece)
            if length > MAX_RECORD_BYTES:
                raise TranscriptError("source_record_too_large")
            if split < 0:
                break
            record = _decode(b"".join(parts))
            if record is not None:
                yield record
            parts.clear()
            length = 0
            chunk = chunk[:split]
    if parts:
        record = _decode(b"".join(parts))
        if record is not None:
            yield record
