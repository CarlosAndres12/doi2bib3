"""Unit tests for the doi2bib3.mcp_server.adapter (task 7.2).

Network calls (doi2bib3.backend.fetch_bibtex, doi2bib3.normalize.normalize_bibtex)
are mocked so tests run offline.
"""

import pytest

from doi2bib3.mcp_server import adapter
from doi2bib3.backend import DOIError


def test_resolve_identifier_success(monkeypatch):
    monkeypatch.setattr(
        adapter, "fetch_bibtex", lambda identifier, timeout=30: "@article{x, title={T}}"
    )
    result = adapter.resolve_identifier("10.1103/PhysRevB.99.014101")
    assert result.ok is True
    assert result.kind == adapter.KIND_RESOLVED
    assert result.data == "@article{x, title={T}}"


def test_resolve_identifier_unresolvable_doierror(monkeypatch):
    def raise_doierror(identifier, timeout=30):
        raise DOIError("Invalid DOI: garbage")

    monkeypatch.setattr(adapter, "fetch_bibtex", raise_doierror)
    result = adapter.resolve_identifier("garbage")
    assert result.ok is False
    assert result.kind == adapter.KIND_UNRESOLVABLE
    assert "Invalid DOI" in (result.error or "")


def test_resolve_identifier_unresolvable_crossref_failure(monkeypatch):
    def raise_doierror(identifier, timeout=30):
        raise DOIError("Crossref lookup failed for: foo")

    monkeypatch.setattr(adapter, "fetch_bibtex", raise_doierror)
    result = adapter.resolve_identifier("foo")
    assert result.ok is False
    assert result.kind == adapter.KIND_UNRESOLVABLE


def test_resolve_identifier_unavailable_timeout(monkeypatch):
    def raise_timeout(identifier, timeout=30):
        raise TimeoutError("Connection timed out")

    monkeypatch.setattr(adapter, "fetch_bibtex", raise_timeout)
    result = adapter.resolve_identifier("10.1103/x")
    assert result.ok is False
    assert result.kind == adapter.KIND_UNAVAILABLE
    assert "timed out" in (result.error or "")


def test_resolve_identifier_unavailable_connection_error(monkeypatch):
    def raise_conn(identifier, timeout=30):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(adapter, "fetch_bibtex", raise_conn)
    result = adapter.resolve_identifier("10.1103/x")
    assert result.ok is False
    assert result.kind == adapter.KIND_UNAVAILABLE


def test_normalize_entry_success(monkeypatch):
    monkeypatch.setattr(
        adapter, "normalize_bibtex", lambda bib_str: "@article{norm, title={T}}"
    )
    result = adapter.normalize_entry("@article{x, title={T}}")
    assert result.ok is True
    assert result.kind == adapter.KIND_RESOLVED
    assert result.data is not None and "norm" in result.data


def test_normalize_entry_parse_error(monkeypatch):
    def raise_exc(bib_str):
        raise ValueError("bad bibtex")

    monkeypatch.setattr(adapter, "normalize_bibtex", raise_exc)
    result = adapter.normalize_entry("@article{x,}")
    assert result.ok is False
    assert result.kind == adapter.KIND_PARSE_ERROR


def test_normalize_entry_empty_result(monkeypatch):
    monkeypatch.setattr(adapter, "normalize_bibtex", lambda bib_str: "   ")
    result = adapter.normalize_entry("@article{x,}")
    assert result.ok is False
    assert result.kind == adapter.KIND_PARSE_ERROR


def test_tool_result_to_dict_roundtrip():
    r = adapter.ToolResult(adapter.KIND_RESOLVED, ok=True, data={"a": 1})
    d = r.to_dict()
    assert d == {"kind": "resolved", "ok": True, "data": {"a": 1}}


def test_issue_and_entry_report_to_dict():
    issue = adapter.Issue(field="year", severity=adapter.SEVERITY_MISSING, expected="2020")
    report = adapter.EntryReport(key="k", type="article", doi="10.x/y", issues=[issue])
    d = report.to_dict()
    assert d["key"] == "k"
    assert d["issues"][0]["field"] == "year"
    assert d["issues"][0]["severity"] == "missing"