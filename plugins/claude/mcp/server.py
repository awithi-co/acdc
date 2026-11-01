# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0", "websockets==15.0.1"]
# ///
"""ACDC MCP tools; each call is answered by this session's live broker."""

import argparse
import asyncio
import json
import os
import signal

from mcp import types
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from version import VERSION
from relay import Relay
from session import (
    local_record,
    ensure_codex_discovery,
    project_key,
    current_cwd,
    relay_root,
    public_view,
    INSTRUCTIONS,
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=["claude", "codex"])
    args = parser.parse_args()
    root = relay_root()
    thread_id = os.environ.get("CODEX_THREAD_ID") if args.agent == "codex" else None
    server = Server("acdc-relay")
    discover = args.agent == "codex" and os.environ.get("PLUGIN_ROOT")
    if discover:
        await ensure_codex_discovery()
    relay = Relay(root)

    async def own_broker():
        if discover:
            await ensure_codex_discovery()
        caller_id = thread_id
        if args.agent == "codex":
            meta = server.request_context.meta
            if meta is not None:
                caller_id = getattr(meta, "threadId", None) or caller_id
        record = local_record(root, args.agent, caller_id)
        if record:
            try:
                return await relay.request(record["id"], {"op": "describe"}, timeout=1)
            except (ConnectionError, ValueError):
                pass
        return None

    def require(broker):
        if not broker:
            raise ValueError(
                "This session is not registered yet. Enable and trust ACDC hooks, run /reload-plugins and send a prompt."
                if args.agent == "claude"
                else "This session is not registered yet; check that the shared Codex daemon is running and reload the plugin."
            )
        return broker["id"]

    tools = [
        types.Tool(
            name="list_peers",
            description="List open ACDC sessions with their titles, project and first-message preview.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="send_message",
            description="Send a message to the connected session; omit to. Acceptance is not proof of reading.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "text": {"type": "string", "minLength": 1},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="connect_peer",
            description="Connect the session the user chose or named, using its live ID from list_peers. Follow-ups keep this connection.",
            inputSchema={
                "type": "object",
                "properties": {"to": {"type": "string"}},
                "required": ["to"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="disconnect_peer",
            description="End this collaboration on both sides and discard pending messages.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="connection_status",
            description="Show the current connection and, for context, the initiating user's recent inputs read directly from their session.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    ]

    @server.list_tools()
    async def list_tools():
        await own_broker()
        return tools

    @server.call_tool()
    async def call_tool(name, arguments):
        broker = await own_broker()
        if name == "list_peers":
            caller_cwd = (broker or {}).get("cwd") or current_cwd()
            caller_project = project_key(caller_cwd)
            result = [
                {**public_view(p), "same_project": p.get("project") == caller_project}
                for p in await relay.peers()
                if p["id"] != (broker or {}).get("id")
            ]
            result.sort(
                key=lambda p: (
                    not p["same_project"],
                    p["cwd"] != caller_cwd,
                    p["agent"],
                    p["name"],
                )
            )
        elif name == "connect_peer":
            result = await relay.request(
                require(broker), {"op": "connect", "to": arguments["to"]}
            )
            result["peer"] = public_view(result["peer"])
            result["self"] = public_view(result["self"])
        elif name == "disconnect_peer":
            result = await relay.request(require(broker), {"op": "disconnect"})
        elif name == "send_message":
            result = await relay.request(
                require(broker),
                {"op": "send", "to": arguments.get("to"), "text": arguments["text"]},
            )
        elif name == "connection_status":
            result = {
                "self": public_view(broker) if broker else None,
                "peer": None,
                "connected": False,
                "user_request_evidence": {"requests": []},
            }
            if broker and broker.get("connected_to"):
                try:
                    peer = await relay.request(
                        broker["connected_to"], {"op": "describe"}, timeout=1
                    )
                    result["peer"] = public_view(peer)
                    result["connected"] = peer.get("connected_to") == broker["id"]
                    result["user_request_evidence"] = await relay.request(
                        broker["id"], {"op": "user_request_evidence"}
                    )
                except (ConnectionError, ValueError):
                    pass
        else:
            raise ValueError("Unknown tool")
        return [
            types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
        ]

    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="acdc-relay",
                server_version=VERSION,
                capabilities=types.ServerCapabilities(tools=types.ToolsCapability()),
                instructions=INSTRUCTIONS,
            ),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
