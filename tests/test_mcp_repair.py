"""Unit tests for the in-place repair tool and backup safety (tasks 4.2-4.5).

Network lookups are mocked via the adapter module so tests run offline and
deterministically.
"""

import os
import shutil
import pytest

from doi2bib3.mcp_server import repair, adapter
from doi2bib3.mcp_server.adapter import (
    KIND_RESOLVED,
    KIND_VALIDATION_ERROR,
    KIND_PATH_ERROR,
    KIND_REPAIR_ERROR,
    KIND_BACKUP_CONFLICT,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "mcp_fixtures")


def _canonical_bibtex():
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


def _resolved_result(identifier, timeout=30, **kw):
    return adapter.ToolResult(KIND_RESOLVED, ok=True, data=_canonical_bibtex())


def _unresolvable_result(identifier, timeout=30, **kw):
    return adapter.ToolResult(
        KIND_REPAIR_ERROR, ok=False, error="unresolvable"
    )


# --- Backup helper tests (task 4.2) -----------------------------------------

def test_create_backup_is_binary_exact(tmp_path):
    src = tmp_path / "x.bib"
    src.write_text("hello world\n", encoding="utf-8")
    backup = repair._create_backup(str(src))
    assert backup == str(src) + ".bak"
    assert os.path.exists(backup)
    assert open(backup, "rb").read() == b"hello world\n"


def test_atomic_write_replaces_and_cleans_temp(tmp_path):
    target = tmp_path / "out.bib"
    target.write_text("old", encoding="utf-8")
    repair._atomic_write(str(target), "new content")
    assert target.read_text(encoding="utf-8") == "new content"
    # No temp files left behind.
    temps = [f for f in os.listdir(tmp_path) if f.startswith("out.bib.tmp.")]
    assert temps == []


