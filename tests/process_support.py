"""Process inspection that works with or without /proc (Linux and macOS)."""

from pathlib import Path
import subprocess
import sys

# macOS caps Unix socket paths at 104 bytes; its default TMPDIR is too long.
SOCKET_TMP = "/tmp" if sys.platform == "darwin" else None


def process_state(pid):
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        return stat.read_text().rsplit(")", 1)[1].split()[0]
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    )
    return result.stdout.strip()[:1]


def process_cwd(pid):
    if Path("/proc").is_dir():
        return Path(f"/proc/{pid}/cwd").resolve()
    result = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
        capture_output=True,
        text=True,
        check=True,
    )
    name = next(line[1:] for line in result.stdout.splitlines() if line.startswith("n"))
    return Path(name).resolve()
