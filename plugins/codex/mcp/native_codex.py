"""The existing Codex daemon and queue; no custom app-server is started."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re

from evidence import first_codex_request
from lifecycle import command_args, open_files
from version import VERSION

ROLLOUT = re.compile(r"/rollout-[^/]*\.jsonl$")
MAX_HEADER_BYTES = 4 * 1024 * 1024


def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def daemon_socket_present():
    """A running shared daemon listens here; checking costs no process launch."""
    return (codex_home() / "app-server-control" / "app-server-control.sock").exists()


def embedded_cli(pid):
    """A --no-daemon Codex CLI runs its own server, so only its files reveal its threads."""
    return "--no-daemon" in command_args(pid)


def rollout_header(path):
    """The session_meta record Codex writes as a rollout's first line."""
    try:
        with open(path, "rb") as stream:
            row = json.loads(stream.readline(MAX_HEADER_BYTES))
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("type") != "session_meta":
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("id"), str) or not isinstance(payload.get("cwd"), str):
        return None
    return payload


class EmbeddedThreads:
    """Threads an embedded Codex CLI has loaded: the rollouts it holds open.

    Headers never change, so each rollout is read once; names are re-read only
    when Codex's session index changes.
    """

    def __init__(self, owner):
        self.owner = owner
        self.headers = {}
        self.previews = {}
        self.sent = {}  # Metadata each broker last received, by thread id.
        self.index = codex_home() / "session_index.jsonl"
        self.index_stamp = None
        self.names = {}

    def thread_names(self):
        try:
            stat = self.index.stat()
        except OSError:
            return self.names
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self.index_stamp:
            names = {}
            try:
                with self.index.open(encoding="utf-8", errors="replace") as lines:
                    for line in lines:
                        try:
                            row = json.loads(line)
                            names[row["id"]] = row.get("thread_name")
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                return self.names
            self.names, self.index_stamp = names, stamp
        return self.names

    def loaded(self):
        """{thread id: thread info}, or None if the CLI's files cannot be read."""
        paths = open_files(self.owner)
        if paths is None:
            return None
        threads = {}
        for path in paths:
            if not ROLLOUT.search(path):
                continue
            header = self.headers.get(path) or rollout_header(path)
            if not header:
                continue  # Not written yet; retry on the next scan.
            self.headers[path] = header
            tid = header["id"]
            threads[tid] = {
                "id": tid,
                "cwd": header["cwd"],
                "source": header.get("source"),
                "threadSource": header.get("thread_source", "user"),
                "path": path,
            }
        return threads

    def metadata(self, thread):
        tid = thread["id"]
        preview, scanned = self.previews.get(tid, ("", None))
        try:
            size = os.stat(thread["path"]).st_size
        except OSError:
            size = scanned
        if not preview and size != scanned:  # The first prompt may not be written yet.
            preview = first_codex_request(thread["path"], tid)
            self.previews[tid] = (preview, size)
        return {"name": self.thread_names().get(tid), "preview": preview}

    def holds(self, thread_id):
        threads = self.loaded()
        return threads is not None and thread_id in threads


async def queue_codex(thread_id, text):
    process = await asyncio.create_subprocess_exec(
        "codex", "queue", "--thread", thread_id, "--message", text,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(25):
            _, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise ValueError(
            "Codex queue rejected delivery: " + stderr.decode(errors="replace")[-2000:]
        )


class DaemonConnection:
    def __init__(self, socket):
        self.socket = socket
        self.sequence = 0

    async def rpc(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        await self.socket.send(
            json.dumps({"id": request_id, "method": method, "params": params})
        )
        async with asyncio.timeout(5):
            while True:
                response = json.loads(await self.socket.recv())
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise ValueError(
                        "Codex daemon rejected "
                        + method
                        + ": "
                        + str(response["error"])[:300]
                    )
                return response["result"]


@asynccontextmanager
async def connect_daemon():
    process = await asyncio.create_subprocess_exec(
        "codex",
        "app-server",
        "daemon",
        "version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    status = json.loads(output)
    if status.get("status") != "running":
        raise ValueError(
            "No running shared Codex daemon; automatic discovery is unavailable"
        )
    from websockets.asyncio.client import unix_connect

    async with unix_connect(
        status["socketPath"], max_size=2 * 1024 * 1024, open_timeout=5
    ) as socket:
        connection = DaemonConnection(socket)
        await connection.rpc(
            "initialize",
            {
                "clientInfo": {"name": "acdc_discovery", "version": VERSION},
                "capabilities": {"experimentalApi": True},
            },
        )
        await socket.send('{"method":"initialized"}')
        yield connection
