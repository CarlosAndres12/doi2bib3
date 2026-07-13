# doi2bib3 MCP Server

`doi2bib3` ships an optional **Model Context Protocol (MCP)** server that lets
AI coding assistants (Claude Code, Cursor, Windsurf, etc.) audit, resolve,
normalize, repair, and inspect BibTeX bibliographies directly inside your
workspace.

The server runs over **stdio** and exposes five tools backed by `doi2bib3`'s
existing backend and normalize modules.

## Installation

The MCP server depends on the official `mcp` Python SDK, which is kept out of
the base install to avoid pulling extra dependencies for users who only need
the library or CLI. Install it with the optional `[mcp]` extra:

```bash
pip install "doi2bib3[mcp]"
```

This adds the `doi2bib3-mcp` console command. `bibtexparser` is already a core
dependency of `doi2bib3`, so no extra parser install is needed.

### One-command install for opencode

To register the server with **opencode** in one step (creates a venv, installs
the `[mcp]` extra, and writes the opencode config block), run from the repo
root:

```bash
scripts/install-opencode-mcp
```

This writes a `doi2bib3` MCP block to the workspace `opencode.json`. Flags:

| Flag             | Description                                                        |
|------------------|--------------------------------------------------------------------|
| `--global`       | Write to `~/.config/opencode/opencode.jsonc` instead of workspace. |
| `--venv <path>`  | Override the venv location (default `~/.venvs/doi2bib3-mcp`).      |
| `--force`        | Overwrite an existing `doi2bib3` MCP block in the config.           |
| `--no-install`   | Skip venv/pip; only write the config block.                        |
| `--no-editable`  | Normal (non-editable) pip install instead of `-e`.                 |

The script is idempotent: re-running it updates the venv and config without
duplicating the block. An existing `doi2bib3` block is preserved unless
`--force` is passed. For the global JSONC config, comments and other config
are preserved.

## Running the server

```bash
doi2bib3-mcp
```

The server reads JSON-RPC messages from stdin and writes responses to stdout.
It takes no required arguments. AI clients launch it as a subprocess and speak
MCP over its stdio streams.

If the `[mcp]` extra is not installed, the command exits non-zero with an
actionable message:

```
doi2bib3-mcp requires the 'mcp' extra. Install it with:
    pip install doi2bib3[mcp]
(missing dependency: mcp)
```

For editable / non-installed repository checkouts, a shim is also provided:

```bash
python3 scripts/doi2bib3-mcp
```

The server accepts an optional `--cache-db <path>` flag to override the default
reference cache location.

## Reference cache

`doi2bib3-mcp` caches resolved BibTeX references in a local SQLite database so
repeated lookups are instant and deterministic within the TTL. The cache is
transparent — all five tools (audit, resolve, normalize, repair, cache_stats)
benefit automatically.

**Cache location** (resolved in order):
1. `--cache-db <path>` CLI flag
2. `DOI2BIB3_CACHE` environment variable
3. `${XDG_CACHE_HOME:-~/.cache}/doi2bib3/references.db` (default)

**TTL:** 7 days by default, overridable via `DOI2BIB3_CACHE_TTL` env var (seconds).

**Stale-on-error:** if a cached entry is expired but a fresh network fetch fails
(e.g. offline), the server returns the stale entry with `"stale": true` in the
result rather than reporting `unavailable`.

**Force refresh:** set `"refresh_cache": true` on any tool call to bypass the
cache and force a fresh network fetch. The cache is updated with the new result.

