# SQLite Benchmark: Cloud Sandbox Providers

Benchmarks SQLite performance across four cloud sandbox providers: **Tensorlake**, **Vercel**, **Daytona**, and **E2B**.

## Quick Start

```bash
# Run all providers with default settings
python run_benchmarks.py

# Run specific providers
python run_benchmarks.py tensorlake vercel

# Use a custom E2B template with specific CPU/memory
python run_benchmarks.py e2b --e2b-template bench-2cpu-4gb
```

## Prerequisites

### CLI Tools

| Provider | Install | Auth |
|---|---|---|
| Tensorlake | `pip install tensorlake` (into `/tmp/venv`) | `tensorlake login` |
| Vercel | `npm i -g sandbox` | `sandbox login` |
| Daytona | `brew install daytonaio/cli/daytona` | `daytona login` |
| E2B | `npm i -g e2b` | `e2b auth login` |

### E2B Custom Template (for specific CPU/memory)

E2B requires building a template to configure CPU and memory:

```bash
mkdir /tmp/e2b-template
echo 'FROM python:3.13-slim' > /tmp/e2b-template/Dockerfile

cd /tmp/e2b-template
e2b template create bench-2cpu-4gb \
  --dockerfile Dockerfile \
  --cpu-count 2 \
  --memory-mb 4096

# Then run with:
python run_benchmarks.py e2b --e2b-template bench-2cpu-4gb
```

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

### Round 2: Normalized (2 vCPU / 4 GB target)

| Provider | Total Time | Relative | vCPUs | Memory |
|---|---|---|---|---|
| **Tensorlake** | **1.30s** | 1.00x | 2 | 3.9 GB |
| **Vercel** | **1.60s** | 1.23x | 2 | 4.3 GB |
| **Daytona** | **1.82s** | 1.40x | 1\* | host |
| **E2B** | **2.32s** | 1.78x | 2 | 3.9 GB |

\*Daytona: Only "small" class runners were available. `nproc` reports 64 (host CPUs) but cgroup limits to 1 vCPU.

### Round 1: Provider Defaults

| Provider | Total Time | vCPUs | Memory |
|---|---|---|---|
| Tensorlake | 1.29s | 1 | 512 MB |
| Vercel | 1.64s | 2 | 4 GB |
| Daytona | 1.84s | 1 | 1 GB |
| E2B | 3.02s | 2 | 512 MB |

### Key Findings

- **Tensorlake** is fastest regardless of resource allocation. Its 1 vCPU / 512 MB result (1.29s) matches its 2 vCPU / 4 GB result (1.30s) because SQLite is single-threaded.
- **E2B improved 23%** when given more memory (3.02s → 2.32s), the largest gain from normalization.
- **Vercel** requires installing `pysqlite3-binary` since its Python runtime lacks the native `_sqlite3` module.
- **Daytona** only offers "small" class runners on the free tier. Medium/large returned "No available runners."

## Files

```
benchmark.py          # The SQLite benchmark script (runs inside sandboxes)
run_benchmarks.py     # Orchestrator: creates sandboxes, copies script, runs, collects results
results/
  round1_defaults.json    # Results with provider default configurations
  round2_normalized.json  # Results with normalized 2 vCPU / 4 GB target
```

## Notes

- Vercel's Python runtime does not include the native `_sqlite3` C extension. The runner automatically installs `pysqlite3-binary` as a workaround.
- Daytona's `exec` command strips quotes from arguments, making file transfer non-trivial. The runner uses base64-encoded Python one-liners to copy files.
- E2B's CLI does not support setting CPU/memory at sandbox creation time — you must build a custom template.
- All results are from March 17, 2026. Provider performance may change over time.
