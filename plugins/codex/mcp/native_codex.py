"""The existing Codex daemon and queue; no custom app-server is started."""

import asyncio
from contextlib import asynccontextmanager
import json

from version import VERSION


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
    from websockets.asyncio.client import unix_connect

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
