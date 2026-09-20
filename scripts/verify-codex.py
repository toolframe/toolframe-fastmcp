#!/usr/bin/env python3
"""Exercise a local Toolframe endpoint through the installed Codex MCP client.

Uses Codex's public app-server APIs, with an ephemeral thread and no model turn.
Requires an already-running disposable application with the Entry Register tool.
"""

import argparse
import asyncio
import json
import os
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlparse


async def verify(args):
    parsed = urlparse(args.url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("This acceptance probe accepts only a local HTTP endpoint")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_file = codex_home / "config.toml"
    config = tomllib.loads(config_file.read_text()) if config_file.is_file() else {}
    command = [args.codex, "app-server", "--stdio"]
    # Override only this child process. Never add/remove persistent servers or copy auth.
    for name in config.get("plugins", {}):
        command += ["-c", f"plugins.{json.dumps(name)}.enabled=false"]
    for name in ("apps", "hooks", "multi_agent", "shell_tool"):
        command += ["-c", f"features.{name}=false"]
    command += [
        "-c",
        "mcp_servers={toolframe_acceptance="
        + "{enabled=true,required=true,startup_timeout_sec=15,tool_timeout_sec=30,url="
        + json.dumps(args.url)
        + "}}",
    ]
    report = {
        "codex_version": args.codex_version,
        "transport": "streamable_http",
        "endpoint": args.url,
        "client_surface": "Codex app-server MCP APIs; no model turn or GUI automation",
    }
    with args.output.with_suffix(".stderr").open("w") as error_log:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=error_log,
            limit=8 * 1024 * 1024,
        )
        sequence = 0

        async def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def request(method, params):
            nonlocal sequence
            sequence += 1
            await send({"id": sequence, "method": method, "params": params})
            async with asyncio.timeout(45):
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise RuntimeError("Codex app-server closed its control pipe")
                    reply = json.loads(line)
                    if "method" in reply and "id" in reply:
                        # Unexpected prompts require the actual client/operator to decide.
                        raise RuntimeError(
                            f"Codex requested client input: {reply['method']}"
                        )
                    if reply.get("id") == sequence:
                        if "error" in reply:
                            raise RuntimeError(f"{method}: {reply['error']}")
                        return reply["result"]

        try:
            report["initialize"] = await request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "toolframe_acceptance",
                        "title": "Toolframe MCP acceptance",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            await send({"method": "initialized", "params": {}})
            started = await request(
                "thread/start",
                {
                    "cwd": str(args.output.parent.resolve()),
                    "ephemeral": True,
                    "approvalPolicy": "on-request",
                    "sandbox": "read-only",
                },
            )
            thread = started["thread"]["id"]
            report["thread_id"] = thread
            inventory = await request(
                "mcpServerStatus/list",
                {"threadId": thread, "detail": "toolsAndAuthOnly", "limit": 100},
            )
            server = next(
                item
                for item in inventory["data"]
                if item["name"] == "toolframe_acceptance"
            )
            report["server"] = server
            assert not server.get("toolsError"), server
            params = {
                "threadId": thread,
                "server": "toolframe_acceptance",
                "tool": args.tool,
            }
            if args.expect_absent:
                assert args.tool not in server["tools"], server
                try:
                    withdrawn = await request(
                        "mcpServer/tool/call", {**params, "arguments": {"limit": 5}}
                    )
                except RuntimeError as error:
                    withdrawn = {"error": str(error)}
                else:
                    assert withdrawn.get("isError") is True, withdrawn
                assert any(
                    marker in json.dumps(withdrawn).lower()
                    for marker in ("unknown tool", "not found", "does not exist")
                ), withdrawn
                report.update(withdrawn_call=withdrawn, passed=True)
                print(
                    f"Codex MCP: {args.tool} is absent and its call is rejected",
                    flush=True,
                )
                return
            tool = server["tools"][args.tool]
            assert (
                tool["inputSchema"]["properties"]["limit"]["maximum"]
                == args.expected_max
            )
            result = await request(
                "mcpServer/tool/call", {**params, "arguments": {"limit": 5}}
            )
            report["valid_call"] = result
            assert not result.get("isError"), result
            assert result["structuredContent"]["count"] == args.expected_count, result
            invalid = await request(
                "mcpServer/tool/call",
                {**params, "arguments": {"limit": args.expected_max + 1}},
            )
            report["invalid_call"] = invalid
            assert invalid.get("isError") is True, invalid
            report["passed"] = True
            print(
                f"Codex MCP: discovered {args.tool}, maximum={args.expected_max}; valid call and schema rejection passed",
                flush=True,
            )
        finally:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.terminate()
                await asyncio.wait_for(process.wait(), 10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--url", required=True)
    parser.add_argument("--tool", default="entry-register__list_entries")
    parser.add_argument("--expected-max", type=int, default=100)
    parser.add_argument("--expected-count", type=int, default=0)
    parser.add_argument("--expect-absent", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("dist/codex-evidence.json"))
    args = parser.parse_args()
    args.codex_version = subprocess.check_output(
        [args.codex, "--version"], text=True
    ).strip()
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
