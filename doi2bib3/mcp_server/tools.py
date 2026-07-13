"""MCP tool handler implementations for doi2bib3.

Each handler is a pure function taking typed arguments and returning a
``ToolResult`` (from .adapter). The server layer (server.py) is responsible for
translating these into MCP content responses. Keeping handlers SDK-free makes
them straightforward to unit-test.
"""

from __future__ import annotations

import os
from typing import Any

import bibtexparser
from bibtexparser.bparser import BibTexParser

from ..normalize import _MONTH_STRING_DEFINITIONS
from . import adapter
from .adapter import (
    ToolResult,
    Issue,
    EntryReport,
    AuditSummary,
    ProgressCallback,
    build_progress_payload,
    KIND_RESOLVED,
    KIND_UNRESOLVABLE,
    KIND_UNAVAILABLE,
    KIND_VALIDATION_ERROR,
    KIND_PARSE_ERROR,
    KIND_PATH_ERROR,
    SEVERITY_MISSING,
    SEVERITY_MISMATCH,
    SEVERITY_MALFORMED,
    SEVERITY_UNAVAILABLE,
    DEFAULT_TIMEOUT,
)


# Fields considered "mandatory" for diffing when present in canonical metadata.
# We only flag a field as missing if the canonical metadata actually provides it
# and the local entry lacks it.
DIFF_FIELDS = (
    "title",
    "author",
    "journal",
    "year",
    "volume",
    "pages",
    "doi",
    "publisher",
)


# --- resolve_reference (tasks 4.1-4.3) --------------------------------------

def resolve_reference(
    doi: str | None = None,
    query: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    refresh_cache: bool = False,
) -> ToolResult:
    """Fetch a canonical BibTeX string for a DOI or free-text query.

    Prefers ``doi`` when both are provided. Returns a structured ToolResult.
    """
    doi = (doi or "").strip()
    query = (query or "").strip()

    if not doi and not query:
        return ToolResult(
            KIND_VALIDATION_ERROR,
            ok=False,
            error="Either 'doi' or 'query' must be provided.",
        )

    identifier = doi or query
    return adapter.resolve_identifier(identifier, timeout=timeout, refresh_cache=refresh_cache)


# --- normalize_bibtex_entry (tasks 5.1-5.3) ---------------------------------

def normalize_bibtex_entry(bibtex_string: str | None = None, refresh_cache: bool = False) -> ToolResult:
    """Normalize a BibTeX entry string via doi2bib3.normalize.

    Sanitizes casing, applies publisher-specific replacements (Nature, APS,
    IOP), and returns formatted BibTeX. Preserves all original fields except
    those explicitly transformed by normalization rules.
    """
    if bibtex_string is None:
        bibtex_string = ""
    if not bibtex_string.strip():
        return ToolResult(
            KIND_VALIDATION_ERROR,
            ok=False,
            error="'bibtex_string' must be a non-empty BibTeX entry.",
        )

    # Validate parseability up front so we can return a clean parse_error
    # distinct from normalization failures.
    try:
        parser = BibTexParser(common_strings=False)
        db = bibtexparser.loads(bibtex_string, parser=parser)
    except Exception as exc:
        return ToolResult(KIND_PARSE_ERROR, ok=False, error=f"Failed to parse BibTeX: {exc}")

    if not db.entries:
        return ToolResult(
            KIND_PARSE_ERROR,
            ok=False,
            error="No BibTeX entries found in the input string.",
        )

    return adapter.normalize_entry(bibtex_string, refresh_cache=refresh_cache)


# --- audit_bib_file (tasks 6.1-6.5) ----------------------------------------

def _parse_bib_file(path: str) -> tuple[list[dict], list[Issue]]:
    """Parse a .bib file, returning (entries, parse_issues).

    Degrades gracefully: unparseable entries are reported as issues rather
    than aborting the whole parse.
    """
    issues: list[Issue] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        raise  # path errors handled by the caller

    parser = BibTexParser(common_strings=False)
    parser.ignore_nonstandard_types = False
    try:
        # Prepend month @string definitions so bare month macros (e.g.
        # `month = mar`) resolve instead of aborting the whole parse. This
        # mirrors doi2bib3.normalize's approach.
        db = bibtexparser.loads(_MONTH_STRING_DEFINITIONS + "\n" + content, parser=parser)
    except Exception as exc:
        # Total parse failure: report as a single malformed issue and return
        # no entries so the audit reports the failure but does not crash.
        issues.append(
            Issue(field="__file__", severity=SEVERITY_MALFORMED, actual=str(exc))
        )
        return [], issues

    # Drop the helper @string blocks from the entry list (they are not real
    # bibliography entries) while keeping their resolved values available.
    entries = [e for e in db.entries if e.get("ENTRYTYPE", "").lower() != "string"]
    return entries, issues


def _entry_doi(entry: dict) -> str | None:
    doi = (entry.get("doi") or "").strip()
    if doi:
        return doi
    url = (entry.get("url") or "").strip()
    if "doi.org/" in url.lower():
        return url.split("doi.org/", 1)[1].split("?")[0].split("#")[0].strip()
    return None


