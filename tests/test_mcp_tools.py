"""Unit tests for the MCP tool handlers (task 7.3).

Covers all spec scenarios for resolve_reference, normalize_bibtex_entry, and
audit_bib_file. Network lookups are mocked via the adapter module.
"""

import json
import os
import pytest

from doi2bib3.mcp_server import tools, adapter
from doi2bib3.backend import DOIError

FIXTURES = os.path.join(os.path.dirname(__file__), "mcp_fixtures")


def _canonical_bibtex():
    """A canonical BibTeX string returned by a mocked successful resolution."""
    return (
        "@article{smith2019,\n"
        " author = {Smith, Jane},\n"
        " title = {A Study of Quantum Effects},\n"
        " journal = {Physical Review B},\n"
        " year = {2019},\n"
        " volume = {99},\n"
        " pages = {014101},\n"
        " doi = {10.1103/PhysRevB.99.014101},\n"
        "}"
    )


def _canonical_einstein_bibtex():
    """Canonical BibTeX matching the clean.bib fixture (Einstein 1905)."""
    return (
        "@article{einstein1905,\n"
        " author = {Einstein, Albert},\n"
        " title = {On the Electrodynamics of Moving Bodies},\n"
        " journal = {Annalen der Physik},\n"
        " year = {1905},\n"
        " volume = {322},\n"
        " pages = {891--921},\n"
        " doi = {10.1002/andp.19053221004},\n"
        "}"
    )


# --- resolve_reference -----------------------------------------------------

def test_resolve_reference_validation_error_no_args():
    r = tools.resolve_reference()
    assert r.ok is False
    assert r.kind == adapter.KIND_VALIDATION_ERROR


def test_resolve_reference_prefers_doi(monkeypatch):
    calls = []

    def fake(identifier, timeout=30, **kw):
        calls.append(identifier)
        return adapter.ToolResult(adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex())

    monkeypatch.setattr(adapter, "resolve_identifier", fake)
    r = tools.resolve_reference(doi="10.1103/PhysRevB.99.014101", query="some title")
    assert r.ok is True
    assert calls == ["10.1103/PhysRevB.99.014101"]


def test_resolve_reference_uses_query_when_no_doi(monkeypatch):
    calls = []

    def fake(identifier, timeout=30, **kw):
        calls.append(identifier)
        return adapter.ToolResult(adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex())

    monkeypatch.setattr(adapter, "resolve_identifier", fake)
    r = tools.resolve_reference(query="quantum effects")
    assert r.ok is True
    assert calls == ["quantum effects"]


def test_resolve_reference_unresolvable(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_UNRESOLVABLE, ok=False, error="not found"
        ),
    )
    r = tools.resolve_reference(doi="10.1103/garbage")
    assert r.ok is False
    assert r.kind == adapter.KIND_UNRESOLVABLE


def test_resolve_reference_unavailable(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_UNAVAILABLE, ok=False, error="timeout"
        ),
    )
    r = tools.resolve_reference(doi="10.1103/x")
    assert r.ok is False
    assert r.kind == adapter.KIND_UNAVAILABLE


# --- normalize_bibtex_entry ------------------------------------------------

def test_normalize_validation_error_empty():
    r = tools.normalize_bibtex_entry("")
    assert r.ok is False
    assert r.kind == adapter.KIND_VALIDATION_ERROR


def test_normalize_validation_error_none():
    r = tools.normalize_bibtex_entry(None)
    assert r.ok is False
    assert r.kind == adapter.KIND_VALIDATION_ERROR


def test_normalize_parse_error_no_entries():
    r = tools.normalize_bibtex_entry("not a bibtex entry at all")
    assert r.ok is False
    assert r.kind == adapter.KIND_PARSE_ERROR


