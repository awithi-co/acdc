"""Byte boundaries, bounded records and truthful failures in native JSONL reads."""

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/claude/mcp"))
import transcript

sys.path.pop(0)


class TranscriptRecordTests(unittest.TestCase):
    def fixture(self):
        values = [
            {"text": '가 😀 \\ \n quote "'},
            {"ignored": "x" * 1000},
            {"type": "user"},
        ]
        lines = [json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in values]
        return values, lines, b"".join(lines)

    def test_reverse_preserves_unicode_and_offsets_across_chunk_boundaries(self):
        values, lines, data = self.fixture()
        expected_offsets = [0, len(lines[0]), len(lines[0]) + len(lines[1])]
        for chunk in [1, 7, 64, 1024]:
            with (
                self.subTest(chunk=chunk),
                patch.object(transcript, "CHUNK_SIZE", chunk),
            ):
                rows = list(transcript.reverse(io.BytesIO(data), len(data)))
                self.assertEqual([r.value for r in rows], list(reversed(values)))
                self.assertEqual(
                    [r.offset for r in rows], list(reversed(expected_offsets))
                )
                for row in rows:
                    self.assertEqual(
                        transcript.at(io.BytesIO(data), row.offset, len(data)).value,
                        row.value,
                    )

    def test_forward_starts_at_validated_byte_boundary(self):
        values, lines, data = self.fixture()
        rows = list(transcript.forward(io.BytesIO(data), len(data), len(lines[0])))
        self.assertEqual([r.value for r in rows], values[1:])
        self.assertEqual(rows[-1].end, len(data))

    def test_forged_offsets_cannot_point_inside_a_string_or_object(self):
        data = b'{"text":"{\\"type\\":\\"user\\"}"}\n'
        for offset in [True, -1, len(data), 8, 10]:
            with (
                self.subTest(offset=offset),
                self.assertRaisesRegex(
                    transcript.TranscriptError, "source_invalid_offset"
                ),
            ):
                transcript.at(io.BytesIO(data), offset, len(data))

    def test_record_limit_is_explicit_in_both_directions(self):
        data = json.dumps({"text": "x" * 100}).encode() + b"\n"
        with patch.object(transcript, "MAX_RECORD_BYTES", 64):
            for reader in [transcript.forward, transcript.reverse]:
                with (
                    self.subTest(reader=reader.__name__),
                    self.assertRaisesRegex(
                        transcript.TranscriptError, "source_record_too_large"
                    ),
                ):
                    list(reader(io.BytesIO(data), len(data)))

    def test_partial_tail_is_not_ignored_as_verified_history(self):
        data = b'{}\n{"type":"user"'
        for reader in [transcript.forward, transcript.reverse]:
            with (
                self.subTest(reader=reader.__name__),
                self.assertRaisesRegex(transcript.TranscriptError, "source_incomplete"),
            ):
                list(reader(io.BytesIO(data), len(data)))

    def test_duplicate_keys_and_adjacent_values_fail_closed(self):
        for data in [b'{"origin":1,"origin":2}\n', b"{} {}\n"]:
            for reader in [transcript.forward, transcript.reverse]:
                with (
                    self.subTest(data=data, reader=reader.__name__),
                    self.assertRaises(transcript.TranscriptError),
                ):
                    list(reader(io.BytesIO(data), len(data)))

    def test_snapshot_does_not_read_later_appended_bytes(self):
        data = b"{}\n" + b'{"unfinished":'
        self.assertEqual(
            [r.value for r in transcript.forward(io.BytesIO(data), 3)], [{}]
        )

    def test_blank_lines_and_complete_unterminated_record(self):
        data = b'\n{}\n\n{"type":"user"}'
        self.assertEqual(
            [r.value for r in transcript.forward(io.BytesIO(data), len(data))],
            [{}, {"type": "user"}],
        )
        self.assertEqual(
            [r.value for r in transcript.reverse(io.BytesIO(data), len(data))],
            [{"type": "user"}, {}],
        )