def _canonical_entry_fields(bibtex_str: str) -> dict[str, str]:
    """Parse a canonical BibTeX string into a {field: value} dict."""
    try:
        parser = BibTexParser(common_strings=False)
        db = bibtexparser.loads(bibtex_str, parser=parser)
    except Exception:
        return {}
    if not db.entries:
        return {}
    return {k.lower(): v for k, v in db.entries[0].items() if k not in ("ID", "ENTRYTYPE")}


def _normalize_for_compare(value: str | None) -> str:
    if value is None:
        return ""
    # Strip braces, whitespace, and collapse whitespace for tolerant comparison.
    v = value.replace("{", "").replace("}", "")
    return " ".join(v.split()).strip().lower()


def audit_bib_file(
    path: str | None,
    timeout: int = DEFAULT_TIMEOUT,
    refresh_cache: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> ToolResult:
    """Parse a .bib file and cross-reference entries against canonical metadata.

    Returns a structured report: per-entry issues plus a summary.
    """
    # --- path validation (task 6.5) ---
    if not path or not isinstance(path, str):
        return ToolResult(
            KIND_PATH_ERROR,
            ok=False,
            error="'path' must be a non-empty string.",
        )

    if not os.path.exists(path):
        return ToolResult(
            KIND_PATH_ERROR,
            ok=False,
            error=f"Path does not exist: {path}",
        )
    if not os.path.isfile(path):
        return ToolResult(
            KIND_PATH_ERROR,
            ok=False,
            error=f"Path is not a file: {path}",
        )

    try:
        entries, parse_issues = _parse_bib_file(path)
    except OSError as exc:
        return ToolResult(
            KIND_PATH_ERROR,
            ok=False,
            error=f"Could not read file '{path}': {exc}",
        )

    reports: list[EntryReport] = []

    for index, entry in enumerate(entries):
        key = entry.get("ID", "")
        etype = entry.get("ENTRYTYPE", "")
        doi = _entry_doi(entry)
        title = (entry.get("title") or "").strip()

        report = EntryReport(key=key, type=etype, doi=doi)

        # If we have a DOI or a title, try to resolve canonical metadata.
        identifier = doi or title
        outcome: str = "skipped"
        if identifier:
            result = adapter.resolve_identifier(identifier, timeout=timeout, refresh_cache=refresh_cache)
            if result.ok and result.data:
                outcome = "resolved"
                canonical = _canonical_entry_fields(result.data)
                for field in DIFF_FIELDS:
                    actual = entry.get(field)
                    expected = canonical.get(field)
                    if expected and not actual:
                        report.issues.append(
                            Issue(
                                field=field,
                                severity=SEVERITY_MISSING,
                                expected=expected,
                                actual=None,
                            )
                        )
                    elif expected and actual and _normalize_for_compare(actual) != _normalize_for_compare(expected):
                        report.issues.append(
                            Issue(
                                field=field,
                                severity=SEVERITY_MISMATCH,
                                expected=expected,
                                actual=actual,
                            )
                        )
            elif result.kind == KIND_UNAVAILABLE:
                outcome = "failed"
                report.issues.append(
                    Issue(
                        field="doi" if doi else "title",
                        severity=SEVERITY_UNAVAILABLE,
                        actual=identifier,
                    )
                )
            elif result.kind == KIND_UNRESOLVABLE and doi:
                outcome = "failed"
                # Only flag malformed for an explicit DOI that failed to resolve;
                # a title that fails to resolve is not necessarily malformed.
                report.issues.append(
                    Issue(
                        field="doi",
                        severity=SEVERITY_MALFORMED,
                        actual=doi,
                    )
                )
        else:
            # No DOI and no title: nothing to cross-reference.
            pass

        reports.append(report)

        if progress_callback is not None:
            try:
                progress_callback(
                    build_progress_payload(
                        index=index,
                        total=len(entries),
                        key=key,
                        doi=doi,
                        outcome=outcome,
                        message=f"Entry {index + 1}/{len(entries)}: {key} — {outcome}",
                    )
                )
            except Exception:
                pass

    # Attach any file-level parse issues to a synthetic report so they are not
    # lost (e.g. when the whole file failed to parse but the tool did not crash).
    if parse_issues and not reports:
        reports.append(
            EntryReport(key="__file__", type="", doi=None, issues=parse_issues)
        )
    elif parse_issues:
        # Fold file-level issues into the first report if present.
        reports[0].issues.extend(parse_issues)

    with_issues = sum(1 for r in reports if r.issues)
    missing_doi = sum(1 for r in reports if not r.doi)

    summary = AuditSummary(
        entries=len(reports),
        with_issues=with_issues,
        missing_doi=missing_doi,
    )

    if progress_callback is not None:
        try:
            progress_callback(
                build_progress_payload(
                    index=len(reports),
                    total=len(reports),
                    key="",
                    doi=None,
                    outcome="complete",
                    message="Audit complete",
                )
            )
        except Exception:
            pass

    payload = {
        "entries": [r.to_dict() for r in reports],
        "summary": summary.to_dict(),
    }
    return ToolResult(KIND_RESOLVED, ok=True, data=payload)