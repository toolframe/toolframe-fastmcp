#!/usr/bin/env python3
"""Exercise real MCP sessions and llms withdrawal against a disposable core home."""

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from fastmcp import Client


def fixture(root, name, behavior, catalog=1):
    package = f"fixture_{name}"
    source = root / name
    module = source / "src" / package
    module.mkdir(parents=True, exist_ok=True)
    (source / "pyproject.toml").write_text(
        f'''[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"
[project]
name = "toolframe-gateway-{name}"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["toolframe-sdk>=0.4.0,<1"]
[project.entry-points."toolframe.plugins"]
{name} = "{package}:plugin"
[tool.hatch.build.targets.wheel]
packages = ["src/{package}"]
'''
    )
    maximum = 100 if catalog == 1 else 10
    schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": maximum,
                "default": 1,
            },
            "delay": {"type": "number", "minimum": 0, "maximum": 10, "default": 0},
        },
        "additionalProperties": False,
    }
    resources = []
    if name == "alpha":
        tool_ids = (
            ["echo"]
            if catalog == 1
            else ["echo", "extra"]
            if catalog == 2
            else ["extra"]
        )
        for identifier in tool_ids:
            resources.append(
                {
                    "id": identifier,
                    "kind": "mcp.tool",
                    "title": identifier,
                    "data": {
                        "service": f"{name}.service",
                        "method": "echo",
                        "input_schema": schema,
                        "output_schema": {"type": "object"},
                    },
                }
            )
        if catalog < 3:
            resources.append(
                {
                    "id": "usage",
                    "kind": "llms.document",
                    "title": "Gateway fixture",
                    "data": {"package": package, "path": "usage.md"},
                }
            )
    (module / "usage.md").write_text(
        "# Gateway fixture\n\nExplicitly packaged documentation.\n"
    )
    (module / "__init__.py").write_text(f"""import asyncio
from toolframe_sdk import Plugin, PluginDefinition, ServiceSpec, MethodSpec, Resource
plugin = PluginDefinition(id={name!r}, factory={package + ":Example"!r},
    services={{{name + ".service"!r}: ServiceSpec(methods={{"echo": MethodSpec(
        input_schema={schema!r}, output_schema={{"type":"object"}}, read_only=True)}})}},
    resources=tuple(Resource.model_validate(value) for value in {resources!r}))
class Example(Plugin):
    async def invoke(self, service, method, params):
        await asyncio.sleep(params.get("delay", 0))
        return {{"behavior": {behavior!r}, "limit": params.get("limit", 1)}}
""")
    return source


