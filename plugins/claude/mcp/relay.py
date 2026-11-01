"""Same-user Unix socket transport between session brokers."""

import asyncio
import json
import os
from pathlib import Path
import re

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
PEER_ID = re.compile(r"(claude|codex)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
LINE_LIMIT = 65536


def ensure_root(root):
    """Create the private runtime directory; another user must not own it."""
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or root.is_symlink():
        raise ValueError("Relay directory must be private and owned by this user")


class Relay:
    """A socket client; SessionRelay adds the listening side."""

    def __init__(self, root):
        self.root = Path(root)

    async def request(self, peer_id, data, timeout=35):
        if not isinstance(peer_id, str) or not PEER_ID.fullmatch(peer_id):
            raise ValueError("Select an exact peer ID from list_peers")
        writer = None
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_unix_connection(
                    self.root / f"{peer_id}.sock", limit=LINE_LIMIT
                )
                writer.write(json.dumps(data, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
                result = json.loads(await reader.readline())
        except (OSError, TimeoutError, json.JSONDecodeError) as error:
            raise ConnectionError(
                "Peer did not answer; delivery outcome unknown. Refresh list_peers."
            ) from error
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        if "error" in result:
            raise ValueError(result["error"])
        return result["result"]

    async def peers(self):
        async def inspect(path):
            try:
                return await self.request(path.stem, {"op": "describe"}, timeout=1)
            except (ValueError, ConnectionError):
                return None

        rows = await asyncio.gather(*map(inspect, self.root.glob("*.sock")))
        return [row for row in rows if row]
