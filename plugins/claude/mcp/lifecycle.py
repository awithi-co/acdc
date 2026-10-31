"""Private runtime registry, process liveness and safe helper upgrades."""

import asyncio
from contextlib import asynccontextmanager
import fcntl
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

from relay import Relay, PEER_ID
from version import VERSION, RUNTIME_ID

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


@lru_cache(maxsize=256)
def project_key(cwd):
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                cwd,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return str(Path(result.stdout.strip()).resolve())
    except (OSError, subprocess.SubprocessError):
        pass
    return str(Path(cwd).resolve())


def current_cwd():
    """A cache update can remove a long-lived MCP process's launch directory."""
    try:
        return os.getcwd()
    except FileNotFoundError:
        return str(Path.home())


def relay_root():
    return Path(
        os.environ.get(
            "ACDC_RELAY_DIR",
            str(Path(tempfile.gettempdir()) / f"acdc-relay-{os.getuid()}"),
        )
    )


def owner_pid(agent):
    """Find the actual CLI ancestor rather than an intermediate hook shell/uv."""
    pid = os.getppid()
    for _ in range(12):
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            parent, command = result.stdout.strip().split(None, 1)
            if Path(command).name in (agent, "claude-code"):
                return pid
            pid = int(parent)
            if pid <= 1:
                break
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return None


def alive(pid):
    try:
        os.kill(pid, 0)
        if Path("/proc/self/stat").exists():
            try:
                state = (
                    Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                )
                if state in ("Z", "X"):
                    return False
            except FileNotFoundError:
                return False
        return True
    except (OSError, TypeError):
        return False


def record_path(root, agent, session_id, owner):
    if not UUID.fullmatch(session_id):
        raise ValueError("Invalid session identity")
    return Path(root) / f"{agent}-{session_id}-{owner}.json"


def current_path(root, agent, owner):
    return Path(root) / f"current-{agent}-{owner}.json"


def read_record(path):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not PEER_ID.fullmatch(value.get("id", "")):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


@asynccontextmanager
async def exclusive_lock(path):
    # Nonblocking acquisition is essential: two tasks in the same event loop
    # must not deadlock while the lock holder awaits subprocess/socket I/O.
    with path.open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _version(value):
    try:
        return tuple(int(part) for part in value.split("."))
    except (AttributeError, ValueError):
        return (0,)


def _owned_discovery_process(pid, owner):
    try:
        process = Path(f"/proc/{pid}")
        if process.stat().st_uid != os.getuid():
            return False
        args = process.joinpath("cmdline").read_bytes().decode().split("\0")
        scripts = [
            index
            for index, arg in enumerate(args)
            if Path(arg).name == "session.py" and Path(arg).parent.name == "mcp"
        ]
        if len(scripts) != 1 or args[scripts[0] + 1] != "discover":
            return False
        return (
            args[args.index("--agent") + 1] == "codex"
            and json.loads(args[args.index("--data") + 1]).get("owner_pid") == owner
        )
    except (OSError, ValueError, IndexError, UnicodeError):
        return False


async def ensure_discovery(owner, script):
    if owner is None:
        return "unsupported"
    root = relay_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or root.is_symlink():
        raise ValueError("Relay directory must be private and owned by this user")
    marker = root / f"codex-{owner}.discovery.json"
    async with exclusive_lock(root / f"codex-{owner}.discovery-start.lock"):
        try:
            status = json.loads(marker.read_text())
        except (OSError, ValueError):
            status = {}
        running = (
            status.get("owner_pid") == owner
            and alive(status.get("pid"))
            and _owned_discovery_process(status["pid"], owner)
        )
        if (
            running
            and status.get("runtime_id") == RUNTIME_ID
            and Path(status.get("script_path", "")).is_file()
        ):
            return "ready"
        if running and _version(status.get("version")) > _version(VERSION):
            return "newer_runtime"
        if running:
            client = Relay(root, "codex", str(root), None)
            peers = [
                peer
                for peer in await client.peers()
                if peer.get("automatic")
                and peer.get("agent") == "codex"
                and peer.get("owner_pid") == owner
            ]
            if any(peer.get("connected_to") for peer in peers):
                return "upgrade_pending"
            visible = {peer["id"] for peer in peers}
            for record in root.glob(f"codex-*-{owner}.json"):
                row = read_record(record)
                if row and row["id"] not in visible and alive(row.get("pid")):
                    return "upgrade_pending"
            # Never signal a reused PID or an unrelated user process.
            if not _owned_discovery_process(status["pid"], owner):
                return "upgrade_pending"
            os.kill(status["pid"], signal.SIGTERM)
            for _ in range(100):
                if not alive(status["pid"]):
                    break
                await asyncio.sleep(0.05)
            else:
                return "upgrade_pending"
            outdated = [peer for peer in peers if peer.get("runtime_id") != RUNTIME_ID]
            for peer in outdated:
                try:
                    await client.request(peer["id"], {"op": "shutdown"}, timeout=2)
                except (ConnectionError, ValueError):
                    pass
            for _ in range(100):
                if all(not alive(peer.get("pid")) for peer in outdated):
                    break
                await asyncio.sleep(0.05)
            else:
                return "upgrade_pending"
        child = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "discover",
                "--agent",
                "codex",
                "--data",
                json.dumps({"owner_pid": owner}),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=root,
        )
        temporary = marker.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "owner_pid": owner,
                    "version": VERSION,
                    "runtime_id": RUNTIME_ID,
                    "script_path": str(script),
                }
            )
        )
        temporary.chmod(0o600)
        temporary.replace(marker)
        return "started"