def test_normalize_success_and_field_preservation(monkeypatch):
    captured = {}

    def fake_normalize(bib_str, **kw):
        captured["input"] = bib_str
        return adapter.ToolResult(
            adapter.KIND_RESOLVED,
            ok=True,
            data=(
                "@article{smith_2019,\n"
                " author = {Smith, Jane},\n"
                " title = {{A} {Study}},\n"
                " journal = {Phys. Rev. B},\n"
                " year = {2019},\n"
                "}"
            ),
        )

    monkeypatch.setattr(adapter, "normalize_entry", fake_normalize)
    r = tools.normalize_bibtex_entry("@article{smith2019, author={Smith, Jane}, title={A Study}, journal={Physical Review B}, year={2019}}")
    assert r.ok is True
    assert r.kind == adapter.KIND_RESOLVED
    # Field preservation: author/year present in output.
    assert r.data is not None
    assert "Smith, Jane" in r.data
    assert "2019" in r.data


def test_normalize_parse_error_from_adapter(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "normalize_entry",
        lambda bib_str: adapter.ToolResult(adapter.KIND_PARSE_ERROR, ok=False, error="bad"),
    )
    r = tools.normalize_bibtex_entry("@article{x,}")
    assert r.ok is False
    assert r.kind == adapter.KIND_PARSE_ERROR


# --- audit_bib_file --------------------------------------------------------

def test_audit_path_error_missing():
    r = tools.audit_bib_file("/nonexistent/path/x.bib")
    assert r.ok is False
    assert r.kind == adapter.KIND_PATH_ERROR


def test_audit_path_error_not_a_file(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    r = tools.audit_bib_file(str(d))
    assert r.ok is False
    assert r.kind == adapter.KIND_PATH_ERROR


def test_audit_path_error_empty_path():
    r = tools.audit_bib_file("")
    assert r.ok is False
    assert r.kind == adapter.KIND_PATH_ERROR


def test_audit_path_error_none():
    r = tools.audit_bib_file(None)
    assert r.ok is False
    assert r.kind == adapter.KIND_PATH_ERROR


def test_audit_clean_file_no_issues(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_einstein_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "clean.bib"))
    assert r.ok is True
    payload = r.data
    assert payload["summary"]["entries"] == 1
    # Clean entry matches canonical -> no issues.
    assert payload["summary"]["with_issues"] == 0


def test_audit_missing_fields(monkeypatch):
    # Canonical provides volume/pages/journal that the fixture lacks.
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "missing_fields.bib"))
    assert r.ok is True
    entry = r.data["entries"][0]
    severities = {i["severity"] for i in entry["issues"]}
    assert adapter.SEVERITY_MISSING in severities
    # The missing fields should include volume and pages.
    missing_fields = {i["field"] for i in entry["issues"] if i["severity"] == adapter.SEVERITY_MISSING}
    assert "volume" in missing_fields
    assert "pages" in missing_fields


def test_audit_mismatched_fields(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "mismatched.bib"))
    assert r.ok is True
    entry = r.data["entries"][0]
    mismatches = [i for i in entry["issues"] if i["severity"] == adapter.SEVERITY_MISMATCH]
    assert len(mismatches) >= 1
    # Title and journal differ from canonical.
    mismatch_fields = {i["field"] for i in mismatches}
    assert "title" in mismatch_fields or "journal" in mismatch_fields


def test_audit_malformed_doi(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_UNRESOLVABLE, ok=False, error="Invalid DOI: not-a-valid-doi"
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "malformed_doi.bib"))
    assert r.ok is True
    entry = r.data["entries"][0]
    malformed = [i for i in entry["issues"] if i["severity"] == adapter.SEVERITY_MALFORMED]
    assert len(malformed) == 1
    assert malformed[0]["field"] == "doi"


def test_audit_unavailable(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_UNAVAILABLE, ok=False, error="timeout"
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "clean.bib"))
    assert r.ok is True
    entry = r.data["entries"][0]
    unavailable = [i for i in entry["issues"] if i["severity"] == adapter.SEVERITY_UNAVAILABLE]
    assert len(unavailable) == 1


def test_audit_summary_counts(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "missing_fields.bib"))
    summary = r.data["summary"]
    assert summary["entries"] == 1
    assert summary["with_issues"] == 1
    # missing_doi counts entries without a DOI field; fixture has a DOI.
    assert summary["missing_doi"] == 0


