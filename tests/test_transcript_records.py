"""Bounded native JSONL reads that skip what they cannot decode."""

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
    values = [
        {"text": '가 😀 \\ \n quote "'},
        {"ignored": "x" * 1000},
        {"type": "user"},
    ]

    def data(self):
        return b"".join(
            json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in self.values
        )

    def test_both_directions_preserve_unicode_across_chunk_boundaries(self):
        data = self.data()
        with patch.object(transcript, "CHUNK_SIZE", 7):
            self.assertEqual(
                list(transcript.reverse(io.BytesIO(data), len(data))), self.values[::-1]
            )
        self.assertEqual(
            list(transcript.forward(io.BytesIO(data), len(data))), self.values
        )

    def test_record_limit_is_explicit_in_both_directions(self):
        data = json.dumps({"text": "x" * 100}).encode() + b"\n"
        with patch.object(transcript, "MAX_RECORD_BYTES", 20):
            for read in (transcript.forward, transcript.reverse):
                with self.assertRaisesRegex(
                    transcript.TranscriptError, "source_record_too_large"
                ):
                    list(read(io.BytesIO(data), len(data)))

    def test_unreadable_lines_are_skipped(self):
        data = (
            b'{"first": 1}\n\nnot json\n[1, 2]\n{"dup": 1, "dup": 2}\n{"partial": "wri'
        )
        expected = [{"first": 1}, {"dup": 2}]
        self.assertEqual(
            list(transcript.forward(io.BytesIO(data), len(data))), expected
        )
        self.assertEqual(
            list(transcript.reverse(io.BytesIO(data), len(data))), expected[::-1]
        )

    def test_snapshot_ignores_later_appended_bytes(self):
        data = b'{"a": 1}\n{"b": 2}\n'
        snapshot = len(b'{"a": 1}\n')
        for read in (transcript.forward, transcript.reverse):
            self.assertEqual(list(read(io.BytesIO(data), snapshot)), [{"a": 1}])

    def test_complete_unterminated_final_record_is_read(self):
        data = b'{"a": 1}\n{"b": 2}'
        self.assertEqual(
            list(transcript.forward(io.BytesIO(data), len(data))), [{"a": 1}, {"b": 2}]
        )


if __name__ == "__main__":
    unittest.main()
