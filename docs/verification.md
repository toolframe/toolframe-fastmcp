# Gateway and client verification

After building the sibling core launcher and workspace wheelhouse:

```sh
uv run python scripts/verify-gateway.py \
  --binary ../toolframe-core/target/release/toolframe \
  --wheelhouse ../toolframe-core/wheelhouse
```

This creates a disposable application and two ordinary fixture plugins. Two real
FastMCP clients exercise admitted-call draining, a backend-only replacement that
retains MCP sessions, a catalog/schema change that replaces the MCP worker, and
explicit reconnection. Tool/document withdrawal, schema rejection and unrelated
worker continuity are checked through the actual core gateway. Evidence is saved
to `dist/gateway-evidence.json`; the script stops its server and preserves adjacent
`<report>.logs/<run>/` diagnostics. Successful checks remove their disposable
`build/verification/` workspace after saving evidence. The latest failed workspace
is retained for up to seven days and pruned by later runs when `ps` and `lsof`
confirm it is inactive. Unknown activity, changed ownership and live child
processes prevent deletion. `TOOLFRAME_KEEP_TEST_WORKSPACE=1` explicitly retains a
run, including after success. Existing applications and unmarked directories are
outside this cleanup scope. This check does not certify independent desktop MCP
clients.

## Codex MCP client acceptance

With a disposable Entry Register application running, use the installed Codex
client to discover the actual tool schema and call it through the core gateway:

```sh
# A fresh demo has zero entries; adjust --expected-count for your test data.
uv run python scripts/verify-codex.py \
  --url http://127.0.0.1:9876/mcp --expected-count 0
```

The probe uses the public Codex app-server `mcpServerStatus/list` and
`mcpServer/tool/call` APIs in an ephemeral thread. It checks a valid read and an
out-of-range argument. `--expected-max 10` verifies a changed catalog after reload;
`--expect-absent` checks withdrawal after disabling the demo plugin. Each run makes
a fresh client connection and writes schema, connection state and actual results
to `dist/codex-evidence.json` (override with `--output`). Only local HTTP endpoints
are accepted. It neither starts the Toolframe server nor modifies data.

Verified with `codex-cli 0.154.0`. The probe replaces the child process's MCP
configuration and disables unrelated plugins for that process; it never writes
global Codex configuration or copies credentials. It does not initiate a model
turn or drive the desktop UI. Noninteractive `codex exec` can refuse a tool call
when its approval policy requires user input; discovery alone is not proof that
such a model call succeeded. Store the probe output with the tested revision
and client version in the release evidence.
