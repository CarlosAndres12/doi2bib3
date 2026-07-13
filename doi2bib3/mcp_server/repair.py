"""In-place BibTeX repair with backup protection (tasks 1 & 2).

Provides ``repair_bib_file_inplace`` — an MCP tool that rewrites a ``.bib`` file
in place with verified, normalized entries resolved via ``doi2bib3.backend``.

Safety contract:
  1. Backup-first: an exact binary copy of ``${path}`` is written to
     ``${path}.bak`` before any resolution or write occurs.
  2. Atomic write: repaired content is written to a temp file in the same
     directory and atomically moved into place via ``os.replace``, so the
     target file is never left half-written.
  3. Rollback-on-failure: if the write fails, ``${path}`` is restored from the
     ``.bak`` snapshot and a structured ``repair_error`` is returned.
  4. Existing ``.bak`` is not silently overwritten — the caller must opt in via
     ``overwrite_backup``.

Per-entry resilience: a resolution/normalization failure for one entry is
recorded as ``skipped`` (original entry preserved verbatim) and the repair
continues for the rest. Only a total parse failure or write failure aborts the
whole pass.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

from ..normalize import _MONTH_STRING_DEFINITIONS
from . import adapter
from .adapter import (
    ToolResult,
    KIND_RESOLVED,
    KIND_VALIDATION_ERROR,
    KIND_PATH_ERROR,
    KIND_REPAIR_ERROR,
    KIND_BACKUP_CONFLICT,
    KIND_UNAVAILABLE,
    KIND_UNRESOLVABLE,
    DEFAULT_TIMEOUT,
    ProgressCallback,
    build_progress_payload,
)
from .tools import _parse_bib_file, _entry_doi


# --- Backup / atomic-write / rollback helpers (tasks 1.2-1.5) ---------------

def _create_backup(path: str) -> str:
    """Create an exact binary copy of ``path`` at ``${path}.bak``.

    Returns the backup path. Raises on I/O failure (caller handles rollback).
    """
    backup_path = path + ".bak"
    shutil.copy2(path, backup_path)
    return backup_path


def _atomic_write(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a temp file in the same directory (same filesystem, so
    ``os.replace`` is atomic), flushes/fsyncs, then renames into place. On
    failure the temp file is removed and the exception propagates.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".tmp.", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file so no stale artifact remains.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _rollback_from_backup(path: str, backup_path: str) -> None:
    """Restore ``path`` from ``backup_path`` via atomic replace.

    Used when the write step fails, so the user's working file matches its
    pre-repair state. The backup file is retained on disk (copy, not move).
    """
    if os.path.exists(backup_path):
        # Copy the backup to a temp, then atomically replace path, so the
        # backup file itself is preserved as the user's fallback.
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp_path = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".rollback.", dir=directory
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                with open(backup_path, "rb") as src:
                    shutil.copyfileobj(src, fh)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# --- Structured report types ------------------------------------------------

@dataclass
class RepairEntryOutcome:
    """Per-entry outcome in a repair report."""

    key: str
    doi: str | None
    outcome: str  # "repaired" | "skipped" | "failed"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepairSummary:
    entries: int
    repaired: int
    skipped: int
    failed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- repair_bib_file_inplace handler (tasks 2.1-2.8) ------------------------

def _entries_to_bibtex(entries: list[dict]) -> str:
    """Serialize a list of bibtexparser entry dicts back to a BibTeX string."""
    writer = BibTexWriter()
    db = bibtexparser.bparser.BibTexParser(common_strings=False).parse(
        _MONTH_STRING_DEFINITIONS + "\n"
    )
    db.entries = entries
    db.strings = {}  # suppress inline @string blocks
    return bibtexparser.dumps(db, writer=writer)


def _entry_to_bibtex(entry: dict) -> str:
    """Serialize a single entry dict to a BibTeX string (preserving it verbatim)."""
    writer = BibTexWriter()
    # Build a clean database with just this entry — no month-string preamble.
    # Month strings are written once at the top of grouped output.
    parser = bibtexparser.bparser.BibTexParser(common_strings=False)
    dummy = "@article{_dummy, title={_}}\n@article{_dummy2, title={_}}"  # minimal parse
    db = parser.parse(_MONTH_STRING_DEFINITIONS + "\n" + dummy)
    db.entries = [entry]
    db.strings = {}  # suppress inline @string output
    return bibtexparser.dumps(db, writer=writer)


# --- Output formatting (bibtex-repair-comments) ------------------------------

# Section ordering for output.
_SECTION_ORDER = ("VERIFIED", "NOT FOUND", "NO IDENTIFIER", "UNAVAILABLE")


def _section_category(outcome: RepairEntryOutcome) -> str:
    """Map a repair outcome to its output section name."""
    if outcome.outcome == "repaired":
        return "VERIFIED"
    odoi = outcome.doi
    oerr = (outcome.error or "").lower()
    if odoi is None:
        return "NO IDENTIFIER"
    if "unresolvable" in oerr or "not found" in oerr or "invalid" in oerr:
        return "NOT FOUND"
    # Network error, timeout, etc. → UNAVAILABLE
    return "UNAVAILABLE"


def _format_comment(outcome: RepairEntryOutcome) -> str:
    """Build a % STATUS: ... comment line for a single entry outcome."""
    status = _section_category(outcome)
    parts = [f"STATUS: {status}"]
    if outcome.doi:
        parts.append(f" doi: {outcome.doi}")
    else:
        parts.append(" key: " + (outcome.key or "?"))
    if outcome.outcome != "repaired" and outcome.error:
        parts.append(f" error: {outcome.error}")
    return "% " + "".join(parts) + "\n"


def _section_header(name: str) -> str:
    """Build a % ===== NAME ===== section boundary comment."""
    return f"% ===== {name} =====\n\n"


def _grouped_output(entries: list[dict], outcomes: list[RepairEntryOutcome]) -> str:
    """Assemble a grouped, self-documented BibTeX output with sections and comments.

    Entries are grouped by their section category; each entry is preceded by a
    ``% STATUS:`` comment line. Sections with zero entries are omitted.
    """
    # Group entries by section, preserving original order within each section.
    groups: dict[str, list[tuple[dict, RepairEntryOutcome]]] = {s: [] for s in _SECTION_ORDER}
    for entry, oc in zip(entries, outcomes):
        cat = _section_category(oc)
        groups[cat].append((entry, oc))

    parts: list[str] = []
    # Write month @string definitions once at the top so bare month macros in
    # preserved entries (e.g. month = mar) remain valid.
    parts.append(_MONTH_STRING_DEFINITIONS)
    parts.append("\n")
    for cat in _SECTION_ORDER:
        group = groups[cat]
        if not group:
            continue
        parts.append(_section_header(cat))
        for entry, oc in group:
            parts.append(_format_comment(oc))
            parts.append(_entry_to_bibtex(entry))
        parts.append("\n")

    return "".join(parts)


def repair_bib_file_inplace(
    path: str | None,
    auto_normalize: bool = False,
    overwrite_backup: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    refresh_cache: bool = False,
    group_output: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> ToolResult:
    """Repair a .bib file in place with backup protection.

    Returns a structured ToolResult. On success, ``data`` is a report with a
    summary and per-entry outcomes. On failure, the original file is restored
    from the ``.bak`` snapshot and a ``repair_error`` is returned.
    """
    # --- argument validation (task 2.1) ---
    if not path or not isinstance(path, str):
        return ToolResult(
            KIND_VALIDATION_ERROR,
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

    backup_path = path + ".bak"

    # --- existing .bak conflict check (task 1.5 / 2.1) ---
    if os.path.exists(backup_path) and not overwrite_backup:
        return ToolResult(
            KIND_BACKUP_CONFLICT,
            ok=False,
            error=(
                f"A backup file already exists at {backup_path}. "
                "Set 'overwrite_backup' to true to overwrite it."
            ),
        )

    # --- backup-first (task 1.2) ---
    try:
        _create_backup(path)
    except OSError as exc:
        return ToolResult(
            KIND_REPAIR_ERROR,
            ok=False,
            error=f"Failed to create backup at {backup_path}: {exc}",
        )

    # --- parse (task 2.2) ---
    try:
        entries, parse_issues = _parse_bib_file(path)
    except OSError as exc:
        return ToolResult(
            KIND_PATH_ERROR,
            ok=False,
            error=f"Could not read file '{path}': {exc}",
        )

    if not entries:
        # Nothing to repair; leave the file and backup as-is.
        return ToolResult(
            KIND_REPAIR_ERROR,
            ok=False,
            error="No parseable BibTeX entries found in the file.",
        )

    # --- resolve / normalize per entry (tasks 2.3-2.5) ---
    outcomes: list[RepairEntryOutcome] = []
    output_entries: list[dict] = []

    total_entries = len(entries)
    for index, entry in enumerate(entries):
        key = entry.get("ID", "")
        doi = _entry_doi(entry)
        title = (entry.get("title") or "").strip()
        identifier = doi or title

        if not identifier:
            # No DOI or title: nothing to resolve against — preserve verbatim.
            output_entries.append(entry)
            outcomes.append(RepairEntryOutcome(key=key, doi=None, outcome="skipped"))
            continue

        result = adapter.resolve_identifier(identifier, timeout=timeout, refresh_cache=refresh_cache)

        if not result.ok or not result.data:
            # Resolution failed — preserve the original entry verbatim.
            output_entries.append(entry)
            err = result.error or result.kind
            outcomes.append(
                RepairEntryOutcome(
                    key=key, doi=doi, outcome="skipped", error=err
                )
            )
            continue

        canonical_bibtex = result.data

        if auto_normalize:
            norm = adapter.normalize_entry(canonical_bibtex)
            if norm.ok and norm.data:
                canonical_bibtex = norm.data
            # If normalization fails, fall back to the resolved (unnormalized) entry.

        # Parse the canonical BibTeX string back into an entry dict so it can be
        # serialized alongside the preserved entries.
        try:
            parser = BibTexParser(common_strings=False)
            canon_db = bibtexparser.loads(
                _MONTH_STRING_DEFINITIONS + "\n" + canonical_bibtex, parser=parser
            )
            if canon_db.entries:
                # Preserve the original key so cross-references in the .bib file
                # don't break; use the canonical entry's fields but the original ID.
                canon_entry = canon_db.entries[0]
                canon_entry["ID"] = key
                output_entries.append(canon_entry)
                outcomes.append(
                    RepairEntryOutcome(key=key, doi=doi, outcome="repaired")
                )
            else:
                output_entries.append(entry)
                outcomes.append(
                    RepairEntryOutcome(
                        key=key, doi=doi, outcome="skipped",
                        error="canonical entry parsed empty",
                    )
                )
        except Exception as exc:
            # Canonical BibTeX unparseable — preserve original.
            output_entries.append(entry)
            outcomes.append(
                RepairEntryOutcome(
                    key=key, doi=doi, outcome="skipped", error=str(exc)
                )
            )

        # --- per-entry progress notification ---
        if progress_callback is not None:
            oc = outcomes[-1]
            entry_msg = (
                f"Entry {index+1}/{total_entries}: {oc.key} — {oc.outcome}"
            )
            if oc.error:
                entry_msg += f" ({oc.error})"
            try:
                progress_callback(
                    build_progress_payload(
                        index=index,
                        total=total_entries,
                        key=oc.key,
                        doi=oc.doi,
                        outcome=oc.outcome,
                        message=entry_msg,
                    )
                )
            except Exception:
                pass

    # --- final completion notification ---
    if progress_callback is not None:
        try:
            progress_callback(
                build_progress_payload(
                    index=total_entries,
                    total=total_entries,
                    key="",
                    doi=None,
                    outcome="complete",
                    message="Repair complete",
                )
            )
        except Exception:
            pass

    # --- assemble repaired output (task 2.6) ---
    try:
        if group_output:
            repaired_content = _grouped_output(output_entries, outcomes)
        else:
            repaired_content = _entries_to_bibtex(output_entries)
    except Exception as exc:
        # Serialization failed — roll back and report.
        _rollback_from_backup(path, backup_path)
        return ToolResult(
            KIND_REPAIR_ERROR,
            ok=False,
            error=f"Failed to serialize repaired entries: {exc}",
        )

    # --- atomic write (task 2.6) + rollback on failure (task 2.7) ---
    try:
        _atomic_write(path, repaired_content)
    except Exception as exc:
        _rollback_from_backup(path, backup_path)
        return ToolResult(
            KIND_REPAIR_ERROR,
            ok=False,
            error=(
                f"Write failed and original restored from backup: {exc}. "
                f"Backup retained at {backup_path}."
            ),
        )

    # --- structured report (task 2.8) ---
    repaired_count = sum(1 for o in outcomes if o.outcome == "repaired")
    skipped_count = sum(1 for o in outcomes if o.outcome == "skipped")
    failed_count = sum(1 for o in outcomes if o.outcome == "failed")

    summary = RepairSummary(
        entries=len(outcomes),
        repaired=repaired_count,
        skipped=skipped_count,
        failed=failed_count,
    )

    payload = {
        "entries": [o.to_dict() for o in outcomes],
        "summary": summary.to_dict(),
        "backup": backup_path,
    }
    return ToolResult(KIND_RESOLVED, ok=True, data=payload)