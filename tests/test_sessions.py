"""Exercise legacy stateful sessions over the provider's real HTTP listener."""

from __future__ import annotations

import asyncio
import json
from importlib import import_module
from unittest.mock import AsyncMock

import fastmcp
import httpx2
import pytest
from jsonschema.exceptions import ValidationError
from toolframe_sdk import OwnedResource, PluginError, Resource, RuntimeContext, validate

from toolframe_fastmcp.plugin import McpProvider, plugin


@pytest.fixture
def context():
    context = RuntimeContext(None, plugin_id="fastmcp", generation="test")

    async def resolve(token):
        if token not in {"principal-a", "principal-b"}:
            raise PluginError("authentication_required", "Unknown request")
        return {
            "principal": {"credential_id": token, "scopes": []},
            "issuer": "test-core",
            "resources": ["demo/mcp.tool/echo"],
        }

    context.mcp_access = AsyncMock(side_effect=resolve)
    context.resource_call = AsyncMock(return_value={"value": "ok"})
    return context


def manager(provider):
    # FastMCP's authenticated route wraps the real ASGI session manager.
    # Inspect private state only to shrink the fixture and observe reclamation.
    route = next(route for route in provider.app.routes if route.path == "/mcp")
    return route.app.app.session_manager


async def wait_for_sessions(session_manager, expected):
    async with asyncio.timeout(5):
        while len(session_manager._server_instances) != expected:
            await asyncio.sleep(0.01)


def headers(principal, session=None):
    result = {
        "Authorization": f"Bearer {principal}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session is not None:
        result["Mcp-Session-Id"] = session
    return result


def message(method, params=None):
    result = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        result["params"] = params
    return result


async def initialize(client, principal):
    response = await client.post(
        "/mcp",
        headers=headers(principal),
        json=message(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "session-test", "version": "1"},
            },
        ),
    )
    return response


def rpc_body(response):
    if response.headers["content-type"].startswith("text/event-stream"):
        return json.loads(
            next(
                line[6:]
                for line in response.text.splitlines()
                if line.startswith("data: ")
            )
        )
    return response.json()


@pytest.mark.parametrize(
    ("configured", "operator", "expected"),
    [({}, None, 1800), ({}, 42.5, 42.5), ({"session_idle_timeout": 12}, 42, 12)],
)
async def test_effective_timeout(context, monkeypatch, configured, operator, expected):
    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", operator)
    context.config = configured
    provider = McpProvider(context)
    await provider.activate()
    try:
        assert manager(provider).session_idle_timeout == expected
        assert manager(provider).stateless is False
    finally:
        await provider.dispose()


@pytest.mark.parametrize(
    "value", [None, False, 0, -1, "30", float("inf"), float("nan")]
)
async def test_invalid_timeout_fails_before_listener(context, value):
    context.config = {"session_idle_timeout": value}
    provider = McpProvider(context)
    with pytest.raises(PluginError, match="positive, finite"):
        await provider.activate()
    assert provider.server is None
    if value is None or isinstance(value, (bool, str)) or value <= 0:
        with pytest.raises(ValidationError):
            validate(plugin.config_schema, context.config)


@pytest.mark.parametrize("source", ["fallback", "operator", "config"])
async def test_idle_expiry_reclaims_slots_for_another_principal(
    context, monkeypatch, source
):
    # Shorten only the test deadline; exercise the real transport's idle loop.
    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", None)
    if source == "fallback":
        monkeypatch.setattr(
            import_module(McpProvider.__module__),
            "_DEFAULT_SESSION_IDLE_TIMEOUT",
            0.5,
            raising=False,
        )
    elif source == "operator":
        monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", 0.5)
    else:
        context.config = {"session_idle_timeout": 0.5}
    provider = McpProvider(context)
    await provider.activate()
    session_manager = manager(provider)
    session_manager.max_sessions = 2
    try:
        await provider.prepare(
            [
                OwnedResource(
                    owner="demo",
                    generation="test",
                    resource=Resource(
                        id="echo",
                        kind="mcp.tool",
                        title="Echo",
                        data={"service": "demo", "method": "echo"},
                    ),
                )
            ],
            "epoch",
        )
        await provider.commit("epoch")
        async with httpx2.AsyncClient(
            base_url=provider.server.url, timeout=5
        ) as client:
            sessions = []
            for _ in range(2):
                response = await initialize(client, "principal-a")
                assert response.status_code == 200
                assert rpc_body(response)["result"]["protocolVersion"] == "2025-11-25"
                session = response.headers["mcp-session-id"]
                sessions.append(session)
                initialized = await client.post(
                    "/mcp",
                    headers=headers("principal-a", session),
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                assert initialized.status_code == 202
            assert len(session_manager._server_instances) == 2
            assert (await initialize(client, "principal-b")).status_code == 503
            wrong_owner = await client.delete(
                "/mcp", headers=headers("principal-b", sessions[0])
            )
            assert wrong_owner.status_code == 404
            assert len(session_manager._server_instances) == 2

            await wait_for_sessions(session_manager, 0)
            for session in sessions:
                expired = await client.post(
                    "/mcp",
                    headers=headers("principal-a", session),
                    json=message("ping"),
                )
                assert expired.status_code == 404

            response = await initialize(client, "principal-b")
            assert response.status_code == 200
            session = response.headers["mcp-session-id"]
            assert len(session_manager._server_instances) == 1
            await client.post(
                "/mcp",
                headers=headers("principal-b", session),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            tools = await client.post(
                "/mcp",
                headers=headers("principal-b", session),
                json=message("tools/list"),
            )
            assert [tool["name"] for tool in rpc_body(tools)["result"]["tools"]] == [
                "demo__echo"
            ]
            call = await client.post(
                "/mcp",
                headers=headers("principal-b", session),
                json=message("tools/call", {"name": "demo__echo", "arguments": {}}),
            )
            assert rpc_body(call)["result"]["structuredContent"] == {"value": "ok"}
            context.resource_call.assert_awaited_once_with(
                "demo/mcp.tool/echo",
                "echo",
                {},
                epoch="epoch",
                access_context="principal-b",
            )
            deleted = await client.delete(
                "/mcp", headers=headers("principal-b", session)
            )
            assert deleted.status_code == 200
            await wait_for_sessions(session_manager, 0)

            # Leave one real session open: disposal must close it without DELETE.
            response = await initialize(client, "principal-a")
            assert response.status_code == 200
            transport = session_manager._server_instances[
                response.headers["mcp-session-id"]
            ]
    finally:
        await provider.dispose()
    assert session_manager._server_instances == {}
    assert session_manager._session_owners == {}
    assert transport.is_terminated
    assert provider.server.task.done()
