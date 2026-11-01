"""Opt-in real Claude + Codex E2E; uses paid model tokens and disposable homes.

Only the initiating Claude receives a user request; the receiving Codex must carry
on the discussion without asking its own user. Existing sessions are not touched.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from native_auto_connect_probe import Terminal
from live_probe_support import (
    completed_turns,
    recent_activity,
)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugins/claude/mcp"))
from relay import Relay  # noqa: E402


def reply_ids(home):
    found = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("status") == "accepted" and value.get("message_id"):
                found.add(value["message_id"])
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                walk(json.loads(value))
            except ValueError:
                pass

    for p in home.glob("sessions/**/rollout-*.jsonl"):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            v = r.get("payload", {})
            if r.get("type") == "response_item" and v.get("type") in (
                "custom_tool_call_output",
                "function_call_output",
            ):
                walk(v)
    return found


async def run():
    if os.environ.get("ACDC_RUN_PAID_TESTS") != "1":
        raise SystemExit(
            "Opt-in required: ACDC_RUN_PAID_TESTS=1 (uses paid Claude and Codex tokens)"
        )
    marketplace = os.environ.get("ACDC_TEST_MARKETPLACE", str(REPO))
    plugin = os.environ.get("ACDC_TEST_PLUGIN", "acdc@acdc")
    codex_user = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    claude_user = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    )
    assert (codex_user / "auth.json").is_file() and (
        claude_user / ".credentials.json"
    ).is_file(), "Local CLI authentication required"
    with tempfile.TemporaryDirectory(prefix="acdc-live-pair-") as directory:
        root = Path(directory)
        home = root / "home"
        ch = home / ".codex"
        ah = home / ".claude"
        ch.mkdir(parents=True, mode=0o700)
        ah.mkdir(mode=0o700)
        for source, target in [
            (codex_user / "auth.json", ch / "auth.json"),
            (claude_user / ".credentials.json", ah / ".credentials.json"),
        ]:
            shutil.copyfile(source, target)
            target.chmod(0o600)
        (ch / "config.toml").write_text(
            'model="gpt-6.1-sol"\nmodel_reasoning_effort="low"\n'
            + "[projects."
            + json.dumps(directory)
            + ']\ntrust_level="trusted"\n'
            + "[plugins."
            + json.dumps(plugin)
            + '.mcp_servers.relay]\ndefault_tools_approval_mode="approve"\n'
        )
        (ah / ".claude.json").write_text(
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "theme": "dark",
                    "bypassPermissionsModeAccepted": True,
                    "projects": {directory: {"hasTrustDialogAccepted": True}},
                }
            )
        )
        env = {
            k: os.environ[k]
            for k in (
                "PATH",
                "LANG",
                "LC_ALL",
                "SHELL",
                "USER",
                "LOGNAME",
                "SSL_CERT_FILE",
            )
            if k in os.environ
        }
        env.update(
            HOME=str(home),
            CODEX_HOME=str(ch),
            CLAUDE_CONFIG_DIR=str(ah),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_STATE_HOME=str(root / "state"),
            XDG_CACHE_HOME=str(root / "cache"),
            UV_CACHE_DIR=str(Path.home() / ".cache/uv"),
            ACDC_RELAY_DIR=str(root / "relay"),
            TERM="xterm-256color",
            NO_COLOR="1",
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        )
        installs = [
            ["codex", "plugin", "marketplace", "add", marketplace, "--json"],
            ["codex", "plugin", "add", plugin, "--json"],
            ["claude", "plugin", "marketplace", "add", marketplace],
            ["claude", "plugin", "install", plugin, "--scope", "user"],
        ]
        for command in installs:
            result = subprocess.run(
                command,
                env=env,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=40,
            )
            assert result.returncode == 0, (
                "Install failed: " + result.stdout[-1500:] + result.stderr[-1000:]
            )
        terminals = []
        try:
            codex = Terminal(
                [
                    "codex",
                    "-C",
                    directory,
                    "-a",
                    "never",
                    "-s",
                    "read-only",
                    "--no-alt-screen",
                    "ACDC 연결 테스트야. 작업을 작게 나누는 기준에 관해 이야기하자. 좋은 분할 기준 세 가지를 짧게 설명해줘.",
                ],
                env,
                directory,
            )
            terminals.append(codex)
            client = Relay(root / "relay")
            target = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                codex.pump()
                target = next(
                    (
                        p
                        for p in await client.peers()
                        if p.get("agent") == "codex"
                    ),
                    None,
                )
                if target and completed_turns(ch):
                    break
                await asyncio.sleep(0.05)
            assert target and completed_turns(ch), (
                "Codex did not become ready"
            )
            from websockets.asyncio.client import unix_connect

            status = json.loads(
                subprocess.run(
                    ["codex", "app-server", "daemon", "version"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout
            )
            async with unix_connect(status["socketPath"], open_timeout=5) as ws:

                async def rpc(request_id, method, params):
                    await ws.send(
                        json.dumps(
                            {"id": request_id, "method": method, "params": params}
                        )
                    )
                    async with asyncio.timeout(10):
                        while True:
                            response = json.loads(await ws.recv())
                            if response.get("id") == request_id:
                                assert "error" not in response, response
                                return

                await rpc(
                    1,
                    "initialize",
                    {
                        "clientInfo": {"name": "acdc_live_pair_probe", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await ws.send('{"method":"initialized"}')
                await rpc(
                    2,
                    "thread/name/set",
                    {"threadId": target["thread_id"], "name": "acdc-live-evidence"},
                )
            for _ in range(100):
                codex.pump()
                if (await client.request(target["id"], {"op": "describe"}))[
                    "name"
                ] == "acdc-live-evidence":
                    break
                await asyncio.sleep(0.05)
            current = await client.request(target["id"], {"op": "describe"})
            assert current["name"] == "acdc-live-evidence", (
                "Native discovery did not converge after rename: " + json.dumps(current)
            )
            prompt = (
                "ACDC로 현재 열린 acdc-live-evidence라는 Codex 세션과 작업을 작게 나누면 오히려 손해인 경우를 "
                "두 차례 왕복 토론해줘. 이 이름으로 상대를 지정한 것이니 다시 선택을 묻지 마. "
                "상대의 실제 답변을 받은 뒤 그 내용을 반영한 후속 질문을 보내고, 두 번째 답변을 받으면 "
                "연결을 해제하고 요약해줘."
            )
            claude = Terminal(
                [
                    "claude",
                    "--model",
                    "sonnet",
                    "--dangerously-skip-permissions",
                    prompt,
                ],
                env,
                directory,
            )
            terminals.append(claude)
            print("both_real_clients_open_initiator_only_user_request", flush=True)
            deadline = time.monotonic() + 240
            last_count = 0
            while time.monotonic() < deadline:
                for terminal in terminals:
                    terminal.pump()
                ids = reply_ids(ch)
                if len(ids) != last_count:
                    print("actual_codex_peer_replies", len(ids), flush=True)
                    last_count = len(ids)
                current = await client.request(target["id"], {"op": "describe"})
                if (
                    len(ids) >= 2
                    and not current.get("connected_to")
                ):
                    native = "\n".join(
                        p.read_text() for p in ah.glob("projects/**/*.jsonl")
                    )
                    assert all(mid in native for mid in ids), (
                        "A Codex send was not received by Claude"
                    )
                    print("two_rounds_message_ids_matched_native_records", flush=True)
                    print("initiator_disconnected_and_summarized", flush=True)
                    print("live_pair_evidence_e2e_passed", flush=True)
                    print(
                        "audit",
                        json.dumps(
                            {
                                "codex_session_id": target["thread_id"],
                                "reply_message_ids": sorted(ids),
                            }
                        ),
                        flush=True,
                    )
                    return
                if claude.process.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            raise AssertionError(
                "Real pair did not finish. Claude: "
                + claude_final(ah)
                + "\nCodex: "
                + recent_activity(ch)
            )
        finally:
            for terminal in reversed(terminals):
                terminal.close()
            try:
                subprocess.run(
                    ["codex", "app-server", "daemon", "stop"],
                    env=env,
                    capture_output=True,
                    timeout=15,
                )
            except subprocess.TimeoutExpired:
                pass
            for proc in Path("/proc").glob("[0-9]*"):
                try:
                    args = (proc / "cmdline").read_bytes().split(b"\0")
                    if args and args[0].startswith(str(ch).encode() + b"/"):
                        os.kill(int(proc.name), signal.SIGTERM)
                except (OSError, ValueError):
                    pass


if __name__ == "__main__":
    asyncio.run(run())
