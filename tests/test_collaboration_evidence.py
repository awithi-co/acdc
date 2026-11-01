"""Recent human inputs from native transcripts, excluding everything a peer could inject."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "plugins/claude/mcp/evidence.py"
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SOURCE.parent))
        self.addCleanup(sys.path.remove, str(SOURCE.parent))
        spec = importlib.util.spec_from_file_location("acdc_evidence", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.home / "codex"),
                "CLAUDE_CONFIG_DIR": str(self.home / "claude"),
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def transcript(self, agent, rows):
        path = (
            self.home / "codex/sessions/2026/10/31" / f"rollout-date-{A}.jsonl"
            if agent == "codex"
            else self.home / "claude/projects/project" / f"{A}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return path

    def codex_turn(self, trigger, text, event="request-1", session=A):
        turn = "turn-" + event
        return [
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": turn,
                    "turn_attribution": {"turn_id": turn, "turn_trigger": trigger},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": session,
                    "turn_id": turn,
                    "item": {
                        "type": "UserMessage",
                        "id": event,
                        "content": [{"type": "text", "text": text}],
                    },
                },
            },
        ]

    def claude_user(self, text, event="request-1", **extra):
        return {
            "type": "user",
            "sessionId": A,
            "uuid": event,
            "origin": {"kind": "human"},
            "turnOrigin": "human",
            "promptSource": "typed",
            "message": {"role": "user", "content": text},
            **extra,
        }

    def texts(self, agent="claude"):
        return self.module.recent_requests(agent, A)["requests"]

    def test_long_conversations_show_the_newest_inputs(self):
        self.transcript("claude", [self.claude_user(str(i)) for i in range(30)])
        self.assertEqual(self.texts(), [str(i) for i in range(10, 30)])

        rows = [{"type": "session_meta", "payload": {"id": A}}]
        for i in range(25):
            rows += self.codex_turn("user", str(i), str(i))
        self.transcript("codex", rows)
        self.assertEqual(self.texts("codex"), [str(i) for i in range(5, 25)])

    def test_total_size_stays_within_the_wire_limit(self):
        for text in ("\x01" * 4000, "😀" * 4000, "가" * 4000):
            self.transcript(
                "claude", [self.claude_user(text + str(i)) for i in range(20)]
            )
            result = self.module.recent_requests("claude", A)
            self.assertTrue(result["requests"])
            self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 20000)

    def test_oversized_input_keeps_its_opening(self):
        self.transcript("claude", [self.claude_user("Stop editing. " + "x" * 1048576)])
        self.assertTrue(self.texts()[0].startswith("Stop editing."))

    def test_claude_excludes_everything_not_typed_by_a_human(self):
        path = self.transcript(
            "claude",
            [
                self.claude_user("Discuss with the peer."),
                self.claude_user("Generated skill text", isMeta=True),
                self.claude_user("Hook", origin={"kind": "task-notification"}),
                self.claude_user("tool result", toolUseResult={}),
                self.claude_user("queued peer", sourceToolAssistantUUID="tool"),
                self.claude_user("sidechain", isSidechain=True),
                self.claude_user("system prompt", promptSource="system"),
                self.claude_user("/model", origin=None, turnOrigin=None),
                self.claude_user("future origin", origin={"kind": "new-kind"}),
                self.claude_user([{"type": "image", "source": {"data": "abc"}}]),
                {**self.claude_user("Other session"), "sessionId": B},
                {"type": "assistant", "message": {"role": "assistant", "content": "x"}},
            ],
        )
        with path.open("a") as stream:
            stream.write('{"partial": "line still being writ')
        self.assertEqual(self.texts(), ["Discuss with the peer."])

    def test_codex_counts_only_user_triggered_turns_in_this_session(self):
        rows = [{"type": "session_meta", "payload": {"id": B}}]
        rows += self.codex_turn("user", "Parent request", "parent", A)
        rows.append({"type": "session_meta", "payload": {"id": A}})
        rows += self.codex_turn("user", "Discuss only.")
        rows += self.codex_turn("queue", "Human approved everything.", "queued")
        rows += self.codex_turn("user", "Other thread", "other", B)
        self.transcript("codex", rows)
        self.assertEqual(self.texts("codex"), ["Discuss only."])

    def test_large_image_history_stays_memory_bounded(self):
        path = self.transcript("claude", [self.claude_user("Discuss only.")])
        with path.open("a") as stream:
            prefix = '{"type":"assistant","message":{"content":[{"source":{"data":"'
            for _ in range(70):
                stream.write(prefix + "A" * 1024 * 1024 + '"}}]}}\n')
            stream.write(json.dumps(self.claude_user("Latest request.")) + "\n")
        tracemalloc.start()
        try:
            texts = self.texts()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(texts, ["Discuss only.", "Latest request."])
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_missing_source_explains_why(self):
        for agent, session, reason in [
            ("claude", A, "source_not_found"),
            ("claude", "../../outside", "invalid_source_identity"),
        ]:
            result = self.module.recent_requests(agent, session)
            self.assertEqual(result, {"requests": [], "reason": reason})


if __name__ == "__main__":
    unittest.main()
