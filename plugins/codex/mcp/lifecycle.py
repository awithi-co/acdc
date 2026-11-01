"""Private runtime registry, process liveness and helper replacement."""

import asyncio
from contextlib import asynccontextmanager
import fcntl
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from relay import Relay, ensure_root
from version import RUNTIME_ID


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
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except (OSError, TypeError, IndexError):
        return False


def record_path(root, agent, session_id, owner):
    return Path(root) / f"{agent}-{session_id}-{owner}.json"


def current_path(root, agent, owner):
    return Path(root) / f"current-{agent}-{owner}.json"


def read_record(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_record(path, value):
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


@asynccontextmanager
async def exclusive_lock(path):
    # Nonblocking acquisition keeps two tasks in one event loop from deadlocking.
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


def _is_discovery_process(pid, owner):
    """A stale marker's PID may now be someone else's; never signal that process."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False
    return "session.py" in args and "discover" in args and f'"owner_pid": {owner}' in args


async def ensure_discovery(owner, script):
    """Run one discovery helper per Codex CLI, replacing it after a plugin update."""
    if owner is None:
        return
    root = relay_root()
    ensure_root(root)
    marker = root / f"codex-{owner}.discovery.json"
    async with exclusive_lock(root / f"codex-{owner}.discovery-start.lock"):
        status = read_record(marker) or {}
        pid = status.get("pid")
        if alive(pid) and _is_discovery_process(pid, owner):
            if status.get("runtime_id") == RUNTIME_ID:
                return
            # Brokers exit with their helper, so wait until no session is paired.
            for peer in await Relay(root).peers():
                if peer.get("owner_pid") == owner and peer.get("connected_to"):
                    return
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if not alive(pid):
                    break
                await asyncio.sleep(0.05)
            else:
                return
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
        write_record(marker, {"pid": child.pid, "runtime_id": RUNTIME_ID})
