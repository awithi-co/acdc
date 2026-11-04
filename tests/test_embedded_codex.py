"""Codex CLIs without the shared daemon (--no-daemon): discovery from open rollouts."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from process_support import SOCKET_TMP

SOURCE = Path(__file__).parents[1] / "plugins/claude/mcp/session.py"
THREAD = "01a1a033-1c52-76b0-9c43-f13046c16784"
SUBAGENT = "01a1a033-5352-7b70-8e06-20ef91fd4fce"


def write_rollout(directory, thread_id, thread_source="user", source="cli", text="fix the flaky test"):
    path = Path(directory) / "sessions/2026/11/03" / f"rollout-2026-11-03T15-00-00-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "session_meta", "payload": {"id": thread_id, "cwd": str(directory),
                                             "source": source, "thread_source": thread_source}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1",
                                          "turn_attribution": {"turn_trigger": "user"}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": thread_id, "turn_id": "t1",
                                          "item": {"type": "UserMessage",
                                                   "content": [{"type": "input_text", "text": text}]}}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


# An owner stand-in: holds the given files open like a --no-daemon Codex CLI.
HOLDER = (
    "import sys,time\n"
    "files=[open(p) for p in sys.argv[1:] if p!='--no-daemon']\n"
    "print('ready',flush=True)\n"
    "time.sleep(60)\n"
)


class EmbeddedCodexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.path.insert(0, str(SOURCE.parent))
        self.addCleanup(sys.path.remove, str(SOURCE.parent))
        spec = importlib.util.spec_from_file_location("acdc_session", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.native = sys.modules["native_codex"]
        self.temp = tempfile.TemporaryDirectory(dir=SOCKET_TMP)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.relay = self.home / "r"
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.home), "ACDC_RELAY_DIR": str(self.relay)})
        env.start()
        self.addCleanup(env.stop)

    def holder(self, *args):
        process = subprocess.Popen([sys.executable, "-c", HOLDER, *map(str, args)],
                                   stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (process.kill(), process.wait()))
        self.assertEqual(process.stdout.readline().strip(), "ready")
        return process

    def test_open_files_lists_a_held_file_without_spawning_processes(self):
        path = write_rollout(self.home, THREAD)
        with open(path):
            with patch.object(subprocess, "run", side_effect=AssertionError("spawned")):
                files = sys.modules["lifecycle"].open_files(os.getpid())
                self.assertIn(str(path), [str(Path(f).resolve()) for f in files])
                self.assertTrue(sys.modules["lifecycle"].alive(os.getpid()))

    def test_loaded_threads_come_from_held_rollouts_with_name_and_preview(self):
        user = write_rollout(self.home, THREAD)
        sub = write_rollout(self.home, SUBAGENT, "subagent", {"subagent": {}})
        (self.home / "session_index.jsonl").write_text(
            json.dumps({"id": THREAD, "thread_name": "old"}) + "\n"
            + json.dumps({"id": THREAD, "thread_name": "Flaky test"}) + "\n"
        )
        owner = self.holder(user, sub)
        threads = self.native.EmbeddedThreads(owner.pid)
        loaded = threads.loaded()
        self.assertEqual(set(loaded), {THREAD, SUBAGENT})
        self.assertTrue(self.module.eligible_codex_thread(loaded[THREAD]))
        self.assertFalse(self.module.eligible_codex_thread(loaded[SUBAGENT]))
        self.assertEqual(threads.metadata(loaded[THREAD]),
                         {"name": "Flaky test", "preview": "fix the flaky test"})
        self.assertTrue(threads.holds(THREAD))
        self.assertTrue(self.native.embedded_cli(self.holder("--no-daemon").pid))
        self.assertFalse(self.native.embedded_cli(owner.pid))

    async def test_closed_recipient_is_refused_before_queueing(self):
        queued = []

        async def queue(thread_id, text):
            queued.append(thread_id)

        owner = self.holder(write_rollout(self.home, THREAD))
        relay = self.module.SessionRelay(self.relay, "codex", self.home, THREAD, owner.pid, embedded=True)
        relay.connected = "claude-peer"
        sender = {"id": "claude-peer", "agent": "claude", "name": "peer"}
        with patch.object(self.module, "queue_codex", queue):
            self.assertEqual((await relay.receive(sender, "hi"))["status"], "queued")
            # The CLI moved to another thread: this one is no longer loaded.
            relay.thread_id = SUBAGENT
            with self.assertRaisesRegex(ValueError, "closed"):
                await relay.receive(sender, "hi")
            relay.thread_id = THREAD
            owner.kill()
            owner.wait()
            with self.assertRaisesRegex(ValueError, "closed"):
                await relay.receive(sender, "hi")
        self.assertEqual(queued, [THREAD])

    def discover(self, owner):
        """Run real discovery against a counting fake codex; return its daemon probes."""
        bindir = self.home / "bin"
        bindir.mkdir(exist_ok=True)
        log = self.home / "codex-calls.log"
        fake = bindir / "codex"
        fake.write_text(f"#!/bin/sh\necho \"$*\" >> {log}\nexit 1\n")
        fake.chmod(0o700)
        env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
        process = subprocess.Popen(
            [sys.executable, str(SOURCE), "discover", "--agent", "codex",
             "--data", json.dumps({"owner_pid": owner})],
            env=env, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (process.poll() is None and process.kill(), process.wait()))
        return process, log

    def calls(self, log):
        return log.read_text().splitlines() if log.exists() else []

    async def test_embedded_cli_never_probes_the_daemon_and_registers_its_thread(self):
        path = write_rollout(self.home, THREAD)
        owner = self.holder("--no-daemon", path)
        process, log = self.discover(owner.pid)
        client = sys.modules["relay"].Relay(self.relay)
        peer = None
        for _ in range(300):
            peer = next((p for p in await client.peers() if p.get("thread_id") == THREAD
                         and p.get("preview")), None)
            if peer:
                break
            await asyncio.sleep(0.05)
        self.assertIsNotNone(peer, "embedded thread was not registered with its preview")
        self.assertEqual(peer["preview"], "fix the flaky test")
        await asyncio.sleep(3)
        self.assertEqual(self.calls(log), [])
        record = self.relay / f"codex-{THREAD}-{owner.pid}.json"
        owner.kill()
        owner.wait()
        for _ in range(300):
            if process.poll() is not None and not record.exists():
                break
            await asyncio.sleep(0.05)
        self.assertIsNotNone(process.poll(), "discovery outlived its CLI")
        self.assertFalse(record.exists(), "broker outlived its CLI")

    async def first_probe_after(self, log, seconds):
        for _ in range(int(seconds / 0.05)):
            if self.calls(log):
                return True
            await asyncio.sleep(0.05)
        return False

    async def test_daemon_is_probed_only_while_its_socket_exists(self):
        owner = self.holder()
        process, log = self.discover(owner.pid)
        await asyncio.sleep(5)
        self.assertEqual(self.calls(log), [], "probed a daemon that has no socket")
        socket = self.home / "app-server-control" / "app-server-control.sock"
        socket.parent.mkdir()
        socket.touch()
        # The socket is noticed on the next scan, not after a backed-off delay.
        self.assertTrue(await self.first_probe_after(log, 3), "appearing daemon socket was not probed")
        await asyncio.sleep(8)
        probes = [c for c in self.calls(log) if c.startswith("app-server daemon version")]
        # A stale socket is re-probed with backoff (~0, 1, 3, 7 s), not every second.
        self.assertGreaterEqual(len(probes), 2)
        self.assertLessEqual(len(probes), 4)

    async def test_embedded_rename_reaches_the_listed_peer(self):
        path = write_rollout(self.home, THREAD)
        owner = self.holder("--no-daemon", path)
        process, log = self.discover(owner.pid)
        client = sys.modules["relay"].Relay(self.relay)

        async def name():
            for _ in range(100):
                peer = next((p for p in await client.peers() if p.get("thread_id") == THREAD), None)
                if peer and peer.get("native_name"):
                    return peer["native_name"]
                await asyncio.sleep(0.05)
            return None

        index = self.home / "session_index.jsonl"
        for title in ("first", "renamed"):
            with index.open("a") as rows:
                rows.write(json.dumps({"id": THREAD, "thread_name": title}) + "\n")
            for _ in range(80):
                if await name() == title:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(await name(), title)

if __name__ == "__main__":
    unittest.main()