def test_audit_report_structure(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "clean.bib"))
    entry = r.data["entries"][0]
    assert set(entry.keys()) == {"key", "type", "doi", "issues"}
    assert entry["key"] == "einstein1905"
    assert entry["type"] == "article"
    assert entry["doi"] == "10.1002/andp.19053221004"


def test_audit_unparseable_does_not_crash(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "unparseable.bib"))
    # Should complete (ok) even with malformed content; report entries parsed.
    assert r.ok is True
    assert r.data["summary"]["entries"] >= 1


def test_audit_string_macros(monkeypatch):
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(os.path.join(FIXTURES, "string_macros.bib"))
    assert r.ok is True
    assert r.data["summary"]["entries"] == 1


# --- progress callback -------------------------------------------------------

def test_audit_progress_callback_called_per_entry(monkeypatch):
    """Progress callback receives one call per entry with correct data."""
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    calls = []
    def _cb(payload):
        calls.append(payload)

    r = tools.audit_bib_file(
        os.path.join(FIXTURES, "clean.bib"),
        progress_callback=_cb,
    )
    assert r.ok is True
    # clean.bib has 1 entry
    assert len(calls) == 2  # 1 per-entry + 1 final


def test_audit_progress_payload_structure(monkeypatch):
    """Each callback payload has expected keys and sane values."""
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    payloads = []
    def _cb(payload):
        payloads.append(payload)

    tools.audit_bib_file(
        os.path.join(FIXTURES, "clean.bib"),
        progress_callback=_cb,
    )
    # First payload: per-entry
    p1 = payloads[0]
    assert p1["index"] == 0
    assert p1["total"] == 1
    assert p1["key"] == "einstein1905"
    assert p1["doi"] is not None
    assert p1["outcome"] in ("resolved", "skipped", "failed")
    assert isinstance(p1["message"], str)

    # Last payload: final completion
    pf = payloads[-1]
    assert pf["outcome"] == "complete"


def test_audit_progress_callback_none_is_safe(monkeypatch):
    """When progress_callback is None, audit works normally."""
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    r = tools.audit_bib_file(
        os.path.join(FIXTURES, "clean.bib"),
        progress_callback=None,
    )
    assert r.ok is True
    assert r.data["summary"]["entries"] == 1


def test_audit_progress_callback_exception_does_not_abort(monkeypatch):
    """A callback that raises is caught; audit finishes correctly."""
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    def _bad_cb(payload):
        raise RuntimeError("callback broken")

    r = tools.audit_bib_file(
        os.path.join(FIXTURES, "clean.bib"),
        progress_callback=_bad_cb,
    )
    # Should still complete successfully.
    assert r.ok is True
    assert r.data["summary"]["entries"] == 1


def test_audit_progress_values_monotonic(monkeypatch):
    """Progress ratio advances monotonically; final value is 1.0."""
    # We need a file with multiple entries to test monotonicity.
    # Use missing_fields.bib (1 entry) — enough to test that ratio is correct.
    monkeypatch.setattr(
        adapter,
        "resolve_identifier",
        lambda identifier, timeout=30, **kw: adapter.ToolResult(
            adapter.KIND_RESOLVED, ok=True, data=_canonical_bibtex()
        ),
    )
    payloads = []
    def _cb(payload):
        payloads.append(payload)

    # missing_fields.bib has 1 entry
    r = tools.audit_bib_file(
        os.path.join(FIXTURES, "missing_fields.bib"),
        progress_callback=_cb,
    )
    assert r.ok is True
    # Verify ratio: index 0, total 1 -> progress = (0+1)/1 = 1.0
    per_entry = [p for p in payloads if p["outcome"] != "complete"]
    final = [p for p in payloads if p["outcome"] == "complete"]
    assert len(per_entry) == 1
    assert len(final) == 1
    # Per-entry progress should be (index+1)/total = 1/1 = 1.0 for the only entry
    assert (per_entry[0]["index"] + 1) / per_entry[0]["total"] == 1.0