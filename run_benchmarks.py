#!/usr/bin/env python3
"""
Run SQLite benchmarks across cloud sandbox providers.

Supports: Tensorlake, Vercel, Daytona, E2B

Usage:
  python run_benchmarks.py                                    # all providers, default mode, 3 iterations
  python run_benchmarks.py tensorlake vercel                  # specific providers
  python run_benchmarks.py --mode fsync                       # disk I/O stress test
  python run_benchmarks.py --mode large                       # large dataset exceeding cache
  python run_benchmarks.py --iterations 5                     # 5 iterations per provider
  python run_benchmarks.py e2b --e2b-template bench-2cpu-4gb  # custom E2B template
"""
import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

BENCHMARK_SCRIPT = Path(__file__).parent / "benchmark.py"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class SandboxInfo:
    provider: str
    sandbox_id: str
    specs: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd, timeout=600, check=True):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {cmd}\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )
    return result.stdout.strip()


def run_unchecked(cmd, timeout=300):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

# --- Tensorlake ---

def tensorlake_create(cpus=2, memory=4096):
    print(f"  Creating Tensorlake sandbox ({cpus} vCPU, {memory} MB)...")
    out = run(
        f"source /tmp/venv/bin/activate && "
        f"tensorlake sbx new --cpus {cpus} --memory {memory} --wait"
    )
    sandbox_id = out.strip().split("\n")[-1].strip()
    return SandboxInfo(
        provider="tensorlake",
        sandbox_id=sandbox_id,
        specs={"requested_cpus": cpus, "requested_memory_mb": memory},
    )


def tensorlake_copy(info, local_path, remote_path):
    run(
        f"source /tmp/venv/bin/activate && "
        f"tensorlake sbx cp {local_path} {info.sandbox_id}:{remote_path}"
    )


def tensorlake_exec(info, cmd):
    return run(
        f"source /tmp/venv/bin/activate && "
        f"tensorlake sbx exec {info.sandbox_id} -- {cmd}",
        timeout=600,
    )


def tensorlake_destroy(info):
    pass  # auto-terminates


# --- Vercel ---

def vercel_create(timeout="30m"):
    print("  Creating Vercel sandbox (python3.13, default 2 vCPU / 4 GB)...")
    out = run(f"sandbox create --runtime python3.13 --timeout {timeout}")
    match = re.search(r"(sbx_\S+)", out)
    if not match:
        raise RuntimeError(f"Could not parse Vercel sandbox ID from: {out}")
    sandbox_id = match.group(1)
    print("  Installing pysqlite3-binary on Vercel sandbox...")
    run(f"sandbox exec {sandbox_id} -- pip install pysqlite3-binary", timeout=120)
    return SandboxInfo(
        provider="vercel",
        sandbox_id=sandbox_id,
        specs={"requested_cpus": 2, "requested_memory_mb": 4096, "note": "pysqlite3-binary installed"},
    )


def vercel_copy(info, local_path, remote_path):
    run(f"sandbox cp {local_path} {info.sandbox_id}:{remote_path}")


def vercel_exec(info, cmd):
    return run(f"sandbox exec {info.sandbox_id} -- {cmd}", timeout=600)


def vercel_destroy(info):
    run_unchecked(f"sandbox rm {info.sandbox_id}")


# --- Daytona ---

def daytona_create(name="sqlite-bench", cpus=2, memory_gb=4):
    print(f"  Creating Daytona sandbox ({cpus} vCPU, {memory_gb} GB)...")
    dockerfile = Path(__file__).parent / "Dockerfile.daytona"
    if not dockerfile.exists():
        dockerfile.write_text("FROM python:3.13-slim\n")
    run(
        f"daytona create --name {name} --cpu {cpus} --memory {memory_gb} "
        f"-f {dockerfile}"
    )
    return SandboxInfo(
        provider="daytona",
        sandbox_id=name,
        specs={"requested_cpus": cpus, "requested_memory_gb": memory_gb},
    )