**Cache database:** SQLite with WAL mode, safe for concurrent readers. Schema
version is auto-managed — if the format changes, the DB is dropped and
recreated (it's a cache, not a source of truth).

**Monitoring:** use the `cache_stats` tool to inspect cache health (entry count,
hit rate, timestamps, DB path and size).

**Clearing:** delete `~/.cache/doi2bib3/references.db` to start fresh.

## Tools

All tool results are returned as a JSON-text `TextContent` block with the
shape:

```json
{ "kind": "<outcome>", "ok": <bool>, "data": <payload>, "error": "<msg>" }
```

`ok` is `true` for successful outcomes and `false` otherwise; `error` is
present only when `ok` is `false`.

### `audit_bib_file`

Parses a local `.bib` file and cross-references each entry that has a DOI or
title against canonical metadata fetched via `doi2bib3`. Reports missing,
mismatched, malformed, or unavailable fields.

**Arguments:**

| Argument  | Type    | Required | Description                                      |
|-----------|---------|----------|--------------------------------------------------|
| `path`    | string  | yes      | Relative or absolute path to a `.bib` file.     |
| `timeout` | integer | no       | Per-request network timeout in seconds (default 30). |

**Result `data`:**

```json
{
  "entries": [
    {
      "key": "<bibtex key>",
      "type": "<entry type, e.g. article>",
      "doi": "<doi or null>",
      "issues": [
        { "field": "<field name>", "severity": "<missing|mismatch|malformed|unavailable>",
          "expected": "<canonical value or null>", "actual": "<file value or null>" }
      ]
    }
  ],
  "summary": { "entries": <int>, "with_issues": <int>, "missing_doi": <int> }
}
```

**Error kinds:** `path_error` (missing / not a file / unreadable).

### `resolve_reference`

Fetches a clean, canonical BibTeX string for a specific item by DOI or
free-text query via `doi2bib3` lookup routines. Prefers `doi` when both are
provided.

**Arguments:**

| Argument  | Type    | Required | Description                                              |
|-----------|---------|----------|----------------------------------------------------------|
| `doi`     | string  | no*      | A DOI (or DOI URL / arXiv id) to resolve.                |
| `query`   | string  | no*      | Free-text query (e.g. article title) to resolve.        |
| `timeout` | integer | no       | Per-request network timeout in seconds (default 30).    |

\*At least one of `doi` or `query` must be provided.

**Result `data`:** the canonical BibTeX entry string.

**Error kinds:** `validation_error` (neither argument provided),
`unresolvable` (identifier could not be resolved), `unavailable` (network /
provider failure — safe to retry).

### `normalize_bibtex_entry`

Sanitizes a BibTeX entry string via `doi2bib3.normalize`: fixes casing, applies
publisher-specific text replacements (Nature, APS, IOP), and returns formatted
BibTeX. Preserves all original fields except those explicitly transformed by
normalization rules.

**Arguments:**

| Argument        | Type   | Required | Description                              |
|-----------------|--------|----------|------------------------------------------|
| `bibtex_string` | string | yes      | A single BibTeX entry string to normalize. |

**Result `data`:** the normalized BibTeX entry string.

**Error kinds:** `validation_error` (empty / missing input),
`parse_error` (input is not a parseable BibTeX entry).

### `repair_bib_file_inplace`

Repairs a `.bib` file in place: resolves canonical metadata for each entry via
`doi2bib3.backend`, optionally normalizes via `doi2bib3.normalize`, and rewrites
the file atomically. A `.bak` backup is created first and retained as a fallback;
on write failure the original is restored from the backup.

**Safety contract:**
1. **Backup-first** — an exact binary copy of `${path}` is written to `${path}.bak`
   before any resolution or write occurs.
2. **Atomic write** — repaired content is written to a temp file and atomically
   moved into place, so the target file is never left half-written.
3. **Rollback-on-failure** — if the write fails, `${path}` is restored from the
   `.bak` snapshot and a `repair_error` is returned.
4. **Existing `.bak`** — by default an existing `${path}.bak` blocks the repair
   (returns `backup_conflict`); set `overwrite_backup` to true to overwrite it.

Per-entry resilience: a resolution/normalization failure for one entry is recorded
as `skipped` (original entry preserved verbatim) and the repair continues for the
rest. Only a total parse failure or write failure aborts the whole pass.

**Arguments:**

| Argument            | Type    | Required | Description                                                                 |
|---------------------|---------|----------|-----------------------------------------------------------------------------|
| `path`              | string  | yes      | Relative or absolute path to a `.bib` file.                                |
| `auto_normalize`   | boolean | no       | Apply `doi2bib3.normalize` (casing, publisher replacements). Default false. |
| `overwrite_backup` | boolean | no       | Overwrite an existing `${path}.bak`. Default false (blocks repair).         |
| `group_output`     | boolean | no       | Group entries into sections with `% STATUS` comment lines. Default true.    |
| `timeout`          | integer | no       | Per-request network timeout in seconds (default 30).                       |

When `group_output=true` (the default), the output `.bib` file is structured
into labeled sections:

```
% ===== VERIFIED =====
% STATUS: VERIFIED doi: 10.1002/andp.19053221004
@article{einstein1905, ...}

% ===== NOT FOUND =====
% STATUS: NOT FOUND doi: 10.9999/bad error: not found
@article{baddoi, ...}

% ===== NO IDENTIFIER =====
% STATUS: NO IDENTIFIER key: nodoi
@misc{nodoi, ...}
```

Sections with zero entries are omitted. Set `group_output=false` for the
original flat format (no comments, no sections).

**Result `data`:**

```json
{
  "entries": [
    { "key": "<bibtex key>", "doi": "<doi or null>", "outcome": "<repaired|skipped|failed>", "error": "<msg or null>" }
  ],
  "summary": { "entries": <int>, "repaired": <int>, "skipped": <int>, "failed": <int> },
  "backup": "<path to the .bak file>"
}
```

**Error kinds:** `validation_error` (empty/missing `path`),
`path_error` (missing/not-a-file/unreadable), `backup_conflict` (existing `.bak`
and `overwrite_backup` not set), `repair_error` (write failed — original restored
from backup).

### `cache_stats`

Return summary statistics about the reference cache.

**Arguments:** none.

**Result `data`:**

```json
{
  "entries": <int>,
  "hit_rate": <float or null>,
  "oldest_fetched": <epoch or null>,
  "newest_fetched": <epoch or null>,
  "db_path": "<absolute path to the SQLite DB>",
  "db_size_bytes": <int>
}
```

### `refresh_cache` flag

All five tools accept an optional `"refresh_cache": true` boolean in their
arguments. When set, the reference cache is bypassed and a fresh network
fetch is forced. The cache is updated with the new result.

## Progress notifications

`audit_bib_file` and `repair_bib_file_inplace` support MCP progress
notifications. When the MCP client provides a `progressToken` in the
`tools/call` request metadata (`_meta.progressToken`), the server emits
`notifications/progress` after each entry is resolved. Each notification
includes:

- `progress`: a float from 0 to 1 (`(entry_index + 1) / total_entries`)
- `total`: the total number of entries in the file
- A message with the entry key, DOI, and outcome (resolved/skipped/failed)

The final notification has `progress` equal to 1.0 with outcome `"complete"`.
If no `progressToken` is provided, no progress notifications are emitted and
the tool result is unchanged. Progress callback failures are silently caught
and never abort the audit or repair.

### `overwrite_backup` flag

The `repair_bib_file_inplace` tool accepts an optional `overwrite_backup`
boolean (default `false`). When an existing `${path}.bak` file is present,
the tool returns a `backup_conflict` error unless `overwrite_backup` is
explicitly set to `true`. The CLI exposes this as both `--force` and
`--overwrite_backup` for convenience.

## Direct Python usage

All MCP tool handlers are importable Python functions that return `ToolResult`
objects. You can call them directly from scripts or a REPL without an MCP
client.

For even simpler command-line access, the `doi2bib3` CLI supports subcommands
(`doi2bib3 audit`, `doi2bib3 repair`, etc.) — see the README.

```python
from doi2bib3.mcp_server.tools import (
    audit_bib_file,
    resolve_reference,
    normalize_bibtex_entry,
)
from doi2bib3.mcp_server.repair import repair_bib_file_inplace
from doi2bib3.mcp_server import cache
```

### Audit a .bib file

```python
r = audit_bib_file("references.bib")
print(r.ok, r.kind)           # True, "resolved"
print(r.data["summary"])       # {entries: 99, with_issues: 42, missing_doi: 5}
for e in r.data["entries"]:
    if e["issues"]:
        print(e["key"], [i["field"] for i in e["issues"]])
```

### Resolve a reference

```python
# By DOI
r = resolve_reference(doi="10.1103/PhysRevB.99.014101")
print(r.data)  # canonical BibTeX string

# By title
r = resolve_reference(query="on the electrodynamics of moving bodies")
print(r.data)  # resolved BibTeX
```

### Normalize a BibTeX entry

```python
bib = "@article{x, title={Hello World}, journal={Physical Review B}, year={2020}}"
r = normalize_bibtex_entry(bib)
print(r.data)  # normalized BibTeX with abbreviated journal name
```

### Repair a .bib file in place

```python
# Basic repair (no normalization)
r = repair_bib_file_inplace("references.bib")
print(r.data["summary"])  # {entries: 99, repaired: 80, skipped: 15, failed: 4}

# Repair with normalization and flat output
r = repair_bib_file_inplace(
    "references.bib",
    auto_normalize=True,
    group_output=False,
    overwrite_backup=True,
)
print(r.data["backup"])  # path to the .bak backup
```

### Check cache health

```python
cache.configure()  # use default DB location
s = cache.stats()
print(s["entries"])       # cached entries count
print(s["hit_rate"])      # e.g. 0.85
print(s["db_size_bytes"]) # SQLite file size
```

## Client configuration examples

See [`mcp-client-configs.md`](mcp-client-configs.md) for ready-to-use
configuration snippets for Claude Code, Cursor, and Windsurf.