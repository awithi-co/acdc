"""A plugin reload updates inactive runtimes without killing an active pair."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

MCP = Path(__file__).parents[1] / "plugins/claude/mcp"


class RuntimeUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.path.insert(0, str(MCP))
        self.addCleanup(sys.path.remove, str(MCP))
        spec = importlib.util.spec_from_file_location(
            "upgrade_session", MCP / "session.py"
        )
        self.session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.session)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.relay = self.root / "relay"
        self.relay.mkdir(mode=0o700)
        script = self.root / "old/mcp/session.py"
        script.parent.mkdir(parents=True)
        script.write_text("import time\ntime.sleep(120)\n")
        self.old = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "discover",
                "--agent",
                "codex",
                "--data",
                json.dumps({"owner_pid": os.getpid()}),
            ]
        )
        self.addCleanup(self.stop_process, self.old)
        self.marker = self.relay / f"codex-{os.getpid()}.discovery.json"
        self.marker.write_text(
            json.dumps(
                {
                    "pid": self.old.pid,
                    "runtime_id": "old",
                }
            )
        )
        self.wrapper = self.root / "python-wrapper"
        self.wrapper.write_text(
            "#!/usr/bin/env python3\nimport time\ntime.sleep(120)\n"
        )
        self.wrapper.chmod(0o700)
        self.env = patch.dict(
            os.environ,
            {"ACDC_RELAY_DIR": str(self.relay), "CODEX_HOME": str(self.root / "codex")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def stop(self, pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    def stop_process(self, process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)

    async def test_reused_unrelated_pid_does_not_block_recovery_or_get_signaled(self):
        marker = json.loads(self.marker.read_text())
        marker.update(pid=os.getpid(), runtime_id=self.session.RUNTIME_ID)
        self.marker.write_text(json.dumps(marker))
        with (
            patch.object(self.session, "owner_pid", return_value=os.getpid()),
            patch.object(sys, "executable", str(self.wrapper)),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", ResourceWarning)
            await self.session.ensure_codex_discovery()
        current = json.loads(self.marker.read_text())
        self.addCleanup(self.stop, current["pid"])
        self.assertNotEqual(current["pid"], os.getpid())

    async def test_helper_upgrade_does_not_depend_on_launcher_cwd(self):
        with (
            patch.object(self.session, "owner_pid", return_value=os.getpid()),
            patch.object(os, "getcwd", side_effect=FileNotFoundError),
            patch.object(sys, "executable", str(self.wrapper)),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", ResourceWarning)
            await self.session.ensure_codex_discovery()
        current = json.loads(self.marker.read_text())
        self.addCleanup(self.stop, current["pid"])
        self.assertEqual(
            Path("/proc/" + str(current["pid"]) + "/cwd").resolve(), self.relay
        )

    async def test_reload_replaces_old_helper_and_records_current_fingerprint(self):
        # Wrapper stands in for the external child process only. The upgrade,
        # PID validation/termination and on-disk registration are real.
        with (
            patch.object(self.session, "owner_pid", return_value=os.getpid()),
            patch.object(sys, "executable", str(self.wrapper)),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", ResourceWarning)
            await self.session.ensure_codex_discovery()
        current = json.loads(self.marker.read_text())
        self.addCleanup(self.stop, current["pid"])
        self.assertNotEqual(current["pid"], self.old.pid)
        self.assertEqual(current["runtime_id"], self.session.RUNTIME_ID)
        self.assertFalse(self.session.alive(self.old.pid))

    async def test_reload_defers_upgrade_while_a_pair_is_active(self):
        a = self.session.SessionRelay(
            self.relay,
            "codex",
            self.tmp.name,
            "11111111-1111-1111-1111-111111111111",
            os.getpid(),
        )
        b = self.session.SessionRelay(
            self.relay,
            "claude",
            self.tmp.name,
            "22222222-2222-2222-2222-222222222222",
            os.getpid(),
        )
        await a.start()
        await b.start()
        self.addAsyncCleanup(a.close)
        self.addAsyncCleanup(b.close)
        await a.connect(b.id)
        with patch.object(self.session, "owner_pid", return_value=os.getpid()):
            await self.session.ensure_codex_discovery()
        self.assertEqual(json.loads(self.marker.read_text())["pid"], self.old.pid)
        self.assertTrue(self.session.alive(self.old.pid))
        self.assertEqual(a.connected, b.id)