def test_atomic_write_failure_leaves_original_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "out.bib"
    target.write_text("original", encoding="utf-8")

    def bad_replace(tmp, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", bad_replace)
    with pytest.raises(OSError):
        repair._atomic_write(str(target), "new")
    # Original intact.
    assert target.read_text(encoding="utf-8") == "original"
    temps = [f for f in os.listdir(tmp_path) if f.startswith("out.bib.tmp.")]
    assert temps == []


def test_rollback_restores_original(tmp_path):
    src = tmp_path / "x.bib"
    src.write_text("original", encoding="utf-8")
    backup = str(src) + ".bak"
    shutil.copy2(str(src), backup)
    # Corrupt the source.
    src.write_text("corrupted", encoding="utf-8")
    repair._rollback_from_backup(str(src), backup)
    assert src.read_text(encoding="utf-8") == "original"
    # Backup retained.
    assert os.path.exists(backup)


# --- repair_bib_file_inplace handler tests (tasks 4.3-4.5) ------------------

def _setup(tmp_path, fixture_name):
    """Copy a fixture into tmp_path and return its path (clean, no .bak)."""
    src = os.path.join(FIXTURES, fixture_name)
    dst = tmp_path / fixture_name
    shutil.copy2(src, dst)
    return str(dst)


def test_repair_validation_error_no_path():
    r = repair.repair_bib_file_inplace(path=None)
    assert r.ok is False
    assert r.kind == KIND_VALIDATION_ERROR


def test_repair_validation_error_empty_path():
    r = repair.repair_bib_file_inplace(path="")
    assert r.ok is False
    assert r.kind == KIND_VALIDATION_ERROR


def test_repair_path_error_missing(tmp_path):
    r = repair.repair_bib_file_inplace(path=str(tmp_path / "nope.bib"))
    assert r.ok is False
    assert r.kind == KIND_PATH_ERROR


def test_repair_path_error_not_a_file(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    r = repair.repair_bib_file_inplace(path=str(d))
    assert r.ok is False
    assert r.kind == KIND_PATH_ERROR


def test_repair_success_creates_backup_and_rewrites(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")
    original = open(path, "rb").read()

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is True
    assert r.kind == KIND_RESOLVED
    # Backup exists and is byte-identical to the original.
    assert os.path.exists(path + ".bak")
    assert open(path + ".bak", "rb").read() == original
    # File was rewritten (content changed for the resolvable entry).
    assert open(path, "rb").read() != original


def test_repair_summary_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path)

    summary = r.data["summary"]
    # repair_clean.bib has 2 entries: einstein1905 (resolvable) + nodoi (skipped).
    assert summary["entries"] == 2
    assert summary["repaired"] == 1
    assert summary["skipped"] == 1
    assert summary["failed"] == 0


def test_repair_per_entry_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path)

    outcomes = {e["key"]: e for e in r.data["entries"]}
    assert outcomes["einstein1905"]["outcome"] == "repaired"
    assert outcomes["nodoi"]["outcome"] == "skipped"
    assert outcomes["nodoi"]["doi"] is None


def test_repair_auto_normalize_true(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    norm_called = []

    def fake_normalize(bib_str):
        norm_called.append(bib_str)
        return adapter.ToolResult(
            KIND_RESOLVED, ok=True, data="@article{norm, title={Normalized}}"
        )

    monkeypatch.setattr(adapter, "normalize_entry", fake_normalize)
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path, auto_normalize=True)

    assert r.ok is True
    assert len(norm_called) == 1  # only the resolvable entry was normalized


def test_repair_auto_normalize_false_skips_normalize(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    norm_called = []
    monkeypatch.setattr(
        adapter, "normalize_entry",
        lambda bib_str: norm_called.append(bib_str) or adapter.ToolResult(
            KIND_RESOLVED, ok=True, data="normalized"
        ),
    )
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path, auto_normalize=False)

    assert r.ok is True
    assert norm_called == []  # normalize not invoked


def test_repair_per_entry_failure_does_not_abort(tmp_path, monkeypatch):
    # One entry resolves, the other (unresolvable doi) is skipped.
    def fake_resolve(identifier, timeout=30, **kw):
        if "does-not-exist" in (identifier or ""):
            return adapter.ToolResult(
                "unresolvable", ok=False, error="not found"
            )
        return _resolved_result(identifier, timeout)

    monkeypatch.setattr(adapter, "resolve_identifier", fake_resolve)
    path = _setup(tmp_path, "repair_mixed.bib")

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is True
    outcomes = {e["key"]: e for e in r.data["entries"]}
    assert outcomes["resolvable"]["outcome"] == "repaired"
    assert outcomes["unresolvable"]["outcome"] == "skipped"
    assert outcomes["nodoi"]["outcome"] == "skipped"
    assert r.data["summary"]["repaired"] == 1
    assert r.data["summary"]["skipped"] == 2


def test_repair_preserves_skipped_entries_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")
    original_content = open(path, "r", encoding="utf-8").read()

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is True
    new_content = open(path, "r", encoding="utf-8").read()
    # The no-identifier entry's note must still appear in the rewritten file.
    assert "An entry with no DOI and no title" in new_content


def test_repair_existing_bak_blocks_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")
    # Pre-create a .bak.
    with open(path + ".bak", "w", encoding="utf-8") as f:
        f.write("precious backup")

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is False
    assert r.kind == KIND_BACKUP_CONFLICT
    # Original file untouched.
    assert "On the Electrodynamics" in open(path, "r", encoding="utf-8").read()
    # Precious backup preserved.
    assert open(path + ".bak", "r", encoding="utf-8").read() == "precious backup"


def test_repair_overwrite_backup_true_proceeds(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")
    with open(path + ".bak", "w", encoding="utf-8") as f:
        f.write("old backup")

    r = repair.repair_bib_file_inplace(path=path, overwrite_backup=True)

    assert r.ok is True
    # .bak now contains the pre-repair original, not "old backup".
    assert "On the Electrodynamics" in open(path + ".bak", "r", encoding="utf-8").read()


def test_repair_write_failure_triggers_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")
    original = open(path, "rb").read()

    def bad_write(target, content):
        raise OSError("disk full")

    monkeypatch.setattr(repair, "_atomic_write", bad_write)

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is False
    assert r.kind == KIND_REPAIR_ERROR
    assert "restored from backup" in (r.error or "")
    # Original restored byte-for-byte.
    assert open(path, "rb").read() == original
    # Backup retained.
    assert os.path.exists(path + ".bak")


def test_repair_backup_retained_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path)

    assert r.ok is True
    assert r.data["backup"] == path + ".bak"
    assert os.path.exists(path + ".bak")


def test_repair_report_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _resolved_result)
    path = _setup(tmp_path, "repair_clean.bib")

    r = repair.repair_bib_file_inplace(path=path)

    assert set(r.data.keys()) == {"entries", "summary", "backup"}
    assert set(r.data["summary"].keys()) == {"entries", "repaired", "skipped", "failed"}
    for e in r.data["entries"]:
        assert set(e.keys()) == {"key", "doi", "outcome", "error"}


