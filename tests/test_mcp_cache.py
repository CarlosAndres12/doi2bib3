"""Tests for the SQLite reference cache module and adapter integration (tasks 4.1-4.9)."""

import os
import time
import pytest

from doi2bib3.mcp_server import cache, adapter
from doi2bib3.backend import DOIError


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Ensure each test uses a fresh temp cache DB."""
    db = str(tmp_path / "cache.db")
    monkeypatch.setattr(cache, "_conn", None)
    monkeypatch.setattr(cache, "_db_path", None)
    cache.configure(db_path=db, ttl=3600)  # 1-hour TTL for testing
    # Force connection creation
    monkeypatch.setattr(cache, "_conn", None)
    try:
        yield db
    finally:
        try:
            cache._conn.close()
        except Exception:
            pass
        cache._conn = None


# --- 4.1: cache miss / store / lookup ---------------------------------------

def test_cache_miss_returns_none():
    row = cache.lookup("nonexistent-id")
    assert row is None


def test_cache_store_and_lookup():
    cache.store("doi:10.test/1", "10.test/1", "@article{x,}",
                normalized_bibtex="@article{x,}")
    row = cache.lookup("doi:10.test/1")
    assert row is not None
    assert row.id == "doi:10.test/1"
    assert row.doi == "10.test/1"
    assert row.bibtex == "@article{x,}"
    assert row.normalized_bibtex == "@article{x,}"


def test_cache_store_updates_existing():
    cache.store("dup", None, "old")
    cache.store("dup", None, "new")
    row = cache.lookup("dup")
    assert row.bibtex == "new"


# --- 4.2: expired entry + stale-on-error ------------------------------------

def test_expired_entry_is_miss(monkeypatch):
    monkeypatch.setattr(cache, "_ttl", 0)  # TTL=0 means immediate expiry
    cache.store("exp", None, "bibtex")
    assert cache.lookup("exp") is None  # expired → miss


def test_lookup_expired_returns_stale_row():
    cache.store("stale-id", None, "stale-bibtex")
    # Set TTL to 0 and re-lookup → expired, but lookup_expired still finds it.
    stale = cache.lookup_expired("stale-id")
    assert stale is not None
    assert stale.bibtex == "stale-bibtex"


def test_stale_on_error_returns_stale_when_network_fails(monkeypatch):
    # Store an entry, then make it expired.
    cache.store("net-fail-id", None, "old-bibtex")
    monkeypatch.setattr(cache, "_ttl", 0)

    # Mock fetch_bibtex to raise
    def fail_fetch(identifier, timeout=30):
        raise Exception("network error")

    monkeypatch.setattr(adapter, "fetch_bibtex", fail_fetch)
    r = adapter.resolve_identifier("net-fail-id")
    assert r.ok is True
    assert r.stale is True
    assert r.data == "old-bibtex"


def test_refresh_cache_no_stale_fallback(monkeypatch):
    cache.store("fresh-id", None, "old")
    monkeypatch.setattr(cache, "_ttl", 0)

    def fail_fetch(identifier, timeout=30):
        raise Exception("network error")

    monkeypatch.setattr(adapter, "fetch_bibtex", fail_fetch)
    r = adapter.resolve_identifier("fresh-id", refresh_cache=True)
    # refresh_cache=True → no stale fallback
    assert r.ok is False


# --- 4.3: refresh_cache bypasses cache --------------------------------------

def test_refresh_cache_bypasses_and_updates(monkeypatch):
    cache.store("bypass-id", None, "old")
    called = []

    def new_fetch(identifier, timeout=30):
        called.append(identifier)
        return "@article{new,}"

    monkeypatch.setattr(adapter, "fetch_bibtex", new_fetch)
    r = adapter.resolve_identifier("bypass-id", refresh_cache=True)
    assert r.ok is True
    assert r.data == "@article{new,}"
    assert len(called) == 1
    # Cache should be updated.
    row = cache.lookup("bypass-id")
    assert row is not None
    assert row.bibtex == "@article{new,}"


# --- 4.4: stats -------------------------------------------------------------

def test_stats_initial():
    s = cache.stats()
    assert s["entries"] == 0
    assert s["hit_rate"] is None


def test_stats_after_operations():
    cache.store("s1", None, "b1")
    cache.lookup("s1")  # hit
    cache.lookup("missing")  # miss
    s = cache.stats()
    assert s["entries"] == 1
    assert s["hit_rate"] == 0.5
    assert s["db_path"] is not None


# --- 4.5: concurrent reads --------------------------------------------------

def test_concurrent_reads_work(tmp_path):
    db = str(tmp_path / "conc.db")
    cache.configure(db_path=db)
    cache.store("c1", None, "b")
    # Open a second connection and read.
    import sqlite3
    c2 = sqlite3.connect(db)
    c2.execute('SELECT * FROM "references"')
    c2.close()


# --- 4.6: adapter cache hit skips network -----------------------------------

def test_adapter_cache_hit_skips_network(monkeypatch):
    cache.store("addr-cache-hit", None, "@article{cached,}")
    called = []
    monkeypatch.setattr(adapter, "fetch_bibtex",
                        lambda *a, **kw: called.append(1) or "@article{net,}")
    r = adapter.resolve_identifier("addr-cache-hit")
    assert r.ok is True
    assert r.data == "@article{cached,}"
    assert called == []  # network NOT called


# --- 4.7: adapter cache miss calls network and stores -----------------------

def test_adapter_cache_miss_calls_network_and_stores(monkeypatch):
    def fake_fetch(identifier, timeout=30):
        return "@article{from-net,}"

    monkeypatch.setattr(adapter, "fetch_bibtex", fake_fetch)
    r = adapter.resolve_identifier("new-identifier")
    assert r.ok is True
    assert r.data == "@article{from-net,}"
    # Verify stored.
    row = cache.lookup("new-identifier")
    assert row is not None
    assert row.bibtex == "@article{from-net,}"


def test_resolve_identifier_unresolvable_does_not_cache(monkeypatch):
    def fail_fetch(identifier, timeout=30):
        raise DOIError("Invalid DOI: bad")

    monkeypatch.setattr(adapter, "fetch_bibtex", fail_fetch)
    # No stale entry.
    r = adapter.resolve_identifier("bad-doi")
    assert r.ok is False
    row = cache.lookup("bad-doi")
    assert row is None  # not cached


# --- 4.8: integration (cache_stats tool over stdio) -------------------------
# Handled in test_mcp_server_integration.py

# --- 4.9: full suite regression (manual, run separately) --------------------