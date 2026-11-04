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
    # macOS caps socket paths at 104 bytes, and its per-user TMPDIR alone is ~50.
    # A fixed /tmp also keeps processes launched without TMPDIR in the same place.
    base = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    return Path(
        os.environ.get("ACDC_RELAY_DIR", str(Path(base) / f"acdc-relay-{os.getuid()}"))
    )


def _load_libproc():
    """macOS process introspection without spawning ps/lsof on every poll."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes

        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
        ]
        library.proc_pidfdinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int
        ]
        return ctypes, library
    except (OSError, AttributeError):
        return None


_LIBPROC = _load_libproc()
# <sys/proc_info.h>: PROC_PIDLISTFDS, PROC_PIDTBSDINFO, PROC_PIDFDVNODEPATHINFO.
_LIST_FDS, _BSD_INFO, _VNODE_PATH = 1, 3, 2
_BSD_INFO_SIZE, _VNODE_PATH_SIZE, _VNODE_PATH_OFFSET = 136, 1200, 176
_FD_TYPE_VNODE, _ZOMBIE = 1, 5


def _darwin_running(pid):
    ctypes, library = _LIBPROC
    buffer = ctypes.create_string_buffer(_BSD_INFO_SIZE)
    # A zombie has no task left, so the call fails for it as for an exited PID.
    if library.proc_pidinfo(pid, _BSD_INFO, 0, buffer, _BSD_INFO_SIZE) != _BSD_INFO_SIZE:
        return False
    return int.from_bytes(buffer.raw[4:8], sys.byteorder) != _ZOMBIE


def _darwin_open_files(pid):
    ctypes, library = _LIBPROC
    size = library.proc_pidinfo(pid, _LIST_FDS, 0, None, 0)
    if size <= 0:
        return None
    buffer = ctypes.create_string_buffer(size + 64 * 8)  # Room for fds opened meanwhile.
    size = library.proc_pidinfo(pid, _LIST_FDS, 0, buffer, len(buffer))
    if size <= 0:
        return None
    paths = []
    for offset in range(0, size - size % 8, 8):
        fd = int.from_bytes(buffer.raw[offset:offset + 4], sys.byteorder, signed=True)
        kind = int.from_bytes(buffer.raw[offset + 4:offset + 8], sys.byteorder)
        if kind != _FD_TYPE_VNODE:
            continue
        info = ctypes.create_string_buffer(_VNODE_PATH_SIZE)
        if library.proc_pidfdinfo(pid, fd, _VNODE_PATH, info, _VNODE_PATH_SIZE) == _VNODE_PATH_SIZE:
            path = info.raw[_VNODE_PATH_OFFSET:].split(b"\0", 1)[0]
            paths.append(os.fsdecode(path))
    return paths


def open_files(pid):
    """Paths of files a process holds open, or None if they cannot be read."""
    fds = Path(f"/proc/{pid}/fd")
    if fds.parent.parent.is_dir():
        try:
            entries = list(fds.iterdir())
        except OSError:
            return None
        paths = []
        for entry in entries:
            try:
                paths.append(os.readlink(entry))
            except OSError:
                pass  # Closed while listing.
        return paths
    if _LIBPROC is not None:
        return _darwin_open_files(pid)
    return None


def _ps(pid, field):
    """Read one process field where /proc is unavailable (macOS)."""
    result = subprocess.run(
        ["ps", "-o", f"{field}=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    return result.stdout.strip()


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
        stat = Path(f"/proc/{pid}/stat")
        if stat.parent.parent.is_dir():
            state = stat.read_text().rsplit(")", 1)[1].split()[0]
        elif _LIBPROC is not None:
            return _darwin_running(pid)
        else:
            state = _ps(pid, "stat")[:1]
        return state not in ("", "Z", "X")
    except (OSError, TypeError, IndexError, subprocess.SubprocessError):
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
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if cmdline.parent.parent.is_dir():
            args = cmdline.read_bytes().decode(errors="replace")
        else:
            args = _ps(pid, "command")
    except (OSError, subprocess.SubprocessError):
        return False
    return "session.py" in args and "discover" in args and f'"owner_pid": {owner}' in args


def command_args(pid):
    """A process's argv, or [] when it cannot be read."""
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if cmdline.parent.parent.is_dir():
            return cmdline.read_bytes().decode(errors="replace").split("\0")
        return _ps(pid, "command").split()
    except (OSError, subprocess.SubprocessError):
        return []


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