async def verify(args):
    root = Path(tempfile.mkdtemp(prefix="toolframe-mcp-gateway-"))
    home = root / "app"
    binary = args.binary.resolve()
    wheels = args.wheelhouse.resolve()
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    launch = [
        str(binary),
        "--uv-path",
        shutil.which("uv"),
        "--wheelhouse",
        str(wheels),
        "--home",
        str(home),
    ]
    environment = {**os.environ, "TOOLFRAME_RUNTIME_ROOT": str(root / "runtime")}
    with (root / "core.log").open("w") as log:
        await asyncio.to_thread(
            subprocess.run,
            [*launch, "init", "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=log,
            check=True,
            timeout=180,
        )
        alpha = fixture(root, "alpha", "old")
        beta = fixture(root, "beta", "unrelated")
        with (home / "toolframe.toml").open("a") as config:
            # init emits an empty [plugins] table; explicit subtables extend it.
            for name, source in {
                "fastmcp": "pypi:toolframe-fastmcp==0.3.1",
                "llms-txt": "pypi:toolframe-llms-txt==0.3.0",
                "alpha": str(alpha),
                "beta": str(beta),
            }.items():
                config.write(
                    f"\n[plugins.{name}]\nsource={json.dumps(source)}\nenabled=true\n"
                )
        server = await asyncio.to_thread(
            subprocess.Popen,
            [*launch, "serve"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:

            async def request(path, data=None):
                def send():
                    token = (home / ".toolframe/management.token").read_text().strip()
                    req = urllib.request.Request(
                        base + path,
                        data=json.dumps(data).encode() if data is not None else None,
                        headers={
                            "Authorization": "Bearer " + token,
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.load(response)

                return await asyncio.to_thread(send)

            async def status():
                return await request("/_toolframe/v1/status")

            async def operation(kind, *, running_call=None, **params):
                op = await request(
                    "/_toolframe/v1/operations", {"action": kind, "params": params}
                )
                observed_phases = set()
                drained_live_call = False
                async with asyncio.timeout(120):
                    while op["status"] in {"queued", "running"}:
                        observed_phases.add(op["phase"])
                        if (
                            op["phase"] == "draining"
                            and running_call is not None
                            and not running_call.done()
                        ):
                            drained_live_call = True
                        await asyncio.sleep(0.05)
                        op = await request("/_toolframe/v1/operations/" + op["id"])
                assert op["status"] == "succeeded", op
                op["observed_phases"] = sorted(observed_phases)
                if running_call is not None:
                    assert drained_live_call, (
                        "Reload never overlapped the admitted call"
                    )
                    op["drained_live_call"] = True
                return op

            async with asyncio.timeout(180):
                while True:
                    assert server.poll() is None, (
                        f"Core exited; see {root / 'core.log'}"
                    )
                    try:
                        initial = await status()
                        if (
                            len(initial["plugins"]) == 4
                            and initial["application"] in {"ready", "degraded"}
                            and all(
                                item["runtime"].get("state") == "active"
                                for item in initial["plugins"]
                            )
                        ):
                            break
                        failures = [
                            op
                            for op in initial["operations"]
                            if op["status"] == "failed"
                        ]
                        assert not failures, failures
                    except OSError, ValueError:
                        pass
                    await asyncio.sleep(0.1)
            pids = lambda state: {
                item["id"]: item["runtime"]["pid"] for item in state["plugins"]
            }
            report = {"workspace": str(root), "initial": initial, "cases": []}
            async with Client(base + "/mcp") as first, Client(base + "/mcp") as second:
                tools = await first.list_tools()
                assert len(tools) == 1
                original_name = tools[0].name
                fixture(root, "alpha", "new")
                plan = await operation("plan", action="reload", id="alpha")
                slow = asyncio.create_task(first.call_tool(original_name, {"delay": 5}))
                await asyncio.sleep(0.2)
                op = await operation(
                    "reload",
                    id="alpha",
                    plan_id=plan["result"]["plan_id"],
                    running_call=slow,
                )
                assert (await slow).structured_content["behavior"] == "old"
                assert (await second.call_tool(original_name, {})).structured_content[
                    "behavior"
                ] == "new"
                after_backend = await status()
                assert pids(initial)["alpha"] != pids(after_backend)["alpha"]
                assert pids(initial)["fastmcp"] == pids(after_backend)["fastmcp"]
                assert initial["pid"] == after_backend["pid"]
                report["cases"].append(
                    {
                        "case": "backend_only_with_admitted_call",
                        "operation": op,
                        "after": after_backend,
                    }
                )
                fixture(root, "alpha", "new", catalog=2)
                plan = await operation("plan", action="reload", id="alpha")
                slow = asyncio.create_task(
                    second.call_tool(original_name, {"delay": 5})
                )
                await asyncio.sleep(0.2)
                op = await operation(
                    "reload",
                    id="alpha",
                    plan_id=plan["result"]["plan_id"],
                    running_call=slow,
                )
                assert (await slow).structured_content["behavior"] == "new"
                after_catalog = await status()
                assert pids(after_backend)["fastmcp"] != pids(after_catalog)["fastmcp"]
                report["cases"].append(
                    {
                        "case": "tool_add_and_schema_change",
                        "operation": op,
                        "after": after_catalog,
                    }
                )
            clients = []
            for number in range(2):
                async with Client(base + "/mcp") as client:
                    tools = await client.list_tools()
                    assert len(tools) == 2
                    assert all(
                        tool.input_schema["properties"]["limit"]["maximum"] == 10
                        for tool in tools
                    )
                    bad = await client.call_tool(
                        original_name, {"limit": 11}, raise_on_error=False
                    )
                    assert bad.is_error
                    clients.append(
                        {
                            "client": number + 1,
                            "tools": [tool.model_dump() for tool in tools],
                            "old_schema_rejected": bad.is_error,
                        }
                    )
            report["cases"][-1]["reconnected_clients"] = clients

            def read_text(path):
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    return response.read().decode()

            assert "Gateway fixture" in await asyncio.to_thread(
                read_text, "/llms/alpha/usage.md"
            )
            fixture(root, "alpha", "new", catalog=3)
            op = await operation("reload", id="alpha")
            for _ in range(2):
                async with Client(base + "/mcp") as client:
                    tools = await client.list_tools()
                    assert len(tools) == 1 and tools[0].name != original_name
                    removed = await client.call_tool(
                        original_name, {}, raise_on_error=False
                    )
                    assert removed.is_error
            assert "Gateway fixture" not in await asyncio.to_thread(
                read_text, "/llms.txt"
            )
            try:
                await asyncio.to_thread(read_text, "/llms/alpha/usage.md")
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("Withdrawn documentation was still served")
            final = await status()
            assert pids(initial)["beta"] == pids(final)["beta"]
            assert pids(initial)["llms-txt"] == pids(final)["llms-txt"]
            assert initial["pid"] == final["pid"]
            report["cases"].append(
                {
                    "case": "tool_and_document_withdrawal",
                    "operation": op,
                    "after": final,
                    "removed_tool_rejected_by_clients": 2,
                    "document_status": 404,
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(
                f"Passed 3 gateway scenarios with two MCP clients; evidence: {args.output}",
                flush=True,
            )
        finally:
            server.send_signal(signal.SIGTERM)
            await asyncio.to_thread(server.wait, timeout=40)
            assert server.returncode in (0, -signal.SIGTERM), root / "core.log"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("dist/gateway-evidence.json")
    )
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
