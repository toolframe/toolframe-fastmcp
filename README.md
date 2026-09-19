# Toolframe FastMCP provider

`toolframe-fastmcp` publishes the accepted Toolframe MCP tool catalog through
FastMCP 4.0.0 over stateful Streamable HTTP at `/mcp`. The provider binds one
private loopback listener during activation and reports its URL through plugin
health; the core gateway owns any public forwarding.

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
to `dist/gateway-evidence.json`; the script stops its server and prints the log
directory on failure. It does not certify independent desktop MCP clients.

## macOS Intel release artifact

The provider pins `cryptography==50.0.1`. Upstream stopped publishing macOS Intel
wheels in cryptography 49; see the [upstream changelog](https://cryptography.io/en/latest/changelog/).
Toolframe's Intel distribution therefore needs a companion wheel in its
wheelhouse. An ordinary PyPI-only installation on Intel may attempt a source
build and require native build dependencies.

```sh
# From this repository; requires Xcode command-line tools, make, Perl, Rust and uv.
# On Apple Silicon, also install Rosetta and the x86_64-apple-darwin Rust target.
uv run python scripts/build-macos-intel.py --out-dir ../toolframe-core/wheelhouse
```

The script verifies pinned source SHA-256 values, builds OpenSSL 4.0.2 in a
temporary private prefix, and statically links it into the Python 3.14 Intel
cryptography wheel with a macOS 12 deployment target. It makes no system OpenSSL
installation. Distribute this wheel beside the provider wheel; retain its upstream
license metadata. Updating either native dependency requires updating the pins,
checksums and release acceptance together.

The complete Intel template, online startup and warm-cache offline restart were
exercised under Rosetta on macOS 14.8.5. Physical Intel hardware and macOS 12 remain
separate release checks. See the core repository's acceptance report for evidence.
