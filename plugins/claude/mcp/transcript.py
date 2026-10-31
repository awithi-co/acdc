"""Bounded JSONL record access for native transcripts.

Byte offsets are hints, not authority: callers must still validate each record's
native identity, origin and text hash. No file-wide size limit or second index.
"""

from collections import deque
from dataclasses import dataclass
import json

CHUNK_SIZE = 64 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024


class TranscriptError(ValueError):
    """A diagnostic reason that is safe to expose without transcript contents."""


@dataclass(frozen=True)
class Record:
    offset: int
    end: int
    value: dict


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TranscriptError("source_duplicate_metadata")
        result[key] = value
    return result


def _decode(raw, offset, end, unfinished_tail=False):
    if not raw.strip():
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        reason = "source_incomplete" if unfinished_tail else "source_invalid_json"
        raise TranscriptError(reason) from error
    if not isinstance(value, dict):
        raise TranscriptError("source_invalid_json")
    return Record(offset, end, value)


def at(stream, offset, size):
    """Read exactly one record at a validated line boundary in a snapshot."""
    if type(offset) is not int or not 0 <= offset < size:
        raise TranscriptError("source_invalid_offset")
    if offset:
        stream.seek(offset - 1)
        if stream.read(1) != b"\n":
            raise TranscriptError("source_invalid_offset")
    stream.seek(offset)
    raw = stream.readline(min(MAX_RECORD_BYTES + 1, size - offset))
    if len(raw) > MAX_RECORD_BYTES:
        raise TranscriptError("source_record_too_large")
    if not raw:
        raise TranscriptError("source_changed_during_read")
    return _decode(raw, offset, stream.tell(), not raw.endswith(b"\n"))


def forward(stream, size, start=0):
    offset = start
    while offset < size:
        record = at(stream, offset, size)
        next_offset = stream.tell()
        if record is not None:
            yield record
        offset = next_offset


def reverse(stream, size):
    """Read newest records first, assembling at most one bounded line."""
    cursor = size
    end = size
    parts = deque()
    length = 0
    while cursor:
        start = max(0, cursor - CHUNK_SIZE)
        stream.seek(start)
        chunk = stream.read(cursor - start)
        if len(chunk) != cursor - start:
            raise TranscriptError("source_changed_during_read")
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
            offset = start + split + 1
            raw = b"".join(parts)
            record = _decode(raw, offset, end, end == size)
            if record is not None:
                yield record
            end = offset
            parts.clear()
            length = 0
            chunk = chunk[:split]
    if parts:
        record = _decode(b"".join(parts), 0, end, end == size)
        if record is not None:
            yield record
