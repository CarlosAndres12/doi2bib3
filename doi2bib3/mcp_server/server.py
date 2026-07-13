"""MCP stdio server exposing doi2bib3-backed tools.

Uses the low-level ``mcp`` SDK API for explicit control over tool input schemas.
Tool logic lives in :mod:`doi2bib3.mcp_server.tools`; this module only wires the
SDK transport and translates ``ToolResult`` objects into MCP content responses.

The ``mcp`` SDK is imported lazily so that importing this module without the
``[mcp]`` extra installed does not crash — the actionable error is raised by
:mod:`doi2bib3.mcp_server.__main__` instead.
"""

from __future__ import annotations

import json
from typing import Any

from . import tools
from .adapter import ToolResult


SERVER_NAME = "doi2bib3-mcp"
SERVER_VERSION = "1.0.0"


def _tool_definitions() -> list[dict[str, Any]]:
    """Return the list of MCP tool definitions (name, description, inputSchema)."""
    return [
        {
            "name": "audit_bib_file",
            "description": (
                "Parse a local .bib file and cross-reference each entry that has a "
                "DOI or title against canonical metadata fetched via doi2bib3. "
                "Reports missing, mismatched, malformed, or unavailable fields as a "
                "structured per-entry report plus a summary."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path to a .bib file.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Per-request network timeout in seconds.",
                        "default": 30,
                    },
                    "refresh_cache": {
                        "type": "boolean",
                        "description": (
                            "If true, bypass the reference cache and force a fresh "
                            "network fetch for every identifier."
                        ),
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "resolve_reference",
            "description": (
                "Fetch a clean, canonical BibTeX string for a specific item by DOI "
                "or free-text query via doi2bib3 lookup routines. Prefers 'doi' when "
                "both 'doi' and 'query' are provided."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doi": {
                        "type": "string",
                        "description": "A DOI (or DOI URL/arXiv id) to resolve.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Free-text query (e.g. article title) to resolve.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Per-request network timeout in seconds.",
                        "default": 30,
                    },
                    "refresh_cache": {
                        "type": "boolean",
                        "description": "If true, bypass cache and force a fresh fetch.",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
        {
            "name": "normalize_bibtex_entry",
            "description": (
                "Sanitize a BibTeX entry string via doi2bib3.normalize: fix casing, "
                "apply publisher-specific text replacements (Nature, APS, IOP), and "
                "return formatted BibTeX. Preserves all original fields except those "
                "explicitly transformed by normalization rules."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bibtex_string": {
                        "type": "string",
                        "description": "A single BibTeX entry string to normalize.",
                    },
                    "refresh_cache": {
                        "type": "boolean",
                        "description": "If true, bypass cache and force fresh normalization.",
                        "default": False,
                    },
                },
                "required": ["bibtex_string"],
            },
        },
        {
            "name": "repair_bib_file_inplace",
            "description": (
                "Repair a local .bib file in place: resolve canonical metadata for "
                "each entry via doi2bib3, optionally normalize, and rewrite the file "
                "atomically. A .bak backup is created first and retained as a "
                "fallback; on write failure the original is restored from the backup."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path to a .bib file.",
                    },
                    "auto_normalize": {
                        "type": "boolean",
                        "description": (
                            "If true, apply doi2bib3.normalize (casing, publisher "
                            "replacements) to each resolved entry. Default false."
                        ),
                        "default": False,
                    },
                    "overwrite_backup": {
                        "type": "boolean",
                        "description": (
                            "If true, overwrite an existing ${path}.bak. By "
                            "default an existing .bak blocks the repair."
                        ),
                        "default": False,
                    },
                    "group_output": {
                        "type": "boolean",
                        "description": (
                            "If true (default), group entries into sections "
                            "(VERIFIED / NOT FOUND / NO IDENTIFIER / UNAVAILABLE) "
                            "with % STATUS comment lines. Set false for flat output."
                        ),
                        "default": True,
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Per-request network timeout in seconds.",
                        "default": 30,
                    },
                    "refresh_cache": {
                        "type": "boolean",
                        "description": "If true, bypass cache and force fresh fetches.",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "cache_stats",
            "description": (
                "Return summary statistics about the reference cache: entry count, "
                "hit rate, oldest/newest fetch timestamps, database path and size."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    ]


def _dispatch(
    name: str,
    arguments: dict[str, Any] | None,
    progress_callback: tools.ProgressCallback | None = None,
) -> ToolResult:
    """Route a tool call to the appropriate handler in :mod:`tools`."""
    arguments = arguments or {}
    refresh_cache = bool(arguments.get("refresh_cache", False))

    if name == "audit_bib_file":
        path = arguments.get("path")
        timeout = int(arguments.get("timeout") or tools.DEFAULT_TIMEOUT)
        return tools.audit_bib_file(
            path=path,
            timeout=timeout,
            refresh_cache=refresh_cache,
            progress_callback=progress_callback,
        )

    if name == "resolve_reference":
        doi = arguments.get("doi")
        query = arguments.get("query")
        timeout = int(arguments.get("timeout") or tools.DEFAULT_TIMEOUT)
        return tools.resolve_reference(doi=doi, query=query, timeout=timeout, refresh_cache=refresh_cache)

    if name == "normalize_bibtex_entry":
        bibtex_string = arguments.get("bibtex_string")
        return tools.normalize_bibtex_entry(bibtex_string=bibtex_string, refresh_cache=refresh_cache)

    if name == "repair_bib_file_inplace":
        from . import repair
        path = arguments.get("path")
        auto_normalize = bool(arguments.get("auto_normalize", False))
        overwrite_backup = bool(arguments.get("overwrite_backup", False))
        group_output = arguments.get("group_output")
        if group_output is not None:
            group_output = bool(group_output)
        else:
            group_output = True
        timeout = int(arguments.get("timeout") or tools.DEFAULT_TIMEOUT)
        return repair.repair_bib_file_inplace(
            path=path,
            auto_normalize=auto_normalize,
            overwrite_backup=overwrite_backup,
            timeout=timeout,
            refresh_cache=refresh_cache,
            group_output=group_output,
            progress_callback=progress_callback,
        )

    if name == "cache_stats":
        from . import cache
        data = cache.stats()
        return ToolResult(kind="resolved", ok=True, data=data)

    return ToolResult(
        kind="validation_error",
        ok=False,
        error=f"Unknown tool: {name}",
    )


def _result_to_payload(result: ToolResult) -> tuple[str, bool]:
    """Convert a ToolResult into (text, is_error) for an MCP TextContent response."""
    payload = result.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return text, not result.ok


def build_server():
    """Construct and return the low-level MCP Server with handlers registered.

    Imports the ``mcp`` SDK lazily so that callers without the ``[mcp]`` extra
    get a clear error from ``__main__`` rather than an import traceback here.
    """
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=defn["name"],
                description=defn["description"],
                inputSchema=defn["inputSchema"],
            )
            for defn in _tool_definitions()
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        # Build a progress callback if the client provided a progressToken via
        # the MCP request metadata.  Notifications are scheduled via
        # asyncio.create_task and will be flushed once the event loop regains
        # control (i.e. after the synchronous _dispatch returns).
        progress_callback = None
        try:
            from mcp.server.lowlevel.server import request_ctx
            import asyncio as _asyncio_impl
            ctx = request_ctx.get()
            meta = getattr(ctx, "meta", None) or {}
            progress_token = meta.get("progressToken") if isinstance(meta, dict) else None
            if progress_token is not None:
                session = ctx.session
                loop = _asyncio_impl.get_running_loop()

                def _send_progress(payload: dict[str, Any]) -> None:
                    try:
                        progress = (payload["index"] + 1) / payload["total"]
                        _asyncio_impl.create_task(
                            session.send_progress_notification(
                                progress_token=progress_token,
                                progress=progress,
                                total=float(payload["total"]),
                                message=payload.get("message"),
                            ),
                        )
                    except Exception:
                        pass  # never let a progress notification abort the tool

                progress_callback = _send_progress
        except Exception:
            pass  # no _meta or request_ctx unavailable — no progress

        result = _dispatch(name, arguments, progress_callback=progress_callback)
        text, _is_error = _result_to_payload(result)
        # Errors are returned as content with isError via CallToolResult for
        # full control, so the client sees a structured error rather than a
        # protocol-level failure.
        if not result.ok:
            return [
                types.TextContent(type="text", text=text),
            ]
        return [types.TextContent(type="text", text=text)]

    return server


async def run_stdio_async() -> None:
    """Start the stdio server loop."""
    import mcp.server.stdio
    from mcp.server.lowlevel import NotificationOptions
    from mcp.server.models import InitializationOptions

    server = build_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> int:
    """Synchronous entry point: run the async stdio loop."""
    import asyncio

    asyncio.run(run_stdio_async())
    return 0