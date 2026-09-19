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
