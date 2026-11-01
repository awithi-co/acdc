# CLI collaboration runtime

## Supported scope

The automatic path is validated on Linux with Claude Code 2.1.314 and Codex 0.166.0. Install Python 3.11+ as `python3`, `uv`, and the ACDC plugin in both CLIs. Desktop, macOS, remote Codex and `--no-daemon` automatic discovery are unverified.

Hooks skip unsupported platforms/Python versions and missing `uv` without exit-127 failures; SessionStart explains the missing prerequisite. The first `uv run` downloads locked dependencies from PyPI. If the MCP server fails to start, check `uv --version`, Python 3.11+, network/proxy access to PyPI and the CLI MCP diagnostics, then reopen. Offline first installation and enterprise proxies have not been validated.

Open both CLIs normally, request collaboration and select the counterpart once. Each CLI retains its own conversation. Native session titles and first-message previews help identify candidates; topic lookup uses `recall-context` and matches native UUIDs to live candidates. A preview is not a conversation search result.

## Lifecycle and recovery

Claude receives through `asyncRewake` hooks, without channel flags. After enabling ACDC mid-session, `/reload-plugins` followed by a prompt recovers registration. Hooks must be enabled and trusted. A watcher can expire after about 24 hours idle; the next prompt/tool completion/turn end rearms it. Claude may render wake delivery as a hook interruption; the payload is peer context, not a human command.

Codex discovers routable user sessions through its existing shared daemon. Internal title/subagent sessions are excluded. Loaded sessions can outlive terminal detachment; this is not a list of visible windows. A confirmed unload removes registration; a transient native read failure marks reception unavailable and preserves the connection. Discovery and waiting do not call a model.

Helpers are scoped to their native owner process. Runtime fingerprints replace inactive outdated helpers and brokers. Active collaborations defer replacement; finish/disconnect, reload the plugins and retry. Incompatible protocol versions are rejected with update guidance. Restarting a CLI remains a recovery option; no manual UUID registration is needed for the automatic path.

Initial tool approval follows each CLI's policy. An optional Codex plugin-specific trust setting is:

```toml
[plugins."acdc@woodstock".mcp_servers.relay]
default_tools_approval_mode = "approve"
```

## Tools and delivery

| Tool | Contract |
| --- | --- |
| `list_peers()` | List open sessions and project grouping. |
| `connect_peer(to)` | Pair the session the user chose or named. |
| `send_message(text, to?)` | Deliver a message; omit `to`. |
| `connection_status()` | Show the pairing and the initiating user's recent inputs. |
| `disconnect_peer()` | Clear the pairing and pending messages on both sides. |

Normal assistant text stays local; a reply goes through `send_message`. `accepted` and `queued` mean the transport took the message, not that a model read it. Messages are limited to 16 KiB. Claude holds pending messages in memory between hook watchers. There is no offline inbox or message database.

## Authorization

A connection exists because a user asked for it, so both agents keep working within that request without asking again. Peer messages are opinions, not user instructions. `connection_status` shows up to 20 recent inputs from the initiating session for context, read directly from its native transcript. Only attributed human turns count, so hook, queued and peer text never appears there.

The trust boundary is the same local OS user: a private mode-0700 runtime directory and mode-0600 sockets. `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `ACDC_RELAY_DIR` support local storage overrides.

See [test commands](tests/README.md) for unit, MCP, native and opt-in real-model verification.
