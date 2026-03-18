#!/usr/bin/env python3
"""SQLite benchmark script for cloud sandbox comparison.

Runs a suite of SQLite operations and outputs results as JSON.
Designed to produce deterministic, reproducible results across providers.
"""
import sqlite3
import time
import os
import json
import random
import string
import sys

DB_PATH = "/tmp/bench.db"


def random_string(length=50):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def bench_create_table(conn):
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS bench")
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


def bench_sequential_inserts(conn, n=10000):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute(
            "INSERT INTO bench (name, value, data) VALUES (?, ?, ?)",
            (f"item_{i}", random.random() * 1000, random_string()),
        )
    conn.commit()
    return time.perf_counter() - start


def bench_batch_inserts(conn, n=50000):
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


def bench_select_all(conn):
    c = conn.cursor()
    start = time.perf_counter()
    rows = c.execute("SELECT COUNT(*) FROM bench").fetchone()
    elapsed = time.perf_counter() - start
    return elapsed, rows[0]


def bench_select_range(conn, iterations=1000):
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


def bench_select_like(conn, iterations=500):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(iterations):
        c.execute(
            "SELECT * FROM bench WHERE name LIKE ? LIMIT 50", (f"item_{i}%",)
        )
        c.fetchall()
    return time.perf_counter() - start


def bench_update(conn, n=5000):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute(
            "UPDATE bench SET value = ? WHERE name = ?",
            (random.random() * 1000, f"item_{i}"),
        )
    conn.commit()
    return time.perf_counter() - start


def bench_delete(conn, n=2000):
    c = conn.cursor()
    start = time.perf_counter()
    for i in range(n):
        c.execute("DELETE FROM bench WHERE name = ?", (f"batch_{i}",))
    conn.commit()
    return time.perf_counter() - start


def bench_transaction(conn, n=5000):
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


def get_db_size():
    if os.path.exists(DB_PATH):
        return os.path.getsize(DB_PATH) / (1024 * 1024)
    return 0


def main():
    random.seed(42)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache

    print(f"SQLite version: {sqlite3.sqlite_version}")
    print(f"Python version: {sys.version}")
    print(f"Database path: {DB_PATH}")
    print("-" * 60)

    results = {}

    bench_create_table(conn)

    t = bench_sequential_inserts(conn, 10000)
    results["sequential_inserts_10k"] = round(t, 4)
    print(f"Sequential inserts (10k):      {t:.4f}s  ({10000/t:.0f} rows/s)")

    t = bench_batch_inserts(conn, 50000)
    results["batch_inserts_50k"] = round(t, 4)
    print(f"Batch inserts (50k):           {t:.4f}s  ({50000/t:.0f} rows/s)")

    t, count = bench_select_all(conn)
    results["select_count"] = round(t, 4)
    print(f"SELECT COUNT(*) ({count} rows): {t:.4f}s")

    t = bench_select_range(conn, 1000)
    results["range_queries_1k"] = round(t, 4)
    print(f"Range queries (1k):            {t:.4f}s  ({1000/t:.0f} queries/s)")

    t = bench_select_like(conn, 500)
    results["like_queries_500"] = round(t, 4)
    print(f"LIKE queries (500):            {t:.4f}s  ({500/t:.0f} queries/s)")

    t = bench_update(conn, 5000)
    results["updates_5k"] = round(t, 4)
    print(f"Updates (5k):                  {t:.4f}s  ({5000/t:.0f} rows/s)")

    t = bench_delete(conn, 2000)
    results["deletes_2k"] = round(t, 4)
    print(f"Deletes (2k):                  {t:.4f}s  ({2000/t:.0f} rows/s)")

    t = bench_transaction(conn, 5000)
    results["transaction_inserts_5k"] = round(t, 4)
    print(f"Transaction inserts (5k):      {t:.4f}s  ({5000/t:.0f} rows/s)")

    t = bench_aggregate(conn)
    results["aggregates"] = round(t, 4)
    print(f"Aggregates:                    {t:.4f}s")

    t = bench_join(conn)
    results["join_query"] = round(t, 4)
    print(f"Join query:                    {t:.4f}s")

    db_size = get_db_size()
    results["db_size_mb"] = round(db_size, 2)
    print(f"\nDatabase size: {db_size:.2f} MB")

    total = sum(v for k, v in results.items() if k != "db_size_mb")
    results["total_time"] = round(total, 4)
    print(f"Total benchmark time: {total:.4f}s")

    print("\n--- JSON ---")
    print(json.dumps(results, indent=2))

    conn.close()
    os.remove(DB_PATH)


if __name__ == "__main__":
    main()
