"""Integration test exercising the stdio MCP server loop (task 7.4).

Launches the server as a subprocess via the mcp client SDK and verifies
tools/list and tools/call work end-to-end. Requires the [mcp] extra.
"""

import asyncio
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="mcp SDK requires Python >=3.10",
)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp extra not installed")


@pytest.fixture
def server_params():
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "doi2bib3.mcp_server"],
    )


def test_list_tools(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {t.name for t in result.tools}
                return names
    names = asyncio.run(run())
    assert names == {
        "audit_bib_file",
        "resolve_reference",
        "normalize_bibtex_entry",
        "repair_bib_file_inplace",
        "cache_stats",
    }


def test_call_normalize_bibtex_entry(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool(
                    "normalize_bibtex_entry",
                    {"bibtex_string": "@article{x, title={Hello World}, journal={Physical Review B}, year={2020}}"},
                )
                return res
    res = asyncio.run(run())
    assert res.isError is False
    text = res.content[0].text
    assert "Phys. Rev. B" in text  # APS abbreviation applied


def test_call_resolve_reference_validation_error(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("resolve_reference", {})
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    assert "validation_error" in text


def test_call_audit_bib_file_path_error(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("audit_bib_file", {"path": "/nonexistent/x.bib"})
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    assert "path_error" in text


def test_call_unknown_tool(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("does_not_exist", {})
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    assert "Unknown tool" in text


def test_list_tools_includes_repair(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.list_tools()
                return {t.name for t in result.tools}
    names = asyncio.run(run())
    assert "repair_bib_file_inplace" in names


def test_call_repair_validation_error(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("repair_bib_file_inplace", {})
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    # The SDK's JSON-schema validation catches the missing required 'path'
    # before the handler runs; accept either the SDK's message or our
    # structured validation_error.
    assert "validation" in text.lower() or "required" in text.lower()


def test_call_repair_path_error(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool(
                    "repair_bib_file_inplace", {"path": "/nonexistent/x.bib"}
                )
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    assert "path_error" in text


def test_call_cache_stats(server_params):
    async def run():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("cache_stats", {})
                return res
    res = asyncio.run(run())
    text = res.content[0].text
    import json
    data = json.loads(text)
    stats = data.get("data", {})
    assert "entries" in stats
    assert "hit_rate" in stats
    assert "db_path" in stats