def daytona_copy(info, local_path, remote_path):
    import base64
    with open(local_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    run(
        f'daytona exec {info.sandbox_id} -- '
        f'"python3 -c \\"import base64; '
        f"open('{remote_path}','wb').write(base64.b64decode('{b64}')); "
        f'print(\'done\')\\""'
    )


def daytona_exec(info, cmd):
    return run(f"daytona exec {info.sandbox_id} -- '{cmd}'", timeout=600)


def daytona_destroy(info):
    run_unchecked(f"daytona delete {info.sandbox_id}")


# --- E2B ---

def e2b_create(template="base"):
    print(f"  Creating E2B sandbox (template={template})...")
    out = run(f"e2b sandbox create {template} -d")
    match = re.search(r"ID\s+(\S+)", out)
    if not match:
        raise RuntimeError(f"Could not parse E2B sandbox ID from: {out}")
    sandbox_id = match.group(1)
    return SandboxInfo(
        provider="e2b",
        sandbox_id=sandbox_id,
        specs={"template": template},
    )


def e2b_copy(info, local_path, remote_path):
    import base64
    with open(local_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    run(
        f'e2b sandbox exec {info.sandbox_id} '
        f"\"python3 -c \\\"import base64; "
        f"open('{remote_path}','wb').write(base64.b64decode('{b64}')); "
        f"print('done')\\\"\""
    )


def e2b_exec(info, cmd):
    return run(f'e2b sandbox exec {info.sandbox_id} "{cmd}"', timeout=600)


def e2b_destroy(info):
    run_unchecked(f"e2b sandbox kill {info.sandbox_id}")


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "tensorlake": {
        "create": tensorlake_create,
        "copy": tensorlake_copy,
        "exec": tensorlake_exec,
        "destroy": tensorlake_destroy,
    },
    "vercel": {
        "create": vercel_create,
        "copy": vercel_copy,
        "exec": vercel_exec,
        "destroy": vercel_destroy,
    },
    "daytona": {
        "create": daytona_create,
        "copy": daytona_copy,
        "exec": daytona_exec,
        "destroy": daytona_destroy,
    },
    "e2b": {
        "create": e2b_create,
        "copy": e2b_copy,
        "exec": e2b_exec,
        "destroy": e2b_destroy,
    },
}


# ---------------------------------------------------------------------------
# Spec detection
# ---------------------------------------------------------------------------

def detect_specs(info, exec_fn):
    try:
        nproc = exec_fn(info, "nproc").strip().split("\n")[-1]
        info.specs["actual_cpus"] = int(nproc)
    except Exception:
        info.specs["actual_cpus"] = "unknown"

    try:
        cgroup = exec_fn(info, "cat /sys/fs/cgroup/cpu.max").strip().split("\n")[-1]
        parts = cgroup.split()
        if len(parts) == 2 and parts[0] != "max":
            info.specs["cgroup_cpus"] = round(int(parts[0]) / int(parts[1]), 1)
    except Exception:
        pass

    try:
        meminfo = exec_fn(info, "grep MemTotal /proc/meminfo")
        for line in meminfo.split("\n"):
            if "MemTotal" in line:
                kb = int(re.search(r"(\d+)", line).group(1))
                info.specs["actual_memory_mb"] = round(kb / 1024)
                break
    except Exception:
        info.specs["actual_memory_mb"] = "unknown"

    try:
        pyver = exec_fn(info, "python3 --version").strip().split("\n")[-1]
        info.specs["python_version"] = pyver.replace("Python ", "")
    except Exception:
        info.specs["python_version"] = "unknown"


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(provider_name, provider_fns, mode, iterations, e2b_template="base"):
    print(f"\n{'='*60}")
    print(f"  {provider_name.upper()}")
    print(f"{'='*60}")

    create_fn = provider_fns["create"]
    copy_fn = provider_fns["copy"]
    exec_fn = provider_fns["exec"]
    destroy_fn = provider_fns["destroy"]

    # Measure sandbox creation time
    create_start = time.time()
    if provider_name == "e2b":
        info = create_fn(template=e2b_template)
    else:
        info = create_fn()
    create_time = round(time.time() - create_start, 2)
    print(f"  Sandbox ID: {info.sandbox_id}")
    print(f"  Creation time: {create_time}s")

    try:
        print("  Detecting specs...")
        detect_specs(info, exec_fn)
        print(f"  Specs: {json.dumps(info.specs, indent=4)}")

        print("  Copying benchmark script...")
        copy_fn(info, str(BENCHMARK_SCRIPT), "/tmp/sqlite_benchmark.py")

        bench_cmd = f"python3 /tmp/sqlite_benchmark.py --mode {mode} --iterations {iterations}"
        print(f"  Running: {bench_cmd}")
        start = time.time()
        output = exec_fn(info, bench_cmd)
        wall_time = time.time() - start
        print(output)

        json_match = re.search(r"--- JSON ---\s*\n(.+)", output, re.DOTALL)
        if json_match:
            bench_results = json.loads(json_match.group(1))
        else:
            bench_results = {"error": "Could not parse JSON from output"}

        return {
            "provider": provider_name,
            "sandbox_id": info.sandbox_id,
            "specs": info.specs,
            "sandbox_creation_time": create_time,
            "mode": mode,
            "iterations": iterations,
            "results": bench_results,
            "wall_time": round(wall_time, 2),
        }

    except Exception as e:
        print(f"  ERROR: {e}")
        return {
            "provider": provider_name,
            "sandbox_id": info.sandbox_id,
            "specs": info.specs,
            "sandbox_creation_time": create_time,
            "results": {"error": str(e)},
        }

    finally:
        print(f"  Cleaning up {provider_name} sandbox...")
        try:
            destroy_fn(info)
        except Exception:
            pass


def get_result_value(results, key):
    """Extract a numeric value from results, handling both single-run and multi-iteration formats."""
    val = results.get(key)
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("mean")
    if isinstance(val, (int, float)):
        return val
    return None


def print_comparison(all_results):
    print(f"\n{'='*80}")
    print("  COMPARISON")
    print(f"{'='*80}\n")

    # Specs
    print("Resource Configuration:")
    print(f"  {'Provider':<15} {'vCPUs':>8} {'Memory (MB)':>12} {'Python':>12} {'Create (s)':>12}")
    print(f"  {'-'*15} {'-'*8} {'-'*12} {'-'*12} {'-'*12}")
    for r in all_results:
        cpus = r["specs"].get("cgroup_cpus", r["specs"].get("actual_cpus", "?"))
        mem = r["specs"].get("actual_memory_mb", "?")
        pyver = r["specs"].get("python_version", "?")
        create_t = r.get("sandbox_creation_time", "?")
        print(f"  {r['provider']:<15} {str(cpus):>8} {str(mem):>12} {pyver:>12} {str(create_t):>12}")

    print()

    benchmarks = [
        "sequential_inserts",
        "batch_inserts",
        "select_count",
        "range_queries",
        "like_queries",
        "updates",
        "deletes",
        "transaction_inserts",
        "aggregates",
        "join_query",
        "concurrent_reads_wall",
        "total_time",
    ]

    header = f"  {'Benchmark':<28}"
    for r in all_results:
        header += f" {r['provider']:>14}"
    print(header)
    print(f"  {'-'*28}" + f" {'-'*14}" * len(all_results))

    for bench in benchmarks:
        row = f"  {bench:<28}"
        for r in all_results:
            val = get_result_value(r["results"], bench)
            if val is not None:
                row += f" {val:>13.4f}s"
            else:
                row += f" {'n/a':>14}"
        print(row)

    # Ranking
    ranked = sorted(
        [r for r in all_results if get_result_value(r["results"], "total_time") is not None],
        key=lambda r: get_result_value(r["results"], "total_time"),
    )
    if ranked:
        print(f"\nRanking (fastest to slowest):")
        baseline = get_result_value(ranked[0]["results"], "total_time")
        for i, r in enumerate(ranked, 1):
            t = get_result_value(r["results"], "total_time")
            ratio = t / baseline
            bar = "#" * int(30 * baseline / t)
            print(f"  {i}. {r['provider']:<14} {t:.4f}s  ({ratio:.2f}x)  {bar}")


def main():
    parser = argparse.ArgumentParser(
        description="Run SQLite benchmarks across cloud sandbox providers"
    )
    parser.add_argument(
        "providers",
        nargs="*",
        default=list(PROVIDERS.keys()),
        choices=list(PROVIDERS.keys()),
        help="Providers to benchmark (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=["default", "fsync", "large"],
        default="default",
        help="Benchmark mode (default: default)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Iterations per provider for mean/stddev (default: 3)",
    )
    parser.add_argument(
        "--e2b-template",
        default="base",
        help="E2B template (default: base). Build custom for specific CPU/memory.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file (default: results/<mode>_<timestamp>.json)",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)

    all_results = []
    for provider_name in args.providers:
        if provider_name not in PROVIDERS:
            print(f"Unknown provider: {provider_name}")
            continue
        result = run_benchmark(
            provider_name,
            PROVIDERS[provider_name],
            mode=args.mode,
            iterations=args.iterations,
            e2b_template=args.e2b_template,
        )
        all_results.append(result)

    print_comparison(all_results)

    output_path = args.output or str(
        RESULTS_DIR / f"{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
