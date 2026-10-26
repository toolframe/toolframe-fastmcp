# Toolframe FastMCP provider

`toolframe-fastmcp` publishes the accepted Toolframe MCP tool catalog through
FastMCP 4.0.0 over stateful Streamable HTTP at `/mcp`. The provider binds one
private loopback listener during activation and reports its URL through plugin
health; the core gateway owns any public forwarding.

## Install

Install the provider into a running [Toolframe application](https://github.com/toolframe/toolframe-core#readme):

```sh
toolframe --home APP plugin add pypi:toolframe-fastmcp==0.3.1 --enable
```

Core resolves dependencies and manages the provider's isolated Python environment.
The Entry Register template already includes this provider.

All private-listener requests require a live core-issued request credential.
Core admits public requests or validates external MCP bearer keys according to
its independent `[mcp_access]` policy. FastMCP never receives the external secret;
it resolves the temporary credential through `RuntimeContext.mcp_access`, filters
tool discovery by the allowed resources, and carries the credential back through
`resource_call`. Core rechecks scopes, expiry and revocation at tool admission.
Stateful MCP sessions bind to the credential record. Use this provider together
with the matching SDK/core changes; an older core cannot supply request credentials.
See the MCP authentication guide in the core repository for configuration details.

Stateful sessions expire after 1800 seconds with no HTTP request in flight.
The provider's optional `session_idle_timeout` configuration accepts a positive,
finite number of seconds and takes precedence over FastMCP's
`FASTMCP_HTTP_SESSION_IDLE_TIMEOUT` environment setting. When neither is set,
the provider uses 1800 seconds; inactivity expiry cannot be disabled with `null`.
An open GET event stream or an in-flight tool request keeps a session active.
After expiry, the old session ID returns HTTP 404 and the client must initialize
a new session. Client DELETE and provider shutdown also release sessions.

Core sends each complete catalog epoch through `prepare`, `commit`, and
`abort`. Preparation builds a frozen candidate without changing the live list.
Commit swaps the entire list atomically, and a later commit withdraws tools
that are absent from that epoch. Core replaces this provider process when the
public tool schema or catalog changes, so MCP clients reconnect and list tools
again after replacement.

Every tool belongs to one SDK `OwnedResource`. Its call is routed through the
SDK `RuntimeContext.resource_call` boundary with the owned resource key,
declared method, and prepared epoch. The provider does not import business
implementations, access business storage, or expose MCP resources and prompts.

## CI and publishing

See [CI and release setup](docs/ci.md) for the shared local validation command,
the GitHub Actions checks, and manual PyPI Trusted Publishing setup.
Validation builds the pinned SDK in a disposable workspace, runs the plugin
tests, checks the distributions, and exercises a clean installed provider wheel
with its real runtime dependencies.

## Gateway acceptance

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

## macOS Intel release artifact

The provider pins `cryptography==50.0.2`. Upstream stopped publishing macOS Intel
wheels in cryptography 49; see the [upstream changelog](https://cryptography.io/en/latest/changelog/).
Toolframe's Intel launcher automatically downloads a companion from its matching
GitHub Release and verifies the SHA-256 pinned into the executable. An ordinary PyPI-only installation on Intel may attempt a source
build and require native build dependencies.

```sh
# From this repository; requires Xcode command-line tools, make, Perl, Rust and uv.
# On Apple Silicon, also install Rosetta and the x86_64-apple-darwin Rust target.
uv run python scripts/build-macos-intel.py --out-dir ../toolframe-core/wheelhouse
```

The script verifies pinned source SHA-256 values, builds OpenSSL 4.0.3 in a
temporary private prefix, and statically links it into the Python 3.14 Intel
cryptography wheel with a macOS 12 deployment target. It makes no system OpenSSL
installation. Publish this wheel as a separate Core GitHub Release asset; retain its upstream
license metadata. No Python wheel is embedded in the launcher. Updating either native dependency requires updating the pins,
checksums and release acceptance together.

Qualify the exact Intel release on its native runner. Rosetta, physical Intel
hardware and the macOS 12 minimum are separate verification environments.

For release validation and Trusted Publisher setup, see [releasing](docs/releasing.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

For development hooks and contribution rules, see [Contributing](CONTRIBUTING.md).

Plugin dependencies allow compatible SDK 0.x updates from the declared minimum.
See the [SDK compatibility policy](https://github.com/toolframe/toolframe-sdk/blob/main/docs/compatibility.md) for API guarantees and minimum/latest verification.
