#!/usr/bin/env python3
"""Generate benchmark_chart.png from results/results.json.

Renders a 2x2 grid: total time for the default, fsync and large modes, plus
default-mode concurrent read throughput. Providers keep a fixed order (default
mode ranking) across all panels.

Usage:
  python generate_chart.py            # -> benchmark_chart.png
  python generate_chart.py --out x.png
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent
RESULTS = ROOT / "results" / "results.json"

LABELS = {
    "tensorlake": "Tensorlake",
    "vercel": "Vercel",
    "e2b": "E2B",
    "modal": "Modal",
    "daytona": "Daytona",
    "tenki": "Tenki",
}
COLORS = {
    "tensorlake": "#2563eb",
    "vercel": "#171717",
    "e2b": "#f59e0b",
    "modal": "#10b981",
    "daytona": "#6366f1",
    "tenki": "#06b6d4",
}
DEFAULT_COLOR = "#94a3b8"

BG = "#0f172a"
PANEL = "#1e293b"
GRID = "#334155"
TEXT = "#f1f5f9"
MUTED = "#94a3b8"
ERR = "#64748b"


def style(ax, title, ylabel):
    ax.set_facecolor(PANEL)
    ax.set_title(title, color=TEXT, fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=11)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def bars(ax, providers, values, errs, fmt):
    x = range(len(providers))
    ax.bar(
        x,
        values,
        yerr=errs if any(errs) else None,
        width=0.65,
        color=[COLORS.get(p, DEFAULT_COLOR) for p in providers],
        error_kw={"ecolor": ERR, "capsize": 5, "elinewidth": 1.5},
    )
    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS.get(p, p) for p in providers])
    top = max(v + e for v, e in zip(values, errs))
    ax.set_ylim(0, top * 1.12)
    for xi, v, e in zip(x, values, errs):
        ax.text(xi, v + e + top * 0.015, fmt(v), ha="center", va="bottom",
                color=TEXT, fontsize=11, fontweight="bold")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "benchmark_chart.png"))
    args = ap.parse_args()

    with open(RESULTS) as f:
        data = json.load(f)
    results = data["results"]
    default = results["default"]
    providers = sorted(
        (p for p in default if p != "description"),
        key=lambda p: default[p]["total_mean"],
    )

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), facecolor=BG)
    cfg = data["config"]
    fig.suptitle(
        f"SQLite Benchmark: Sandbox Providers ({cfg['vcpus']} vCPU / "
        f"{cfg['memory_gb']} GB, {cfg['iterations']} iterations)",
        color=TEXT, fontsize=16, fontweight="bold", y=0.97,
    )
    fig.text(0.5, 0.905, "Lower is better (except concurrent reads: higher is better)",
             ha="center", color=MUTED, fontsize=11)

    panels = [
        ("default", "Default (WAL, 60k rows)", lambda v: f"{v:.2f}s"),
        ("fsync", "Fsync (sync=FULL, no WAL)", lambda v: f"{v:.2f}s"),
        ("large", "Large (250k rows, 8MB cache)", lambda v: f"{v:.1f}s"),
    ]
    for ax, (mode, title, fmt) in zip(axes.flat, panels):
        style(ax, title, "Total time (s)")
        mode_data = results[mode]
        bars(
            ax,
            providers,
            [mode_data[p]["total_mean"] for p in providers],
            [mode_data[p].get("total_stddev", 0) for p in providers],
            fmt,
        )

    ax = axes.flat[3]
    style(ax, "Concurrent Reads (4 threads)", "Queries/sec")
    bars(
        ax,
        providers,
        [default[p]["concurrent_reads_qps"] for p in providers],
        [0] * len(providers),
        lambda v: f"{v:.0f}",
    )

    fig.subplots_adjust(left=0.06, right=0.97, top=0.82, bottom=0.06,
                        hspace=0.35, wspace=0.25)
    fig.savefig(args.out, dpi=200, facecolor=BG)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
