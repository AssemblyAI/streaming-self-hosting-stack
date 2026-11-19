"""Draw the performance-by-GPU charts from results.json.

Usage: python make_charts.py [--png-dir DIR]
Needs matplotlib. Writes the SVGs next to this script.
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent

# One fixed colour per GPU, the same in both charts.
COLORS = {"RTX PRO 6000": "#2a78d6", "L40S": "#eb6834", "RTX PRO 4500": "#1baf7a"}
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#ffffff"

plt.rcParams.update(
    {
        "svg.fonttype": "path",  # glyphs as paths, so no font has to be installed
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "text.color": TEXT,
        "axes.labelcolor": TEXT_2,
        "xtick.color": TEXT_2,
        "ytick.color": TEXT_2,
        "svg.hashsalt": "performance-by-gpu",  # stable ids between runs
    }
)


def new_axes(title, subtitle):
    fig, ax = plt.subplots(figsize=(7.6, 4.4), dpi=100)
    fig.patch.set_facecolor(SURFACE)
    fig.patch.set_alpha(1.0)
    ax.set_facecolor(SURFACE)
    fig.subplots_adjust(left=0.1, right=0.83, top=0.78, bottom=0.13)
    fig.text(0.1, 0.94, title, fontsize=14, weight="bold", color=TEXT)
    fig.text(0.1, 0.885, subtitle, fontsize=10.5, color=TEXT_2)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, pad=6)
    return fig, ax


def plot_series(ax, xs, ys, label):
    ax.plot(xs, ys, color=COLORS[label], linewidth=2, solid_joinstyle="round", solid_capstyle="round", label=label)
    ax.scatter(xs, ys, s=36, color=COLORS[label], edgecolors=SURFACE, linewidths=1.5, zorder=3)


def end_label(ax, x, y, text, dy=0):
    ax.annotate(text, (x, y), xytext=(8, dy), textcoords="offset points", va="center", fontsize=10.5, color=TEXT)


def legend(fig, ax, labels):
    handles = [plt.Line2D([], [], color=COLORS[lab], linewidth=2, marker="o", markersize=6) for lab in labels]
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.095, 0.875), ncol=len(labels), frameon=False, fontsize=10.5, handlelength=1.6, columnspacing=1.6)


def streaming_chart(gpus, out):
    fig, ax = new_axes("Streaming: median final latency by GPU", "Every stream sends continuous dense speech in real time")
    labels = []
    for g in gpus:
        if not g["streaming"]:
            continue
        xs = [p["streams"] for p in g["streaming"]]
        ys = [p["p50_ms"] for p in g["streaming"]]
        plot_series(ax, xs, ys, g["label"])
        end_label(ax, xs[-1], ys[-1], g["label"])
        labels.append(g["label"])
    ax.axhline(500, color="#8a8984", linewidth=1, zorder=1)
    ax.annotate("500 ms", (0, 500), xytext=(4, 4), textcoords="offset points", fontsize=9.5, color=TEXT_2, xycoords=("axes fraction", "data"))
    ax.set_xlim(0, 75)
    ax.set_ylim(0, 1800)
    ax.set_yticks(range(0, 1801, 300))
    ax.set_yticklabels([f"{v:,}" for v in range(0, 1801, 300)])
    ax.set_xlabel("Concurrent streams")
    ax.set_ylabel("Median final latency (ms)")
    legend(fig, ax, labels)
    fig.savefig(out, facecolor=SURFACE, metadata={"Date": None})
    return fig


def sync_chart(gpus, out):
    fig, ax = new_axes("Sync: request time by load", "Each request is one minute of dense speech")
    labels = [g["label"] for g in gpus]
    ends = {}
    for g in gpus:
        xs = [p["requests_per_s"] for p in g["sync"]]
        ys = [p["p50_ms"] / 1000 for p in g["sync"]]
        plot_series(ax, xs, ys, g["label"])
        ends[g["label"]] = (xs[-1], ys[-1])
    # Nudge end labels apart where the lines finish close together.
    order = sorted(ends, key=lambda k: ends[k][1])
    placed = []
    for lab in order:
        x, y = ends[lab]
        dy = 0
        if placed and y - placed[-1] < 0.35:
            dy = 6
        placed.append(y)
        end_label(ax, x, y, lab, dy=dy)
    ax.set_xlim(0, 6)
    ax.set_xticks(range(0, 7))
    ax.set_ylim(0, 4)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.set_yticklabels(["0 s", "1 s", "2 s", "3 s", "4 s"])
    ax.set_xlabel("One-minute requests completed per second")
    ax.set_ylabel("Median time per request")
    legend(fig, ax, labels)
    fig.savefig(out, facecolor=SURFACE, metadata={"Date": None})
    return fig


def add_background(svg_path):
    # An explicit opaque rect keeps the chart readable on dark pages.
    text = svg_path.read_text()
    head, sep, rest = text.partition("<metadata>")
    svg_path.write_text(f'{head}<rect width="100%" height="100%" fill="{SURFACE}"/>\n {sep}{rest}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--png-dir", type=Path, help="also write PNG previews here")
    args = parser.parse_args()
    gpus = json.loads((HERE / "results.json").read_text())["gpus"]
    figs = {
        "streaming": streaming_chart(gpus, HERE / "streaming-latency-by-gpu.svg"),
        "sync": sync_chart(gpus, HERE / "sync-latency-by-gpu.svg"),
    }
    for name in ("streaming", "sync"):
        add_background(HERE / f"{name}-latency-by-gpu.svg")
    if args.png_dir:
        for name, fig in figs.items():
            fig.savefig(args.png_dir / f"preview-{name}.png", facecolor=SURFACE, dpi=150)


if __name__ == "__main__":
    main()