# --- bibtex-repair-comments tests -------------------------------------------

def _canonical_smith():
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


def _canonical_resolver(identifier, timeout=30, **kw):
    """Return canonical for anything except specifically unresolvable DOIs."""
    if "10.9999" in (identifier or ""):
        return adapter.ToolResult("unresolvable", ok=False, error="not found")
    return adapter.ToolResult(KIND_RESOLVED, ok=True, data=_canonical_smith())


def test_grouped_output_has_status_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_mixed.bib")
    r = repair.repair_bib_file_inplace(path=path, group_output=True)
    assert r.ok is True
    content = open(path, "r", encoding="utf-8").read()
    # Should have % STATUS lines
    assert "% STATUS: VERIFIED" in content
    assert "% STATUS: NOT FOUND" in content
    assert "% STATUS: NO IDENTIFIER" in content


def test_grouped_output_has_section_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_mixed.bib")
    r = repair.repair_bib_file_inplace(path=path, group_output=True)
    content = open(path, "r", encoding="utf-8").read()
    assert "% ===== VERIFIED =====" in content
    assert "% ===== NOT FOUND =====" in content
    assert "% ===== NO IDENTIFIER =====" in content


def test_grouped_output_omits_empty_sections(tmp_path, monkeypatch):
    # Use a fixture where all entries resolve -> no NOT FOUND or NO IDENTIFIER sections.
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_clean.bib")
    r = repair.repair_bib_file_inplace(path=path, group_output=True)
    content = open(path, "r", encoding="utf-8").read()
    # UNAVAILABLE section should NOT appear (no network errors).
    assert "% ===== UNAVAILABLE =====" not in content


def test_flat_output_no_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_mixed.bib")
    r = repair.repair_bib_file_inplace(path=path, group_output=False)
    content = open(path, "r", encoding="utf-8").read()
    assert "% STATUS:" not in content
    assert "% =====" not in content


def test_audit_on_grouped_file_works(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_mixed.bib")
    repair.repair_bib_file_inplace(path=path, group_output=True)

    # Now audit the grouped output — should parse all entries ignoring comments.
    from doi2bib3.mcp_server.tools import audit_bib_file
    r = audit_bib_file(path)
    assert r.ok is True
    assert r.data["summary"]["entries"] == 3  # 3 real entries, comments ignored


def test_rerepair_on_grouped_file_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "resolve_identifier", _canonical_resolver)
    path = _setup(tmp_path, "repair_mixed.bib")
    repair.repair_bib_file_inplace(path=path, group_output=True)
    outcomes1 = repair.repair_bib_file_inplace(path=path, group_output=True, overwrite_backup=True).data["summary"]
    # Re-running should produce same counts.
    outcomes2 = repair.repair_bib_file_inplace(path=path, group_output=True, overwrite_backup=True).data["summary"]
    assert outcomes1 == outcomes2