# doi2bib3
This is a fork of [archisman-panigrahi/doi2bib3](https://github.com/archisman-panigrahi/doi2bib3) maintained at [CarlosAndres12/doi2bib3](https://github.com/CarlosAndres12/doi2bib3).

doi2bib3 is a small Python utility to fetch BibTeX metadata for a DOI or to
resolve arXiv identifiers to DOIs and fetch their BibTeX entries. It accepts
DOI inputs, DOI URLs, arXiv IDs/URLs (modern and legacy), publisher landing
pages, and uses a sequence of resolution strategies to return a BibTeX string.
This tool combines the features of [doi2bib](https://github.com/bibcure/doi2bib/) and [doi2bib2](https://github.com/davidagraf/doi2bib2).

## Key behaviors

- Accepts DOI, DOI URL, arXiv ID/URL, publisher URL, or article-title text.
- Resolves inputs to a DOI using URL metadata, arXiv metadata, Crossref lookup,
  and DOI content negotiation with Crossref fallback.
- Normalizes BibTeX output, including journal abbreviation mappings and
  selected publisher-specific cleanup.
- Full pipeline documentation (input -> output): [`docs/ALGORITHM.md`](docs/ALGORITHM.md)
- Diagram version of the pipeline: [`docs/ALGORITHM_VISUALS.md`](docs/ALGORITHM_VISUALS.md)



## Installation

```bash
pipx install -e .
```

### Installing from source

Create a virtual environment and install runtime dependencies:

```bash
git clone https://github.com/CarlosAndres12/doi2bib3
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install the package for local development:

```bash
pip install -e .
```

### MCP server (for AI coding assistants)

`doi2bib3` ships an optional MCP server (five tools: `audit_bib_file`,
`resolve_reference`, `normalize_bibtex_entry`, `repair_bib_file_inplace`,
`cache_stats`) that lets AI coding assistants (opencode, Claude Code, Cursor,
Windsurf) audit, resolve, normalize, repair, and inspect BibTeX files. The server
supports progress notifications — when the client provides a `progressToken`,
the server streams per-entry progress during `audit_bib_file` and
`repair_bib_file_inplace` so long-running operations don't leave the caller
waiting blindly.

To install and register it with **opencode** in one step:

```bash
pip install -e ".[mcp]"
scripts/install-opencode-mcp
```

Once installed, you can also process a `.bib` file end-to-end from the command line
(no MCP client needed):

```bash
# Audit a .bib file (report issues, no changes)
doi2bib3 audit references.bib

# Repair in place: resolve every DOI, normalize fields, backup before writing
# Use --force or --overwrite_backup to overwrite an existing .bak
doi2bib3 repair references.bib --normalize
```

The repair command creates a backup at `references.bib.bak` before touching the
original, groups output into VERIFIED / NOT FOUND / NO IDENTIFIER sections, and
annotates each entry with `% STATUS:` comment lines.

See [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md) for full details.

## CLI usage

The CLI accepts a single positional identifier, an optional `-o/--out`
path to save the BibTeX output, and `-b/--bibitem` to also print an
APS/RevTeX-style `\bibitem`. When installed, the package installs a console
script named `doi2bib3` (configured in `pyproject.toml`). From the repository
root you can run the local script wrapper at `scripts/doi2bib3`.

```bash
# using the local wrapper script from repo root
python scripts/doi2bib3 <identifier> [-o OUT] [--bibitem]

# or when installed as console script
doi2bib3 <identifier> [-o OUT] [--bibitem]
```

The CLI also supports subcommands for bibliography management (requires the
`[mcp]` extra: `pip install "doi2bib3[mcp]"`).

### `doi2bib3 audit <path>`

Audit a `.bib` file for missing, mismatched, or malformed fields without
modifying it. Prints a summary and per-entry issues.

```bash
doi2bib3 audit references.bib
```
```
Entries: 99
With issues: 42
Missing DOI: 5

[einstein1905] type=article doi=10.1002/andp.19053221004
    title: mismatch (expected: "On the Electrodynamics of Moving..."...)
    year: mismatch (expected: "1905" actual: "1906")
```

### `doi2bib3 repair <path>`

Repair a `.bib` file in place: resolves canonical metadata for every entry with
a DOI or title, optionally normalizes fields, and rewrites the file atomically.
Always creates a `.bak` backup first and retains it as a fallback. By default,
an existing `.bak` blocks the repair — use `--force` or `--overwrite_backup`
to proceed.

| Flag              | Description                                        |
|-------------------|----------------------------------------------------|
| `--normalize`     | Apply doi2bib3.normalize (casing, journal abbrevs) |
| `--force`         | Overwrite an existing `.bak` backup (CLI alias for `--overwrite_backup`) |
| `--overwrite_backup` | Overwrite an existing `.bak` backup (same as `--force`, matches MCP argument name) |
| `--flat`          | Disable section grouping (flat output)             |

```bash
# Basic repair (no normalization, grouped output with % STATUS comments)
doi2bib3 repair references.bib

# Repair with normalization, force overwrite existing backup, flat output
doi2bib3 repair references.bib --normalize --force --flat

# Same using the MCP-style argument name
doi2bib3 repair references.bib --normalize --overwrite_backup --flat
```
```
Entries: 99  Repaired: 80  Skipped: 15  Failed: 4
Backup: references.bib.bak
```

### `doi2bib3 resolve <identifier>`

Resolve a DOI, arXiv ID, URL, or title to a canonical BibTeX string.

```bash
doi2bib3 resolve 10.1103/PhysRevB.99.014101
doi2bib3 resolve "on the electrodynamics of moving bodies"
```

### `doi2bib3 normalize <bibtex>`

Normalize a single BibTeX entry (fixes casing, abbreviates journal names).

```bash
doi2bib3 normalize "@article{x, title={Hello}, journal={Physical Review B}, year={2020}}"
```
```
@article{hello_2020,
 journal = {Phys. Rev. B},
 title = {{Hello}},
 year = {2020}
}
```

### MCP common arguments

All MCP tools accept these optional arguments:

| Argument          | Type    | Description                                              |
|-------------------|---------|----------------------------------------------------------|
| `timeout`         | integer | Per-request network timeout in seconds (default 30).     |
| `refresh_cache`   | boolean | Bypass the reference cache and force a fresh network fetch. |

When calling tools through an MCP client that supports `progressToken`, the
server streams per-entry progress notifications during `audit_bib_file` and
`repair_bib_file_inplace` — no extra configuration needed on the caller side
beyond providing the token in the request metadata.

### `doi2bib3 cache-stats`

Print reference cache statistics (entries, hit rate, database location).

```bash
doi2bib3 cache-stats
```
```json
{
  "entries": 80,
  "hit_rate": 0.85,
  "db_path": "/home/user/.cache/doi2bib3/references.db",
  "db_size_bytes": 45056
}
```

## Examples

Fetch by DOI (bare DOI or DOI URL):

```bash
doi2bib3 10.1038/nphys1170
doi2bib3 https://doi.org/10.1038/nphys1170
```

ArXiv inputs (detected automatically):

```bash
doi2bib3 https://arxiv.org/abs/2411.08091
doi2bib3 arxiv.org/abs/2411.08091
doi2bib3 www.arxiv.org/abs/2411.08091
doi2bib3 http://xxx.lanl.gov/abs/cond-mat/9903064
doi2bib3 arXiv:2411.08091
doi2bib3 2411.08091
doi2bib3 hep-th/9901001
```

Name of the paper (includes fuzzy search):

```bash
doi2bib3 "Projected Topological Branes"
```

Publisher/article pages (Supports APS, AMS, ACS, Science, IOP Science, Nature, PNAS, SciPost, and ScienceDirect journals):

```bash
doi2bib3 https://www.pnas.org/doi/10.1073/pnas.2305943120
doi2bib3 https://iopscience.iop.org/article/10.1088/1402-4896/ad995f/pdf
doi2bib3 https://www.scipost.org/SciPostPhys.20.3.082/
doi2bib3 https://www.scipost.org/SciPostPhys.20.3.082/pdf
doi2bib3 https://www.sciencedirect.com/science/article/pii/S0003491605000096?via%3Dihub
```

Save to a file:

```bash
doi2bib3 https://doi.org/10.1038/nphys1170 -o paper.bib
```

This appends the BibTeX entry to `paper.bib` and prints `Wrote paper.bib`.

Print BibTeX and an APS/RevTeX-style `\bibitem` without saving to a file:

```bash
doi2bib3 https://doi.org/10.1038/nphys1170 --bibitem
```

Save BibTeX to a file and print the `\bibitem`:

```bash
doi2bib3 https://doi.org/10.1038/nphys1170 -o paper.bib --bibitem
```

When `-o/--out` and `--bibitem` are used together, the BibTeX entry is
appended to the file, `Wrote paper.bib` is printed, and the `\bibitem` is
printed to the terminal. The `\bibitem` is not written to the `.bib` file.

Note: If the tool is not installed, you can run `python scripts/doi2bib3 https://doi.org/10.1038/nphys1170`.

## Supported journal groups

`doi2bib3` directly supports many APS, AMS, ACS, Nature, Science, PNAS, SciPost, ScienceDirect and IOP groups of journals.
For other journals, the DOI link works, but the paper's URL would not work.

## Programmatic usage

### Public API

The Python API exposes one primary function:

- `doi2bib3.fetch_bibtex(identifier: str, timeout: int = 15) -> str`

Example:

```python
from doi2bib3 import fetch_bibtex

bib = fetch_bibtex('https://www.pnas.org/doi/10.1073/pnas.2305943120')
print(bib)
```

Additionally two convenience helpers are provided for APS/RevTeX-style
`\bibitem` output:

- `doi2bib3.format_bibtex_to_aps_bibitem(bibtex_str: str, key: Optional[str] = None) -> str`
- `doi2bib3.fetch_bibitem_aps(identifier: str, key: Optional[str] = None, timeout: int = 15) -> str`

Examples:

Format an already-obtained BibTeX string into an APS `\bibitem`:

```python
from doi2bib3 import format_bibtex_to_aps_bibitem

normalized_bibtex = "@article{smith_foobar_2020, title={Foo Bar}, author={Smith, A.}, year={2020}}"
bibitem = format_bibtex_to_aps_bibitem(normalized_bibtex, key="Smith2020")
print(bibitem)
```

Fetch an identifier (DOI/arXiv/etc.), get its normalized BibTeX, and return
an APS `\bibitem` in one call:

```python
from doi2bib3 import fetch_bibitem_aps

bibitem = fetch_bibitem_aps('10.1038/nphys1170', key='PhysRevSmith2008')
print(bibitem)
```

### Programmatic CLI entry

Use `subprocess` with `scripts/doi2bib3` (or installed `doi2bib3` command)
for automated CLI tests.

## Internal module layout

- `doi2bib3/backend.py`: input resolution and network fetch logic
- `doi2bib3/normalize.py`: BibTeX normalization/transforms
- `doi2bib3/bibitem.py`: APS/RevTeX `\bibitem` formatting
- `doi2bib3/io.py`: file output helpers
- `doi2bib3/constants.py`: shared constants (user agent)
- `doi2bib3/mcp_server/`: MCP server implementation
  - `server.py`: MCP SDK wiring, tool dispatch, progress notification support
  - `tools.py`: tool handlers (`audit_bib_file`, `resolve_reference`, `normalize_bibtex_entry`)
  - `repair.py`: in-place repair handler with backup/atomic-write/rollback safety
  - `adapter.py`: adapter layer between MCP and backend; result types, progress callback
  - `cache.py`: SQLite-backed reference cache with WAL mode and TTL expiry
- `scripts/doi2bib3`: command-line argument parsing and subcommand dispatch
- `scripts/doi2bib3-mcp`: MCP server launcher script

## License

This project is distributed under the GNU General Public License v3 (GPL-3.0-only).

## Acknowledgements

Parts of the code and documentation were assisted by copilot and codex.

## Future plans

Fix common pitfals listed in https://tex.stackexchange.com/q/386053/78560. This is tracked in [todo.md](./todo.md).
