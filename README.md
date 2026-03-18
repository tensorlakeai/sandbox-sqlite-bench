# Sandbox File System I/O Benchmarks

Benchmarks SQLite performance across four sandbox providers: **Tensorlake**, **Vercel**, **Daytona**, and **E2B**.

All sandboxes were configured with **2 vCPUs and ~4 GB RAM** to ensure a fair comparison.

## What It Benchmarks

The benchmark script (`benchmark.py`) runs 10 SQLite operations with deterministic data (`random.seed(42)`):

| Operation | Description |
|---|---|
| Sequential inserts (10k) | Single-row INSERTs in autocommit |
| Batch inserts (50k) | `executemany` INSERT |
| SELECT COUNT(*) | Full table count |
| Range queries (1k) | `WHERE value BETWEEN ? AND ?` with index |
| LIKE queries (500) | `WHERE name LIKE ?` (full scan) |
| Updates (5k) | Single-row UPDATEs by name |
| Deletes (2k) | Single-row DELETEs by name |
| Transaction inserts (5k) | INSERTs within explicit BEGIN/COMMIT |
| Aggregates | AVG, MIN, MAX, SUM + GROUP BY |
| Join query | Two-table JOIN with WHERE filter |

SQLite pragmas: WAL mode, synchronous=NORMAL, 64MB cache.

## Results

| Provider | Total Time | Relative | vCPUs | Memory |
|---|---|---|---|---|
| **Tensorlake** | **1.30s** | 1.00x | 2 | 3.9 GB |
| **Vercel** | **1.60s** | 1.23x | 2 | 4.3 GB |
| **Daytona** | **1.77s** | 1.36x | 2 | 4.0 GB |
| **E2B** | **2.32s** | 1.78x | 2 | 3.9 GB |

```
Tensorlake  ██████████████████████████████  1.30s  (1.00x)
Vercel      ████████████████████████        1.60s  (1.23x)
Daytona     ██████████████████████          1.77s  (1.36x)
E2B         ████████████████                2.32s  (1.78x)
```

### Detailed Breakdown

| Benchmark | Tensorlake | Vercel | Daytona | E2B |
|---|---|---|---|---|
| Sequential inserts (10k) | 0.0517s (193k/s) | 0.0627s (159k/s) | 0.0680s (147k/s) | 0.0871s (115k/s) |
| Batch inserts (50k) | 0.2690s (186k/s) | 0.3044s (164k/s) | 0.3558s (141k/s) | 0.4861s (103k/s) |
| SELECT COUNT(*) | 0.0001s | 0.0001s | 0.0001s | 0.0002s |
| Range queries (1k) | 0.0917s (10.9k q/s) | 0.1168s (8.6k q/s) | 0.0963s (10.4k q/s) | 0.1551s (6.4k q/s) |
| LIKE queries (500) | 0.8238s (607 q/s) | 1.0438s (479 q/s) | 1.1723s (427 q/s) | 1.4993s (333 q/s) |
| Updates (5k) | 0.0138s (363k/s) | 0.0149s (336k/s) | 0.0159s (315k/s) | 0.0183s (274k/s) |
| Deletes (2k) | 0.0063s (318k/s) | 0.0059s (342k/s) | 0.0073s (272k/s) | 0.0071s (282k/s) |
| Transaction inserts (5k) | 0.0264s (189k/s) | 0.0338s (148k/s) | 0.0374s (134k/s) | 0.0381s (131k/s) |
| Aggregates | 0.0166s | 0.0200s | 0.0171s | 0.0226s |
| Join query | 0.0013s | 0.0020s | 0.0018s | 0.0027s |

### Environment

| | Tensorlake | Vercel | Daytona | E2B |
|---|---|---|---|---|
| Python | 3.12.3 | 3.13.1 | 3.13.12 | 3.13.12 |
| SQLite | 3.45.1 | 3.51.1 | 3.46.1 | 3.46.1 |
| vCPUs (verified) | 2 | 2 | 2 | 2 |
| Memory (verified) | 3.9 GB | 4.3 GB | 4.0 GB | 3.9 GB |

## Running the Benchmarks

### Prerequisites

Install and authenticate each provider's CLI:

| Provider | Install | Auth |
|---|---|---|
| Tensorlake | `pip install tensorlake` (into `/tmp/venv`) | `tensorlake login` |
| Vercel | `npm i -g sandbox` | `sandbox login` |
| Daytona | `brew install daytonaio/cli/daytona` | `daytona login` |
| E2B | `npm i -g e2b` | `e2b auth login` |

E2B requires building a template to configure CPU and memory:

```bash
mkdir /tmp/e2b-template
echo 'FROM python:3.13-slim' > /tmp/e2b-template/Dockerfile
cd /tmp/e2b-template
e2b template create bench-2cpu-4gb \
  --dockerfile Dockerfile \
  --cpu-count 2 \
  --memory-mb 4096
```

### Usage

```bash
# Run all providers
python run_benchmarks.py

# Run specific providers
python run_benchmarks.py tensorlake vercel

# Use a custom E2B template
python run_benchmarks.py e2b --e2b-template bench-2cpu-4gb
```

### Provider Notes

- **Vercel**: Python runtime does not include the native `_sqlite3` C extension. The runner installs `pysqlite3-binary` automatically.
- **Daytona**: Using `--class small` locks you to a snapshot with fixed 1 vCPU. To get custom resources, use `--cpu`/`--memory` with `-f Dockerfile` instead. `nproc` reports host CPUs, but cgroup enforces the requested limit.
- **E2B**: CPU and memory cannot be set at sandbox creation time. You must build a custom template with `e2b template create --cpu-count N --memory-mb N`.

### Files

```
benchmark.py          # SQLite benchmark (runs inside sandboxes)
run_benchmarks.py     # Orchestrator: create, copy, run, collect, cleanup
results/
  results.json        # Benchmark results (2 vCPU / 4 GB, March 17 2026)
```
