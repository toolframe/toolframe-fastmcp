#!/usr/bin/env python3
"""Exercise an installed wheel from outside the source tree, using python -I."""

import argparse
import asyncio
import json
import sys
from importlib.metadata import distribution, version
from pathlib import Path

import cryptography
import fastmcp
import toolframe_sdk
from cryptography.fernet import Fernet
from fastmcp import Client
from toolframe_sdk import RuntimeContext

import toolframe_fastmcp
from toolframe_fastmcp import McpProvider, plugin


async def smoke(expected_version):
    expected = {
        "toolframe-fastmcp": expected_version,
        "toolframe-sdk": "0.2.0",
        "fastmcp": "4.0.0",
        "cryptography": "50.0.1",
        "uvicorn": "0.42.0",
    }
    installed = {name: version(name) for name in expected}
    assert installed == expected, installed
    locations = {}
    for module in (toolframe_fastmcp, toolframe_sdk, fastmcp, cryptography):
        location = Path(module.__file__).resolve()
        assert location.is_relative_to(Path(sys.prefix).resolve()), location
        locations[module.__name__] = str(location)
    [entry] = [
        entry
        for entry in distribution("toolframe-fastmcp").entry_points
        if entry.group == "toolframe.plugins" and entry.name == "fastmcp"
    ]
    assert entry.load() is plugin
    assert plugin.id == "fastmcp"
    cipher = Fernet(Fernet.generate_key())
    assert (
        cipher.decrypt(cipher.encrypt(b"native cryptography smoke"))
        == b"native cryptography smoke"
    )
    provider = McpProvider(
        RuntimeContext(None, plugin_id="fastmcp", generation="wheel-smoke")
    )
    await provider.activate()
    try:
        async with Client(f"{provider.server.url}/mcp") as client:
            assert await client.list_tools() == []
        assert (await provider.health())["runtime"] == "ready"
    finally:
        await provider.dispose()
    return {
        "versions": installed,
        "installed_modules": locations,
        "http_smoke": "passed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(smoke(args.expected_version))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
