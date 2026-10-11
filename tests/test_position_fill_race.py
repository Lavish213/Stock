# Covers _apply_fill_to_position - previously untested entirely, for
# either correctness or concurrency. Real bug found and fixed: it was a
# plain read-then-write (SELECT qty/avg_price, compute in Python, write
# back), so two fills landing for the same symbol close together (e.g. a
# new entry's fill confirmation and an exit's bracket-fill confirmation,
# recorded from two different monitor threads) could race - one fill's
# effect silently lost (the classic lost-update problem), or DuckDB
# raising a raw TransactionException for the losing side of two genuinely
# concurrent UPDATEs. Fixed with an in-process lock (this bot is single-
# process by design - see run_ingest.py) plus atomic SQL and conflict-
# retry underneath as defense-in-depth. Confirmed directly via real
# concurrent threads, not assumed - both the realistic 2-way case and an
# adversarial 8-way stress case.
from __future__ import annotations

import threading

from lavish_core.db.hybrid_store import HybridStore


def _store(tmp_path, name="test.duckdb"):
    return HybridStore(duckdb_path=tmp_path / name)


# ---------------------------------------------------------------------------
# Single-threaded correctness (the original Python-level logic, now in SQL)
# ---------------------------------------------------------------------------

def test_first_buy_establishes_the_position(tmp_path):
    store = _store(tmp_path)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 100.0)
    assert store.get_position("AAPL") == {"qty": 10.0, "avg_price": 100.0}


def test_second_buy_reaverages_the_cost_basis(tmp_path):
    store = _store(tmp_path)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 100.0)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 120.0)
    pos = store.get_position("AAPL")
    assert pos["qty"] == 20.0
    assert pos["avg_price"] == 110.0  # (10*100 + 10*120) / 20


def test_sell_reduces_qty_without_changing_avg_price(tmp_path):
    store = _store(tmp_path)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 100.0)
    store._apply_fill_to_position("AAPL", "sell", 4.0, 150.0)  # sell price must not affect avg_price
    pos = store.get_position("AAPL")
    assert pos["qty"] == 6.0
    assert pos["avg_price"] == 100.0


def test_selling_to_exactly_flat_deletes_the_position(tmp_path):
    store = _store(tmp_path)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 100.0)
    store._apply_fill_to_position("AAPL", "sell", 10.0, 150.0)
    assert store.get_position("AAPL") is None


def test_different_symbols_are_independent(tmp_path):
    store = _store(tmp_path)
    store._apply_fill_to_position("AAPL", "buy", 10.0, 100.0)
    store._apply_fill_to_position("MSFT", "buy", 5.0, 50.0)
    assert store.get_position("AAPL") == {"qty": 10.0, "avg_price": 100.0}
    assert store.get_position("MSFT") == {"qty": 5.0, "avg_price": 50.0}


# ---------------------------------------------------------------------------
# Concurrency - the actual bug
# ---------------------------------------------------------------------------

def test_two_concurrent_fills_on_the_same_symbol_both_apply(tmp_path):
    # The realistic production scenario: a new entry's fill confirmation
    # and an existing position's exit fill confirmation, landing at
    # close to the same time from two different threads.
    db_path = tmp_path / "test.duckdb"
    seed = HybridStore(duckdb_path=db_path)
    seed._apply_fill_to_position("AAPL", "buy", 10.0, 90.0)  # pre-existing position

    barrier = threading.Barrier(2)
    errors = []

    def do_buy():
        try:
            store = HybridStore(duckdb_path=db_path)
            barrier.wait()
            store._apply_fill_to_position("AAPL", "buy", 5.0, 100.0)
        except Exception as e:
            errors.append(("buy", e))

    def do_sell():
        try:
            store = HybridStore(duckdb_path=db_path)
            barrier.wait()
            store._apply_fill_to_position("AAPL", "sell", 3.0, 100.0)
        except Exception as e:
            errors.append(("sell", e))

    threads = [threading.Thread(target=do_buy), threading.Thread(target=do_sell)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not any(t.is_alive() for t in threads), "a thread hung"
    assert not errors, f"unexpected errors: {errors}"

    final = seed.get_position("AAPL")
    assert final["qty"] == 12.0  # 10 + 5 - 3 - neither fill's delta was lost


def test_adversarial_concurrent_fills_converge_correctly(tmp_path):
    # Far beyond what this bot's design would ever actually produce for
    # one symbol at once (realistically at most 2 overlapping writers) -
    # deliberately adversarial to prove the fix holds even under heavy
    # contention, not just the realistic case.
    db_path = tmp_path / "test.duckdb"
    HybridStore(duckdb_path=db_path)

    N = 8  # even split: N/2 buys of 1, N/2 sells of 1 -> net flat
    barrier = threading.Barrier(N)
    errors = []

    def worker(i):
        try:
            store = HybridStore(duckdb_path=db_path)
            barrier.wait()
            if i % 2 == 0:
                store._apply_fill_to_position("AAPL", "buy", 1.0, 100.0 + i)
            else:
                store._apply_fill_to_position("AAPL", "sell", 1.0, 100.0)
        except Exception as e:
            errors.append((i, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "a thread hung"
    assert not errors, f"unexpected errors: {errors}"

    store = HybridStore(duckdb_path=db_path)
    assert store.get_position("AAPL") is None  # net zero -> flat, no leftover row
