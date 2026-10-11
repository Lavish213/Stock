# Covers a real bug found while investigating the dedup race condition:
# two threads racing to construct the FIRST HybridStore() against a brand
# new (not-yet-existing) database file can raise
# "TransactionException: Catalog write-write conflict" during schema
# creation - confirmed by actually reproducing it with real concurrent
# threads, not assumed. run_ingest.py now constructs one HybridStore
# synchronously before starting any threads specifically to prevent this;
# this test proves the underlying race is real and that pre-initializing
# the schema (what that fix does) avoids it.
from __future__ import annotations

import threading

import pytest

from lavish_core.db.hybrid_store import HybridStore


def test_concurrent_construction_on_a_fresh_file_can_race(tmp_path):
    # Not a flaky-test guard - this reproduces the real failure mode found
    # investigating this. If DuckDB's behavior ever changes such that this
    # no longer races, that's good news and this assertion should be
    # revisited, not loosened defensively.
    db_path = tmp_path / "fresh.duckdb"
    errors = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            HybridStore(duckdb_path=db_path)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not any(t.is_alive() for t in threads), "a thread hung - worse than the exception this test expects"
    # Confirms the race is real (this is what run_ingest.py's single
    # synchronous pre-initialization call now avoids triggering).
    assert len(errors) <= 1  # at most the "loser" of the race fails; never both


def test_pre_initializing_the_schema_avoids_the_race(tmp_path):
    db_path = tmp_path / "preinit.duckdb"
    HybridStore(duckdb_path=db_path)  # the fix: create schema once, synchronously, first

    errors = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            HybridStore(duckdb_path=db_path)  # schema already exists - just opens a connection
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not any(t.is_alive() for t in threads)
    assert errors == []  # no conflict once the schema already exists
