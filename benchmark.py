#!/usr/bin/env python3
"""SQLite benchmark script for cloud sandbox comparison.

Runs a suite of SQLite operations and outputs results as JSON.
Designed to produce deterministic, reproducible results across providers.

Usage:
  python benchmark.py                          # default: WAL mode, small dataset
  python benchmark.py --mode fsync             # synchronous=FULL to stress disk I/O
  python benchmark.py --mode large             # 100MB+ dataset that exceeds cache
  python benchmark.py --iterations 3           # run 3 times, report mean/stddev
  python benchmark.py --mode fsync --iterations 5
"""
import argparse
try:
    import sqlite3
except ImportError:
    import pysqlite3 as sqlite3
import statistics
import time
import os
import json
import random
import string
import sys
import threading

DB_PATH = "/tmp/bench.db"


def random_string(length=50):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def bench_create_table(conn, large=False):
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS bench")
    c.execute("DROP TABLE IF EXISTS bench2")
    c.execute(
        """
        CREATE TABLE bench (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value REAL,
            data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    c.execute("CREATE INDEX idx_name ON bench(name)")
    c.execute("CREATE INDEX idx_value ON bench(value)")
    conn.commit()


# ---------------------------------------------------------------------------
# Individual benchmarks — each returns elapsed seconds
# ---------------------------------------------------------------------------

def bench_sequential_inserts(conn, n):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute(
            "INSERT INTO bench (name, value, data) VALUES (?, ?, ?)",
            (f"item_{i}", random.random() * 1000, random_string()),
        )
    conn.commit()
    return time.perf_counter() - start


def bench_batch_inserts(conn, n):
    c = conn.cursor()
    start = time.perf_counter()
    data = [
        (f"batch_{i}", random.random() * 1000, random_string()) for i in range(n)
    ]
    c.executemany(
        "INSERT INTO bench (name, value, data) VALUES (?, ?, ?)", data
    )
    conn.commit()
    return time.perf_counter() - start


def bench_select_count(conn):
    c = conn.cursor()
    start = time.perf_counter()
    rows = c.execute("SELECT COUNT(*) FROM bench").fetchone()
    elapsed = time.perf_counter() - start
    return elapsed, rows[0]


def bench_select_range(conn, iterations):
    c = conn.cursor()
    start = time.perf_counter()
    for _ in range(iterations):
        low = random.random() * 500
        c.execute(
            "SELECT * FROM bench WHERE value BETWEEN ? AND ? LIMIT 100",
            (low, low + 100),
        )
        c.fetchall()
    return time.perf_counter() - start


def bench_select_like(conn, iterations):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(iterations):
        c.execute(
            "SELECT * FROM bench WHERE name LIKE ? LIMIT 50", (f"item_{i}%",)
        )
        c.fetchall()
    return time.perf_counter() - start


def bench_update(conn, n):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute(
            "UPDATE bench SET value = ? WHERE name = ?",
            (random.random() * 1000, f"item_{i}"),
        )
    conn.commit()
    return time.perf_counter() - start


def bench_delete(conn, n):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute("DELETE FROM bench WHERE name = ?", (f"batch_{i}",))
    conn.commit()
    return time.perf_counter() - start


def bench_transaction(conn, n):
    c = conn.cursor()
    start = time.perf_counter()
    c.execute("BEGIN")
    for i in range(n):
        c.execute(
            "INSERT INTO bench (name, value, data) VALUES (?, ?, ?)",
            (f"tx_{i}", random.random() * 1000, random_string(30)),
        )
    c.execute("COMMIT")
    return time.perf_counter() - start


def bench_aggregate(conn):
    c = conn.cursor()
    start = time.perf_counter()
    c.execute(
        "SELECT AVG(value), MIN(value), MAX(value), SUM(value) FROM bench"
    )
    c.fetchone()
    c.execute(
        "SELECT name, COUNT(*) FROM bench GROUP BY substr(name, 1, 4)"
    )
    c.fetchall()
    return time.perf_counter() - start


def bench_join(conn):
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS bench2")
    c.execute(
        "CREATE TABLE bench2 (id INTEGER PRIMARY KEY, bench_id INTEGER, extra TEXT)"
    )
    data = [
        (random.randint(1, 60000), random_string(20)) for _ in range(10000)
    ]
    c.executemany("INSERT INTO bench2 (bench_id, extra) VALUES (?, ?)", data)
    conn.commit()
    start = time.perf_counter()
    c.execute(
        """
        SELECT b.name, b.value, b2.extra
        FROM bench b
        JOIN bench2 b2 ON b.id = b2.bench_id
        WHERE b.value > 500
        LIMIT 1000
    """
    )
    c.fetchall()
    return time.perf_counter() - start


def bench_concurrent_reads(db_path, num_threads=4, queries_per_thread=500):
    """Spawn multiple threads doing read queries concurrently."""
    timings = []
    errors = []

    def reader(thread_id):
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        c = conn.cursor()
        rng = random.Random(thread_id)
        t0 = time.perf_counter()
        for _ in range(queries_per_thread):
            low = rng.random() * 500
            c.execute(
                "SELECT * FROM bench WHERE value BETWEEN ? AND ? LIMIT 100",
                (low, low + 100),
            )
            c.fetchall()
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)
        conn.close()

    threads = []
    wall_start = time.perf_counter()
    for tid in range(num_threads):
        t = threading.Thread(target=reader, args=(tid,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    wall_time = time.perf_counter() - wall_start

    total_queries = num_threads * queries_per_thread
    return wall_time, total_queries


# ---------------------------------------------------------------------------
# Mode configs — scale factors for each mode
# ---------------------------------------------------------------------------

MODES = {
    "default": {
        "label": "Default (WAL, small dataset)",
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size_kb": 64000,
        "seq_inserts": 10000,
        "batch_inserts": 50000,
        "range_queries": 1000,
        "like_queries": 500,
        "updates": 5000,
        "deletes": 2000,
        "tx_inserts": 5000,
    },
    "fsync": {
        "label": "Fsync stress (synchronous=FULL, no WAL)",
        "journal_mode": "DELETE",
        "synchronous": "FULL",
        "cache_size_kb": 64000,
        "seq_inserts": 5000,
        "batch_inserts": 20000,
        "range_queries": 500,
        "like_queries": 200,
        "updates": 2000,
        "deletes": 1000,
        "tx_inserts": 5000,
    },
    "large": {
        "label": "Large dataset (WAL, exceeds cache)",
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size_kb": 8000,  # 8MB cache to force spills
        "seq_inserts": 50000,
        "batch_inserts": 200000,
        "range_queries": 2000,
        "like_queries": 1000,
        "updates": 10000,
        "deletes": 5000,
        "tx_inserts": 10000,
    },
}


def get_db_size():
    if os.path.exists(DB_PATH):
        size = os.path.getsize(DB_PATH)
        wal = DB_PATH + "-wal"
        if os.path.exists(wal):
            size += os.path.getsize(wal)
        return size / (1024 * 1024)
    return 0


def run_single(mode_cfg):
    """Run one full benchmark pass. Returns dict of results."""
    random.seed(42)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    for suffix in ("-wal", "-shm"):
        p = DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"PRAGMA journal_mode={mode_cfg['journal_mode']}")
    conn.execute(f"PRAGMA synchronous={mode_cfg['synchronous']}")
    conn.execute(f"PRAGMA cache_size=-{mode_cfg['cache_size_kb']}")

    results = {}

    bench_create_table(conn)

    n = mode_cfg["seq_inserts"]
    t = bench_sequential_inserts(conn, n)
    results["sequential_inserts"] = round(t, 4)

    n = mode_cfg["batch_inserts"]
    t = bench_batch_inserts(conn, n)
    results["batch_inserts"] = round(t, 4)

    t, count = bench_select_count(conn)
    results["select_count"] = round(t, 4)
    results["row_count"] = count

    n = mode_cfg["range_queries"]
    t = bench_select_range(conn, n)
    results["range_queries"] = round(t, 4)

    n = mode_cfg["like_queries"]
    t = bench_select_like(conn, n)
    results["like_queries"] = round(t, 4)

    n = mode_cfg["updates"]
    t = bench_update(conn, n)
    results["updates"] = round(t, 4)

    n = mode_cfg["deletes"]
    t = bench_delete(conn, n)
    results["deletes"] = round(t, 4)

    n = mode_cfg["tx_inserts"]
    t = bench_transaction(conn, n)
    results["transaction_inserts"] = round(t, 4)

    t = bench_aggregate(conn)
    results["aggregates"] = round(t, 4)

    t = bench_join(conn)
    results["join_query"] = round(t, 4)

    # Concurrent reads (uses its own connections)
    conn.close()
    wall, total_q = bench_concurrent_reads(DB_PATH, num_threads=4, queries_per_thread=500)
    results["concurrent_reads_wall"] = round(wall, 4)
    results["concurrent_reads_total_queries"] = total_q

    results["db_size_mb"] = round(get_db_size(), 2)

    total = sum(
        v for k, v in results.items()
        if k not in ("db_size_mb", "row_count", "concurrent_reads_total_queries")
    )
    results["total_time"] = round(total, 4)

    # Cleanup
    os.remove(DB_PATH)
    for suffix in ("-wal", "-shm"):
        p = DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)

    return results


def main():
    parser = argparse.ArgumentParser(description="SQLite sandbox benchmark")
    parser.add_argument(
        "--mode",
        choices=list(MODES.keys()),
        default="default",
        help="Benchmark mode (default: default)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations to run (default: 1). Reports mean/stddev when > 1.",
    )
    args = parser.parse_args()

    mode_cfg = MODES[args.mode]

    print(f"SQLite version: {sqlite3.sqlite_version}")
    print(f"Python version: {sys.version}")
    print(f"Mode: {mode_cfg['label']}")
    print(f"Iterations: {args.iterations}")
    print(f"Journal: {mode_cfg['journal_mode']}, Sync: {mode_cfg['synchronous']}, Cache: {mode_cfg['cache_size_kb']}KB")
    print("-" * 60)

    all_runs = []
    for i in range(args.iterations):
        if args.iterations > 1:
            print(f"\n--- Iteration {i+1}/{args.iterations} ---")
        run_results = run_single(mode_cfg)
        all_runs.append(run_results)

        # Print this iteration
        cfg = mode_cfg
        print(f"  Sequential inserts ({cfg['seq_inserts']}):  {run_results['sequential_inserts']:.4f}s")
        print(f"  Batch inserts ({cfg['batch_inserts']}):     {run_results['batch_inserts']:.4f}s")
        print(f"  SELECT COUNT(*) ({run_results['row_count']} rows): {run_results['select_count']:.4f}s")
        print(f"  Range queries ({cfg['range_queries']}):     {run_results['range_queries']:.4f}s")
        print(f"  LIKE queries ({cfg['like_queries']}):       {run_results['like_queries']:.4f}s")
        print(f"  Updates ({cfg['updates']}):                 {run_results['updates']:.4f}s")
        print(f"  Deletes ({cfg['deletes']}):                 {run_results['deletes']:.4f}s")
        print(f"  Transaction inserts ({cfg['tx_inserts']}):  {run_results['transaction_inserts']:.4f}s")
        print(f"  Aggregates:                    {run_results['aggregates']:.4f}s")
        print(f"  Join query:                    {run_results['join_query']:.4f}s")
        qps = run_results['concurrent_reads_total_queries'] / run_results['concurrent_reads_wall']
        print(f"  Concurrent reads (4 threads):  {run_results['concurrent_reads_wall']:.4f}s ({qps:.0f} q/s)")
        print(f"  DB size: {run_results['db_size_mb']:.2f} MB")
        print(f"  Total: {run_results['total_time']:.4f}s")

    # Build summary
    if args.iterations == 1:
        summary = all_runs[0]
    else:
        print(f"\n{'='*60}")
        print(f"  SUMMARY ({args.iterations} iterations)")
        print(f"{'='*60}")
        summary = {"iterations": args.iterations}
        numeric_keys = [
            k for k in all_runs[0]
            if isinstance(all_runs[0][k], (int, float))
            and k not in ("row_count", "concurrent_reads_total_queries")
        ]
        for key in numeric_keys:
            values = [r[key] for r in all_runs]
            mean = statistics.mean(values)
            summary[key] = {
                "mean": round(mean, 4),
                "stddev": round(statistics.stdev(values), 4) if len(values) > 1 else 0,
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }
            if key == "total_time":
                print(f"  Total: {mean:.4f}s +/- {summary[key]['stddev']:.4f}s")
        summary["row_count"] = all_runs[0]["row_count"]
        summary["all_runs"] = all_runs

    summary["mode"] = args.mode
    summary["mode_label"] = mode_cfg["label"]
    summary["sqlite_version"] = sqlite3.sqlite_version
    summary["python_version"] = sys.version.split()[0]

    print("\n--- JSON ---")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
