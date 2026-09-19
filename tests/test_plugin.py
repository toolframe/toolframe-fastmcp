from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from toolframe_sdk import OwnedResource, PluginError, Resource, RuntimeContext

from toolframe_fastmcp.plugin import McpProvider, plugin


def owned(
    owner: str,
    tool_id: str,
    *,
    title: str = "Echo",
    description: str | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    method: str = "echo",
) -> OwnedResource:
    data = {"service": "demo", "method": method}
    if description is not None:
        data["description"] = description
    if input_schema is not None:
        data["input_schema"] = input_schema
    if output_schema is not None:
        data["output_schema"] = output_schema
    return OwnedResource(
        owner=owner,
        generation="generation-1",
        resource=Resource(id=tool_id, kind="mcp.tool", title=title, data=data),
    )


@pytest.fixture
def context() -> RuntimeContext:
    return RuntimeContext(None, plugin_id="fastmcp", generation="generation-1")


@pytest.mark.asyncio
async def test_definition_and_http_lifecycle(context: RuntimeContext):
    assert plugin.model_dump(mode="json") == {
        "id": "fastmcp",
        "api_version": 1,
        "factory": "toolframe_fastmcp.plugin:McpProvider",
        "kind": "provider",
        "provider_kind": "mcp",
        "config_schema": {
            "type": "object",
            "additionalProperties": False,
        },
        "requires": {},
        "services": {},
        "resources": [],
        "store": None,
    }
    provider = McpProvider(context)
    await provider.activate()
    try:
        assert provider.server is not None
        assert (await provider.health())["http_url"] == provider.server.url
        async with Client(f"{provider.server.url}/mcp") as client:
            assert await client.list_tools() == []
    finally:
        await provider.dispose()
    assert provider.server.task is not None and provider.server.task.done()


@pytest.mark.asyncio
async def test_clients_list_and_call_catalog_tools(context: RuntimeContext):
    provider = McpProvider(context)
    callback = AsyncMock(return_value={"value": "ok"})
    context.resource_call = callback
    await provider.activate()
    assert provider.server is not None
    try:
        input_schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        await provider.prepare(
            [
                owned(
                    "alpha",
                    "echo",
                    input_schema=input_schema,
                    output_schema=output_schema,
                )
            ],
            "epoch-1",
        )
        await provider.commit("epoch-1")
        async with (
            Client(f"{provider.server.url}/mcp") as first,
            Client(f"{provider.server.url}/mcp") as second,
        ):
            first_tools = await first.list_tools()
            second_tools = await second.list_tools()
            assert [tool.name for tool in first_tools] == ["alpha__echo"]
            assert [tool.name for tool in second_tools] == ["alpha__echo"]
            assert first_tools[0].title == "Echo"
            assert first_tools[0].description == "Echo"
            assert first_tools[0].input_schema == input_schema
            assert first_tools[0].output_schema == output_schema
            result = await first.call_tool("alpha__echo", {"value": "input"})
            assert result.structured_content == {"value": "ok"}
            callback.assert_awaited_once_with(
                "alpha/mcp.tool/echo",
                "echo",
                {"value": "input"},
                epoch="epoch-1",
            )
    finally:
        await provider.dispose()


@pytest.mark.asyncio
async def test_invalid_input_and_business_error_are_tool_errors(
    context: RuntimeContext,
):
    provider = McpProvider(context)
    callback = AsyncMock(side_effect=PluginError("business_failed", "Nope"))
    context.resource_call = callback
    await provider.activate()
    assert provider.server is not None
    try:
        await provider.prepare(
            [
                owned(
                    "alpha",
                    "echo",
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                )
            ],
            "epoch-1",
        )
        await provider.commit("epoch-1")
        async with Client(f"{provider.server.url}/mcp") as client:
            invalid = await client.call_tool(
                "alpha__echo", {"value": 3}, raise_on_error=False
            )
            assert invalid.is_error
            assert callback.await_count == 0
            failed = await client.call_tool(
                "alpha__echo", {"value": "ok"}, raise_on_error=False
            )
            assert failed.is_error
            assert "business_failed" in failed.content[0].text
    finally:
        await provider.dispose()


