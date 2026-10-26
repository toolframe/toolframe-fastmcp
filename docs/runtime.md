# MCP sessions and publication

All private-listener requests require a live core-issued request credential.
Core admits public requests or validates external MCP bearer keys according to
its independent `[mcp_access]` policy. FastMCP never receives the external secret;
it resolves the temporary credential through `RuntimeContext.mcp_access`, filters
tool discovery by the allowed resources, and carries the credential back through
`resource_call`. Core rechecks scopes, expiry and revocation at tool admission.
Stateful MCP sessions bind to the credential record. Use this provider together
with the matching SDK/core changes; an older core cannot supply request credentials.
See the MCP authentication guide in the core repository for configuration details.

## Session lifetime

Stateful sessions expire after 1800 seconds with no HTTP request in flight.
The provider's optional `session_idle_timeout` configuration accepts a positive,
finite number of seconds and takes precedence over FastMCP's
`FASTMCP_HTTP_SESSION_IDLE_TIMEOUT` environment setting. When neither is set,
the provider uses 1800 seconds; inactivity expiry cannot be disabled with `null`.
An open GET event stream or an in-flight tool request keeps a session active.
After expiry, the old session ID returns HTTP 404 and the client must initialize
a new session. Client DELETE and provider shutdown also release sessions.

## Catalog publication

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
