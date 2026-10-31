# CLI collaboration runtime

## Supported scope

The automatic path is validated on Linux with Claude Code 2.1.314 and Codex 0.166.0. Install Python 3.11+ as `python3`, `uv`, and the ACDC plugin in both CLIs. Desktop, macOS, remote Codex and `--no-daemon` automatic discovery are unverified.

Open both CLIs normally, request collaboration and select the counterpart once. Each CLI retains its own conversation. Native session titles, aliases and first-message previews help identify candidates; topic lookup uses `recall-context` and matches native UUIDs to live candidates. A preview is not a conversation search result.

## Lifecycle and recovery

Claude receives through `asyncRewake` hooks, without channel flags. After enabling ACDC mid-session, `/reload-plugins` followed by a prompt recovers registration. Hooks must be enabled and trusted. A watcher can expire after about 24 hours idle; the next prompt/tool completion/turn end rearms it. Claude may render wake delivery as a hook interruption; the payload is peer context, not a human command.

Codex discovers routable user sessions through its existing shared daemon. Internal title/subagent sessions are excluded. Loaded sessions can outlive terminal detachment; this is not a list of visible windows. A confirmed unload removes registration; a transient native read failure marks reception unavailable and preserves the connection. Discovery and waiting do not call a model.

Helpers are scoped to their native owner process. Runtime fingerprints replace inactive outdated helpers and brokers. Active collaborations defer replacement; finish/disconnect, reload the plugins and retry. Incompatible runtime pairs are rejected with update guidance. Restarting a CLI remains a recovery option; no manual UUID registration is needed for the automatic path.

Initial tool approval follows each CLI's policy. An optional Codex plugin-specific trust setting is:

```toml
[plugins."acdc@woodstock".mcp_servers.relay]
default_tools_approval_mode = "approve"
```

## Tools and delivery

| Tool | Contract |
| --- | --- |
| `list_peers()` | Discover routable candidates and project grouping. |
| `connect_peer(to)` | Pair exactly the user-selected live session. |
| `connection_status()` | Inspect pairing, runtime and original-request evidence. |
| `send_message(text, to?)` | Deliver substantive context; omit `to` on follow-ups. |
| `disconnect_peer()` | Clear pairing and pending messages on both sides. |
| `register_peer(name, thread_id?)` | Set an alias; explicit legacy routing is retained for compatibility. |

Normal assistant text stays local. A reply is delivered only through `send_message`. `accepted`, `queued` and `transport_written` mean transport acceptance, not proof that a model read or completed the request. Verify an actual reply when completion matters. Delivery failures are reported without automatic resend; a lost response can leave delivery uncertain.

Messages are limited to 16 KiB. Claude holds at most eight pending messages in live memory between hook watchers. There is no offline inbox, message database or replay service. Disconnect discards pending context; stale connection IDs cannot deliver into a new pairing. Socket requests have bounded size, concurrency and timeouts; discovery scans at most 200 candidate sockets.

## Original user requests

A connection pins up to three recent human inputs by native identity, text hash and byte offset. The recipient resolves the source path locally and rereads it independently. Claude human provenance and Codex native user-turn attribution distinguish actual user input from hook/queued messages. A peer body cannot grant permission. Native slash commands, compaction and interruption signals are surfaced as non-authorizing control events; command output is excluded. Only the pinned recent window is supplied, so an older request outside that window is not implicitly verified.

Evidence proves provenance, not semantic authorization. The agent must assess the original target/scope, its own user's restrictions and every later human instruction. Later inputs are returned with an explicit incomplete status when more than three exist. Edited, unavailable, ambiguous or truncated evidence must not be treated as full authorization. Capture failures include a diagnostic reason in the connection result.

Transcripts have no file-wide size cap. Records are bounded to 16 MiB; oversized or incomplete records return explicit unavailability rather than silently skipping possible restrictions. Images are excluded from text excerpts; non-text inputs are flagged as truncated. Source records remain read-only. No secondary history index or approval database is created.

The trust boundary is the same local OS user: private mode-0700 runtime directories and mode-0600 sockets. Same-user processes can modify native files; this is not cryptographic identity or remote authorization. `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `ACDC_RELAY_DIR` support local storage overrides.

## Legacy explicit runtime

`scripts/channel_runtime.py --help` describes the retained explicit Codex runtime launcher. It is not required for automatic CLI collaboration. `ACDC_CODEX_REMOTE` accepts only a local Unix endpoint or localhost WebSocket. Do not register both a user MCP server and the plugin's MCP server for the same purpose.

See [test commands](tests/README.md) for unit, MCP, native and opt-in real-model verification.
