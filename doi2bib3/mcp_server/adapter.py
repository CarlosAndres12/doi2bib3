"""Thin adapter wrapping doi2bib3 backend/normalize for use by MCP tools.

The adapter isolates the MCP layer from doi2bib3 internals so upstream API
changes only affect this module. It also defines the structured result/error
types shared across all tool handlers.

Resolution is cached via :mod:`doi2bib3.mcp_server.cache` — repeated lookups
for the same identifier return instantly and deterministically within the TTL.
Set ``refresh_cache=True`` to force a fresh network fetch.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Any

from ..backend import fetch_bibtex, DOIError
from ..normalize import normalize_bibtex


# --- Structured result/error types -------------------------------------------

# Issue severities used by audit_bib_file.
SEVERITY_MISSING = "missing"
SEVERITY_MISMATCH = "mismatch"
SEVERITY_MALFORMED = "malformed"
SEVERITY_UNAVAILABLE = "unavailable"

# Outcome kinds for resolve_reference / normalize.
KIND_RESOLVED = "resolved"
KIND_UNRESOLVABLE = "unresolvable"
KIND_UNAVAILABLE = "unavailable"
KIND_VALIDATION_ERROR = "validation_error"
KIND_PARSE_ERROR = "parse_error"
KIND_PATH_ERROR = "path_error"
KIND_REPAIR_ERROR = "repair_error"
KIND_BACKUP_CONFLICT = "backup_conflict"


@dataclass
class Issue:
    """A single field discrepancy reported by audit_bib_file."""

    field: str
    severity: str
    expected: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EntryReport:
    """Per-entry audit report."""

    key: str
    type: str
    doi: Optional[str] = None
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "doi": self.doi,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class AuditSummary:
    entries: int
    with_issues: int
    missing_doi: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    """Structured outcome returned by every MCP tool handler.

    ``kind`` is one of the ``KIND_*`` constants. ``ok`` is True for successful
    outcomes (resolved / normalized / audit-complete). ``data`` carries the
    payload; ``error`` carries a human-readable message for non-ok outcomes.
    ``stale`` is True when the result came from an expired cache entry
    (stale-on-error fallback).
    """

    kind: str
    ok: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "ok": self.ok}
        if self.stale:
            out["stale"] = True
        if self.data is not None:
            out["data"] = self.data
        if self.error is not None:
            out["error"] = self.error
        return out


# --- Adapter wrappers --------------------------------------------------------

# Default per-request timeout (seconds) for network lookups.
DEFAULT_TIMEOUT = 30


def _doi_from_bibtex(bibtex: str) -> Optional[str]:
    """Extract a DOI from a BibTeX string (best-effort)."""
    import re
    m = re.search(r"doi\s*=\s*\{([^}]+)\}", bibtex, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"doi\s*=\s*\"([^\"]+)\"", bibtex, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def resolve_identifier(
    identifier: str,
    timeout: int = DEFAULT_TIMEOUT,
    refresh_cache: bool = False,
) -> ToolResult:
    """Resolve a DOI/arXiv/URL/title identifier to canonical BibTeX.

    Returns a ToolResult:
      - kind=resolved, data=<bibtex string> on success
      - kind=unresolvable when the identifier cannot be resolved
      - kind=unavailable when a network/provider error prevents resolution
    """
    # --- cache check ---
    from . import cache
    if not refresh_cache:
        cached = cache.lookup(identifier)
        if cached is not None:
            return ToolResult(KIND_RESOLVED, ok=True, data=cached.bibtex)

    # --- network path ---
    try:
        bibtex = fetch_bibtex(identifier, timeout=timeout)
    except DOIError as exc:
        # DOIError means unresolvable — no stale fallback.
        msg = str(exc)
        if "Crossref lookup failed" in msg:
            return _handle_network_error(identifier, msg, refresh_cache)
        return ToolResult(KIND_UNRESOLVABLE, ok=False, error=msg)
    except ConnectionError as exc:
        return _handle_network_error(identifier, str(exc), refresh_cache)
    except Exception as exc:
        # Any other error (timeout, transport, etc.) — try stale fallback.
        return _handle_network_error(identifier, str(exc), refresh_cache)

    # Success — populate cache.
    doi = _doi_from_bibtex(bibtex)
    cache.store(identifier, doi, bibtex)
    return ToolResult(KIND_RESOLVED, ok=True, data=bibtex)


def _handle_network_error(
    identifier: str, error_msg: str, refresh_cache: bool
) -> ToolResult:
    """Handle a network error: try stale-on-error fallback."""
    from . import cache
    # If the caller requested a fresh fetch, don't fall back — they
    # explicitly wanted new data.
    if refresh_cache:
        if "Crossref lookup failed" in error_msg or "Invalid DOI" in error_msg:
            return ToolResult(KIND_UNRESOLVABLE, ok=False, error=error_msg)
        return ToolResult(KIND_UNAVAILABLE, ok=False, error=error_msg)

    # Check for a stale (expired) entry.
    stale_row = cache.lookup_expired(identifier)
    if stale_row is not None:
        return ToolResult(
            KIND_RESOLVED, ok=True, data=stale_row.bibtex, stale=True
        )

    if "Crossref lookup failed" in error_msg or "Invalid DOI" in error_msg:
        return ToolResult(KIND_UNRESOLVABLE, ok=False, error=error_msg)
    return ToolResult(KIND_UNAVAILABLE, ok=False, error=error_msg)


def normalize_entry(
    bibtex_string: str,
    refresh_cache: bool = False,
) -> ToolResult:
    """Normalize a BibTeX entry string via doi2bib3.normalize.

    Returns a ToolResult:
      - kind=resolved, data=<normalized bibtex string> on success
      - kind=parse_error when the input cannot be parsed as BibTeX
    """
    # Use bibtex_string as the cache key (identifier for normalization results).
    # The cached normalized result depends on the exact input string.
    from . import cache
    if not refresh_cache:
        cached = cache.lookup(bibtex_string)
        if cached is not None and cached.normalized_bibtex:
            return ToolResult(KIND_RESOLVED, ok=True, data=cached.normalized_bibtex)

    try:
        normalized = normalize_bibtex(bibtex_string)
    except Exception as exc:
        return ToolResult(KIND_PARSE_ERROR, ok=False, error=str(exc))

    if not normalized or not normalized.strip():
        return ToolResult(KIND_PARSE_ERROR, ok=False, error="Normalization produced an empty result")

    cache.store(bibtex_string, doi=None, bibtex=bibtex_string, normalized_bibtex=normalized)
    return ToolResult(KIND_RESOLVED, ok=True, data=normalized)