@pytest.mark.asyncio
async def test_tool_defaults_and_non_object_output_validation(context):
    provider = McpProvider(context)
    callback = AsyncMock(return_value=["valid"])
    context.resource_call = callback
    await provider.activate()
    try:
        await provider.prepare(
            [
                owned(
                    "alpha",
                    "list",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "default": 50,
                            }
                        },
                    },
                    output_schema={"type": "array", "items": {"type": "string"}},
                )
            ],
            "epoch",
        )
        await provider.commit("epoch")
        async with Client(f"{provider.server.url}/mcp") as client:
            assert not (await client.call_tool("alpha__list", {})).is_error
            assert callback.call_args.args[2] == {"limit": 50}
            callback.return_value = [123]
            assert (
                await client.call_tool("alpha__list", {}, raise_on_error=False)
            ).is_error
    finally:
        await provider.dispose()


@pytest.mark.asyncio
async def test_prepare_abort_commit_and_withdrawal_are_atomic(context: RuntimeContext):
    provider = McpProvider(context)
    await provider.activate()
    assert provider.server is not None
    try:
        await provider.prepare([owned("alpha", "old")], "epoch-1")
        await provider.commit("epoch-1")
        await provider.prepare([owned("beta", "new")], "epoch-2")
        async with Client(f"{provider.server.url}/mcp") as client:
            assert [tool.name for tool in await client.list_tools()] == ["alpha__old"]
            await provider.abort("epoch-2")
            assert [tool.name for tool in await client.list_tools()] == ["alpha__old"]
            await provider.prepare([owned("beta", "new")], "epoch-3")
            await provider.commit("epoch-3")
            assert [tool.name for tool in await client.list_tools()] == ["beta__new"]
            assert (await provider.health())["epoch"] == "epoch-3"
    finally:
        await provider.dispose()


@pytest.mark.asyncio
async def test_replacement_provider_serves_new_catalog_to_reconnecting_clients(
    context: RuntimeContext,
):
    old_provider = McpProvider(context)
    await old_provider.activate()
    assert old_provider.server is not None
    try:
        await old_provider.prepare([owned("alpha", "old")], "epoch-1")
        await old_provider.commit("epoch-1")
        async with (
            Client(f"{old_provider.server.url}/mcp") as first,
            Client(f"{old_provider.server.url}/mcp") as second,
        ):
            assert [tool.name for tool in await first.list_tools()] == ["alpha__old"]
            assert [tool.name for tool in await second.list_tools()] == ["alpha__old"]
    finally:
        await old_provider.dispose()

    new_provider = McpProvider(context)
    await new_provider.activate()
    assert new_provider.server is not None
    try:
        await new_provider.prepare([owned("alpha", "new")], "epoch-2")
        await new_provider.commit("epoch-2")
        async with (
            Client(f"{new_provider.server.url}/mcp") as first,
            Client(f"{new_provider.server.url}/mcp") as second,
        ):
            assert [tool.name for tool in await first.list_tools()] == ["alpha__new"]
            assert [tool.name for tool in await second.list_tools()] == ["alpha__new"]
    finally:
        await new_provider.dispose()


@pytest.mark.asyncio
async def test_invalid_prepare_leaves_live_catalog_and_unknown_commit_fails(
    context: RuntimeContext,
):
    provider = McpProvider(context)
    await provider.activate()
    assert provider.server is not None
    try:
        await provider.prepare([owned("alpha", "live")], "live")
        await provider.commit("live")
        invalid = [
            [owned("Alpha", "tool")],
            [owned("alpha", "tool"), owned("alpha", "tool")],
            [
                OwnedResource(
                    owner="alpha",
                    generation="generation-1",
                    resource=Resource(
                        id="page",
                        kind="dashboard.page",
                        data={"entrypoint": "example:page"},
                    ),
                )
            ],
        ]
        async with Client(f"{provider.server.url}/mcp") as client:
            for resources in invalid:
                with pytest.raises(PluginError):
                    await provider.prepare(resources, "candidate")
                assert [tool.name for tool in await client.list_tools()] == [
                    "alpha__live"
                ]
        with pytest.raises(PluginError, match="Unknown staged epoch") as error:
            await provider.commit("missing")
        assert error.value.code == "unknown_epoch"
    finally:
        await provider.dispose()
