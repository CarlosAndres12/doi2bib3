"""MCP server exposing doi2bib3 capabilities as tools for AI coding assistants.

The server runs over stdio using the Model Context Protocol. It is an optional
component: import the ``mcp`` SDK extra (``pip install doi2bib3[mcp]``) before
launching the ``doi2bib3-mcp`` entry point.
"""

__all__ = ["main"]