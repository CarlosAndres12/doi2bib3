"""SQLite-backed reference cache for doi2bib3 MCP server.

Caches resolved BibTeX references keyed by their lookup identifier so repeated
audit/repair/resolve calls are instant and deterministic within the TTL.

Uses stdlib ``sqlite3`` with WAL mode — no new runtime dependencies.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Schema version: increment if the table shape changes to trigger auto-reset.
_SCHEMA_VERSION = 1

# Default TTL: 7 days in seconds.
DEFAULT_TTL = 7 * 24 * 3600  # 604800

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS "references" (
    id       TEXT PRIMARY KEY,         -- the lookup identifier
    doi      TEXT,                     -- resolved DOI (if any)
    bibtex   TEXT NOT NULL,            -- raw canonical BibTeX
    normalized_bibtex TEXT,            -- normalized BibTeX (if available)
    fetched_at REAL NOT NULL           -- epoch timestamp
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_references_doi ON "references"(doi);
CREATE INDEX IF NOT EXISTS idx_references_fetched ON "references"(fetched_at);
"""

# Insert or replace a cache row.
_UPSERT = """
INSERT OR REPLACE INTO "references" (id, doi, bibtex, normalized_bibtex, fetched_at)
VALUES (?, ?, ?, ?, ?)
"""

# Lookup by identifier.
_SELECT = 'SELECT id, doi, bibtex, normalized_bibtex, fetched_at FROM "references" WHERE id = ?'

# Count rows.
_COUNT = 'SELECT COUNT(*) FROM "references"'

# Summary stats query.
_STATS = """
SELECT
    COUNT(*) AS entries,
    MIN(fetched_at) AS oldest,
    MAX(fetched_at) AS newest
FROM "references"
"""

# Meta key access helpers.
_META_GET = "SELECT value FROM meta WHERE key = ?"
_META_SET = "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)"


@dataclass
class CacheRow:
    id: str
    doi: Optional[str]
    bibtex: str
    normalized_bibtex: Optional[str]
    fetched_at: float


def _default_db_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(xdg) / "doi2bib3" / "references.db"


# --- Module-level singleton --------------------------------------------------
_db_path: Optional[Path] = None
_conn: Optional[sqlite3.Connection] = None
_ttl: int = DEFAULT_TTL


def configure(db_path: Optional[str | Path] = None, ttl: int = DEFAULT_TTL) -> None:
    """Set the cache database path and TTL.

    Called once before the server loop starts, typically from ``__main__``.
    Precedence: explicit ``db_path`` arg > ``DOI2BIB3_CACHE`` env var >
    XDG default.
    """
    global _db_path, _ttl
    if db_path:
        _db_path = Path(db_path)
    else:
        env = os.environ.get("DOI2BIB3_CACHE")
        _db_path = Path(env) if env else _default_db_path()
    _ttl = int(os.environ.get("DOI2BIB3_CACHE_TTL", str(ttl)))
    _db_path.parent.mkdir(parents=True, exist_ok=True)


def _get_path() -> Path:
    global _db_path
    if _db_path is None:
        configure()
    return _db_path  # type: ignore[return-value]


def _get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(_META_GET, ("schema_version",)).fetchone()
    return int(row[0]) if row else 0


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(_META_SET, (key, value))


def get_connection() -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection to the cache database.

    Creates the database and schema on first use. Automatically resets the
    schema if the stored version does not match (cache is not a source of
    truth).
    """
    global _conn
    if _conn is not None:
        # Quick health check.
        try:
            _conn.execute("SELECT 1")
            return _conn
        except (sqlite3.DatabaseError, sqlite3.ProgrammingError):
            _conn = None

    path = _get_path()
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # metadata-only store

    conn.executescript(_CREATE_TABLES)

    version = _get_schema_version(conn)
    if version != _SCHEMA_VERSION:
        # Drop and recreate — it's a cache, not a source of truth.
        conn.execute('DROP TABLE IF EXISTS "references"')
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.executescript(_CREATE_TABLES)
        _set_meta(conn, "schema_version", str(_SCHEMA_VERSION))
        # Reset hit/miss counters.
        _set_meta(conn, "hits", "0")
        _set_meta(conn, "misses", "0")

    conn.executescript(_CREATE_INDEXES)
    conn.commit()

    _conn = conn
    return conn


def _increment_counter(conn: sqlite3.Connection, key: str) -> None:
    row = conn.execute(_META_GET, (key,)).fetchone()
    current = int(row[0]) if row else 0
    conn.execute(_META_SET, (key, str(current + 1)))
    conn.commit()


def lookup(identifier: str) -> Optional[CacheRow]:
    """Look up an identifier in the cache.

    Returns the row if it exists and is within the TTL, else None.
    """
    conn = get_connection()
    row = conn.execute(_SELECT, (identifier,)).fetchone()
    if row is None:
        _increment_counter(conn, "misses")
        return None

    fetched_at = row[4]
    if time.time() - fetched_at > _ttl:
        # Expired — treat as miss but keep the row for stale-on-error reads.
        _increment_counter(conn, "misses")
        return None

    _increment_counter(conn, "hits")
    return CacheRow(
        id=row[0],
        doi=row[1],
        bibtex=row[2],
        normalized_bibtex=row[3],
        fetched_at=fetched_at,
    )


def lookup_expired(identifier: str) -> Optional[CacheRow]:
    """Look up an identifier including expired entries (for stale-on-error).

    Returns the row even if expired. Returns None only if no row exists at all.
    """
    conn = get_connection()
    row = conn.execute(_SELECT, (identifier,)).fetchone()
    if row is None:
        return None
    return CacheRow(
        id=row[0],
        doi=row[1],
        bibtex=row[2],
        normalized_bibtex=row[3],
        fetched_at=row[4],
    )


def store(
    identifier: str,
    doi: Optional[str],
    bibtex: str,
    normalized_bibtex: Optional[str] = None,
) -> None:
    """Store (or update) a resolved reference in the cache."""
    conn = get_connection()
    conn.execute(
        _UPSERT,
        (identifier, doi, bibtex, normalized_bibtex, time.time()),
    )
    conn.commit()


def stats() -> dict:
    """Return cache statistics as a dict.

    Keys: entries, hit_rate, oldest_fetched, newest_fetched, db_path, db_size_bytes.
    """
    conn = get_connection()
    row = conn.execute(_STATS).fetchone()
    entries = row[0] or 0
    oldest = row[1]
    newest = row[2]

    hits_row = conn.execute(_META_GET, ("hits",)).fetchone()
    misses_row = conn.execute(_META_GET, ("misses",)).fetchone()
    hits = int(hits_row[0]) if hits_row else 0
    misses = int(misses_row[0]) if misses_row else 0
    total = hits + misses
    hit_rate = round(hits / total, 4) if total > 0 else None

    db_path = str(_get_path())
    try:
        db_size_bytes = os.path.getsize(db_path)
    except OSError:
        db_size_bytes = 0

    return {
        "entries": entries,
        "hit_rate": hit_rate,
        "oldest_fetched": oldest,
        "newest_fetched": newest,
        "db_path": db_path,
        "db_size_bytes": db_size_bytes,
    }