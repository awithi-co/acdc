"""Read-only observations shared by the opt-in live E2E."""

import json


def completed_turns(home):
    count = 0
    for path in home.glob("sessions/**/rollout-*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (
                row.get("type") == "event_msg"
                and row.get("payload", {}).get("type") == "task_complete"
            ):
                count += 1
    return count


def recent_activity(home):
    rows = []
    for path in home.glob("sessions/**/rollout-*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload", {})
            if row.get("type") == "response_item" and (
                payload.get("type")
                in (
                    "function_call",
                    "function_call_output",
                    "custom_tool_call",
                    "custom_tool_call_output",
                )
                or payload.get("role") == "assistant"
            ):
                rows.append(str(payload)[:1600])
    return "\n".join(rows[-12:])


def model_observed_verified_evidence(home):
    for path in home.glob("sessions/**/rollout-*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload", {})
            if (
                row.get("type") == "response_item"
                and payload.get("type")
                in ("custom_tool_call_output", "function_call_output")
                and "verified_user_input" in json.dumps(payload)
            ):
                return True
    return False
