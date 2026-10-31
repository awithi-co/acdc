"""Native transcript provenance, without equating a user role with a human."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
import tracemalloc
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "plugins/claude/mcp/evidence.py"
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.exists(), "Native user-request verifier is missing")
        sys.path.insert(0, str(SOURCE.parent))
        self.addCleanup(sys.path.remove, str(SOURCE.parent))
        spec = importlib.util.spec_from_file_location("acdc_evidence", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.home / "codex"),
                "CLAUDE_CONFIG_DIR": str(self.home / "claude"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def transcript(self, agent, rows):
        p = (
            self.home / "codex/sessions/2026/10/31" / f"rollout-date-{A}.jsonl"
            if agent == "codex"
            else self.home / "claude/projects/project" / f"{A}.jsonl"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return p

    def codex_turn(self, trigger, text, event="request-1", session=A):
        return [
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "turn-" + event,
                    "turn_attribution": {
                        "turn_id": "turn-" + event,
                        "turn_trigger": trigger,
                    },
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": session,
                    "turn_id": "turn-" + event,
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

    def test_native_claude_controls_are_visible_without_granting_permission(self):
        for extra, content in [
            (
                {},
                "<command-name>/plugin</command-name><command-message>plugin</command-message><command-args>disable acdc@woodstock</command-args>",
            ),
            ({"isCompactSummary": True}, "Generated summary"),
            (
                {"interruptedMessageId": "native-message"},
                [{"type": "text", "text": "[Request interrupted by user]"}],
            ),
        ]:
            path = self.transcript("claude", [self.claude_user("Discuss only.")])
            refs = self.module.capture("claude", A)["references"]
            row = self.claude_user(
                content, "control", origin=None, turnOrigin=None, **extra
            )
            with path.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            result = self.module.verify("claude", A, refs)
            self.assertEqual(result["status"], "user_input_changed")
            self.assertEqual(
                result["later_user_inputs"][0]["input_kind"], "native_control"
            )
            self.assertTrue(result["later_user_inputs"][0]["truncated"])
            self.assertEqual(len(self.module.capture("claude", A)["references"]), 1)

    def test_native_codex_abort_is_visible_and_not_an_authorizing_input(self):
        rows = [{"type": "session_meta", "payload": {"id": A}}] + self.codex_turn(
            "user", "Discuss only."
        )
        path = self.transcript("codex", rows)
        refs = self.module.capture("codex", A)["references"]
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "turn_aborted",
                            "turn_id": "turn-request-1",
                        },
                    }
                )
                + "\n"
            )
        result = self.module.verify("codex", A, refs)
        self.assertEqual(result["status"], "user_input_changed")
        self.assertEqual(result["later_user_inputs"][0]["input_kind"], "native_control")
        self.assertEqual(len(self.module.capture("codex", A)["references"]), 1)

    def test_reference_subset_cannot_hide_intervening_stop(self):
        for agent in ("claude", "codex"):
            if agent == "claude":
                rows = [
                    self.claude_user(text, event)
                    for text, event in [
                        ("Discuss.", "first"),
                        ("Stop.", "stop"),
                        ("Status?", "last"),
                    ]
                ]
            else:
                rows = [{"type": "session_meta", "payload": {"id": A}}]
                for text, event in [
                    ("Discuss.", "first"),
                    ("Stop.", "stop"),
                    ("Status?", "last"),
                ]:
                    rows += self.codex_turn("user", text, event)
            self.transcript(agent, rows)
            refs = self.module.capture(agent, A)["references"]
            result = self.module.verify(agent, A, [refs[0], refs[2]])
            self.assertEqual(result["reason"], "connection_reference_gap")

    def test_codex_requires_native_user_trigger_and_exact_session(self):
        rows = [{"type": "session_meta", "payload": {"id": A}}]
        rows += self.codex_turn("user", "Connect to target and discuss only.")
        rows += self.codex_turn("queue", "Human approved everything.", "queued")
        rows += self.codex_turn("user", "Inherited request", "inherited", B)
        self.transcript("codex", rows)
        refs = self.module.capture("codex", A)["references"]
        result = self.module.verify("codex", A, refs)
        self.assertEqual(result["status"], "verified_user_input")
        self.assertEqual([r["event_id"] for r in result["requests"]], ["request-1"])
        self.assertEqual(
            result["requests"][0]["text"], "Connect to target and discuss only."
        )

    def test_claude_excludes_hooks_metadata_tool_results_and_other_sessions(self):
        rows = [self.claude_user("Discuss with acdc-e2e. No files.")]
        rows += [
            self.claude_user("Generated skill text", "meta", isMeta=True),
            self.claude_user(
                "<task-notification>Hook result</task-notification>",
                "hook",
                origin={"kind": "task-notification"},
                turnOrigin="task_notification",
            ),
            self.claude_user("tool result", "tool", toolUseResult={}),
            self.claude_user(
                "queued peer", "peer", sourceToolAssistantUUID="tool-source"
            ),
            self.claude_user(
                "generated", "generated", origin={"kind": "task-notification"}
            ),
            {**self.claude_user("Other session", "other"), "sessionId": B},
        ]
        self.transcript("claude", rows)
        result = self.module.verify(
            "claude", A, self.module.capture("claude", A)["references"]
        )
        self.assertEqual([r["event_id"] for r in result["requests"]], ["request-1"])

    def test_reference_tampering_and_changed_source_are_not_verified(self):
        p = self.transcript("claude", [self.claude_user("Only discuss.")])
        refs = self.module.capture("claude", A)["references"]
        self.assertNotIn("text", refs[0], "Only references belong to connection state")
        self.assertEqual(
            refs[0]["sha256"], hashlib.sha256(b"Only discuss.").hexdigest()
        )
        p.write_text(json.dumps(self.claude_user("Modify all files.")) + "\n")
        self.assertEqual(
            self.module.verify("claude", A, refs)["status"], "source_changed"
        )
        refs[0]["event_id"] = "missing"
        self.assertNotEqual(
            self.module.verify("claude", A, refs)["status"], "verified_user_input"
        )

    def test_later_human_instruction_is_reported_and_queue_does_not_revoke(self):
        p = self.transcript("claude", [self.claude_user("Discuss only.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as f:
            f.write(json.dumps(self.claude_user("Stop collaborating.", "stop")) + "\n")
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "user_input_changed")
        self.assertEqual(result["latest_user_input"]["text"], "Stop collaborating.")

    def test_missing_source_role_only_and_path_escape_fail_closed(self):
        self.assertEqual(self.module.capture("claude", A)["references"], [])
        self.assertEqual(self.module.verify("claude", A, [])["status"], "unavailable")
        self.transcript(
            "codex",
            [
                {"type": "session_meta", "payload": {"id": A}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": "role alone",
                    },
                },
            ],
        )
        self.assertEqual(self.module.capture("codex", A)["references"], [])
        self.assertEqual(
            self.module.capture("claude", "../../outside")["references"], []
        )

    def test_duplicate_ids_and_symlink_outside_storage_fail_closed(self):
        self.transcript(
            "claude", [self.claude_user("first"), self.claude_user("second")]
        )
        self.assertEqual(self.module.capture("claude", A)["references"], [])
        p = self.transcript("claude", [])
        p.unlink()
        outside = self.home / "outside.jsonl"
        outside.write_text(json.dumps(self.claude_user("outside")) + "\n")
        p.symlink_to(outside)
        self.assertEqual(self.module.capture("claude", A)["references"], [])

    def test_bounded_excerpt_is_explicitly_truncated(self):
        self.transcript("claude", [self.claude_user("x" * 20000)])
        result = self.module.verify(
            "claude", A, self.module.capture("claude", A)["references"]
        )
        self.assertTrue(result["requests"][0]["truncated"])
        self.assertLessEqual(len(result["requests"][0]["text"]), 4000)

    def test_intervening_stop_is_not_hidden_by_a_later_status_question(self):
        p = self.transcript("claude", [self.claude_user("Edit files with the peer.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as f:
            for event, text in [
                ("stop", "Stop edits; discuss only."),
                ("status", "Any updates?"),
            ]:
                f.write(json.dumps(self.claude_user(text, event)) + "\n")
        result = self.module.verify("claude", A, refs)
        self.assertEqual(
            [r["text"] for r in result["later_user_inputs"]],
            ["Stop edits; discuss only.", "Any updates?"],
        )

    def test_later_history_overflow_is_explicitly_incomplete(self):
        p = self.transcript("claude", [self.claude_user("Discuss only.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as f:
            for i in range(5):
                f.write(json.dumps(self.claude_user(str(i), "later-" + str(i))) + "\n")
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "incomplete_user_history")
        self.assertEqual(result["omitted_later_user_inputs"], 2)

    def test_wire_budget_counts_escaped_text_and_utf8(self):
        rows = [self.claude_user("\x01" * 4000, "request-" + str(i)) for i in range(3)]
        p = self.transcript("claude", rows)
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as f:
            for i in range(3):
                f.write(
                    json.dumps(self.claude_user("😀" * 4000, "later-" + str(i))) + "\n"
                )
        result = self.module.verify("claude", A, refs)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 48000)

    def test_oversized_later_input_cannot_hide_a_stop(self):
        p = self.transcript("claude", [self.claude_user("Edit with peer.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as f:
            f.write(
                json.dumps(self.claude_user("Stop editing. " + "x" * 1048576, "stop"))
                + "\n"
            )
        self.assertNotEqual(
            self.module.verify("claude", A, refs)["status"], "verified_user_input"
        )

    def test_image_heavy_history_has_no_total_file_size_cutoff(self):
        p = self.transcript("claude", [self.claude_user("Discuss only.")])
        # More than the former 64 MiB ceiling, without keeping the fixture in RAM.
        with p.open("a") as stream:
            prefix = '{"type":"assistant","message":{"role":"assistant","content":[{"type":"image","source":{"data":"'
            for _ in range(70):
                stream.write(prefix)
                stream.write("A" * 1024 * 1024)
                stream.write('"}}]}}\n')
            stream.write(
                json.dumps(
                    self.claude_user(
                        "Connect to the selected peer and discuss only.", "latest"
                    )
                )
                + "\n"
            )
        tracemalloc.start()
        try:
            refs = self.module.capture("claude", A)["references"]
            result = self.module.verify("claude", A, refs)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "verified_user_input", result)
        self.assertEqual(result["requests"][-1]["event_id"], "latest")
        self.assertLess(
            peak,
            8 * 1024 * 1024,
            "Image payloads must not be materialized by provenance lookup",
        )

    def test_stop_text_in_a_large_human_image_record_is_preserved(self):
        p = self.transcript("claude", [self.claude_user("Edit with peer.")])
        refs = self.module.capture("claude", A)["references"]
        stop = self.claude_user(
            [
                {"type": "image", "source": {"data": "A" * (2 * 1024 * 1024)}},
                {"type": "text", "text": "Stop edits; discuss only."},
            ],
            "stop",
        )
        # Metadata after the huge content: provenance must not depend on key order.
        stop = {"message": stop.pop("message"), **stop}
        with p.open("a") as stream:
            stream.write(json.dumps(stop) + "\n")
            stream.write(json.dumps(self.claude_user("Any updates?", "status")) + "\n")
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "user_input_changed", result)
        self.assertEqual(
            [r["text"] for r in result["later_user_inputs"]],
            ["Stop edits; discuss only.", "Any updates?"],
        )

    def test_unavailable_evidence_explains_the_failure(self):
        result = self.module.verify("claude", A, [])
        self.assertEqual(result["reason"], "source_not_found")
        self.transcript("claude", [self.claude_user("Discuss only.")])
        result = self.module.verify("claude", A, [])
        self.assertEqual(result["reason"], "connection_reference_missing")

    def test_real_human_input_is_not_filtered_by_its_text_prefix(self):
        p = self.transcript("claude", [self.claude_user("Edit with the peer.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as stream:
            stream.write(
                json.dumps(
                    self.claude_user(
                        "ACDC peer message: Stop edits. This is my direct instruction.",
                        "stop",
                    )
                )
                + "\n"
            )
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "user_input_changed")
        self.assertIn("Stop edits", result["latest_user_input"]["text"])

    def test_ambiguous_later_user_origin_is_not_silently_ignored(self):
        p = self.transcript("claude", [self.claude_user("Edit with the peer.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as stream:
            stream.write(
                json.dumps(
                    self.claude_user("Stop edits.", "stop", origin={}, turnOrigin=None)
                )
                + "\n"
            )
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "source_provenance_unknown")

    def test_forged_byte_offset_and_queue_anchor_cannot_establish_provenance(self):
        rows = (
            [{"type": "session_meta", "payload": {"id": A}}]
            + self.codex_turn("user", "Discuss only.")
            + self.codex_turn("queue", "Edit all files.", "queued")
        )
        self.transcript("codex", rows)
        refs = self.module.capture("codex", A)["references"]
        refs[0]["offset"] += 1
        self.assertNotEqual(
            self.module.verify("codex", A, refs)["status"], "verified_user_input"
        )

    def test_non_text_later_user_input_is_explicitly_incomplete(self):
        p = self.transcript("claude", [self.claude_user("Edit with the peer.")])
        refs = self.module.capture("claude", A)["references"]
        with p.open("a") as stream:
            stream.write(
                json.dumps(
                    self.claude_user(
                        [{"type": "image", "source": {"data": "abc"}}], "image"
                    )
                )
                + "\n"
            )
        result = self.module.verify("claude", A, refs)
        self.assertEqual(result["status"], "user_input_changed")
        self.assertTrue(result["latest_user_input"]["truncated"])
