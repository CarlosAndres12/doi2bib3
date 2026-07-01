# MCP Client Configurations

Ready-to-use snippets for registering the `doi2bib3-mcp` server with common AI
coding assistants. Replace `/absolute/path/to/doi2bib3-mcp` with the actual path
to the `doi2bib3-mcp` executable on your system (find it with
`which doi2bib3-mcp`), or invoke it via `python -m doi2bib3.mcp_server`.

## Claude Code

Add to `~/.config/claude-code/mcp.json` (or the project-local `.mcp.json`):

```json
{
  "mcpServers": {
    "doi2bib3": {
      "command": "doi2bib3-mcp",
      "args": []
    }
  }
}
```

If `doi2bib3-mcp` is not on `PATH`, use the absolute path:

```json
{
  "mcpServers": {
    "doi2bib3": {
      "command": "/absolute/path/to/doi2bib3-mcp",
      "args": []
    }
  }
}
```

Or run via the module entry point with a specific interpreter:

```json
{
  "mcpServers": {
    "doi2bib3": {
      "command": "python",
      "args": ["-m", "doi2bib3.mcp_server"]
    }
  }
}
```

## Cursor

Add to `~/.cursor/mcp.json` (or the project-local `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "doi2bib3": {
      "command": "doi2bib3-mcp",
      "args": []
    }
  }
}
```

## Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "doi2bib3": {
      "command": "doi2bib3-mcp",
      "args": []
    }
  }
}
```

## Verifying the server

After registering, restart your assistant. The five tools should appear in its
tool list:

- `audit_bib_file`
- `resolve_reference`
- `normalize_bibtex_entry`
- `repair_bib_file_inplace`
- `cache_stats`

## Repair usage & `.bak` safety

`repair_bib_file_inplace` rewrites a `.bib` file in place with verified entries.
Before any write, it creates a binary backup at `${path}.bak` that is retained as
a fallback. If the write fails, the original file is restored from the backup.

```json
{
  "name": "repair_bib_file_inplace",
  "arguments": {
    "path": "references.bib",
    "auto_normalize": true,
    "overwrite_backup": false
  }
}
```

If a `.bak` already exists, the repair is blocked by default to protect your
backup. Set `overwrite_backup: true` to overwrite it.

You can also test the server standalone with the MCP inspector:

```bash
npx -y @modelcontextprotocol/inspector doi2bib3-mcp
```

## Troubleshooting

- **`command not found: doi2bib3-mcp`** — install the extra:
  `pip install "doi2bib3[mcp]"`.
- **`doi2bib3-mcp requires the 'mcp' extra`** — the `[mcp]` extra is missing;
  install it as above.
- **Tool calls return `unavailable`** — a network/provider issue prevented DOI
  lookup; retry later.