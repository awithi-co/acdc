# Verification

From the repository root:

```sh
python3 -m unittest discover -s tests -q
npm ci --ignore-scripts --prefix tests/mcp
npm test --prefix tests/mcp
```

Native CLI integration uses temporary homes, the installed plugin and a local mock model. It does not call paid model APIs. Install the supported CLI versions and `uv` first:

```sh
python3 tests/native_auto_connect_probe.py pair --blank --check-multi
python3 tests/native_auto_connect_probe.py claude --automatic --reload-plugin
```

The real-model E2E is explicitly opt-in and consumes authenticated Claude and Codex tokens:

```sh
ACDC_RUN_PAID_TESTS=1 python3 tests/live_pair_evidence_probe.py
```

It copies local authentication into mode-0600 files in disposable homes, never targets existing user sessions, and removes temporary storage on exit. Only the initiating agent receives the collaboration request. Assertions require original-request evidence, two actual round trips, disconnect and summary, with no shell/file-edit calls by the models. Do not attach private native session logs to public results.

Shared helpers are shipped as copies because plugins install independently; mirror tests require identical contents. Both Codex manifest formats remain intentionally present: the supported CLI consumes legacy environment forwarding as well as the modern installation metadata.
