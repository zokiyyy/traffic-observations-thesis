"""Render the derived offline development states, without accessing video or YOLO."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator


def render(data_path, output_path):
    data = json.loads(Path(data_path).read_text())
    font = FontProperties(family="DejaVu Sans")
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 11,
                         "savefig.facecolor": "white"})
    colors = {"empty": "#E5E9EF", "low": "#D9EBF7", "medium": "#C5E5D8",
              "high": "#F5DDA5", "unknown": "#D7D7DA"}
    # Match the Unity persistent-state palette; other states remain a neutral aggregate.
    state_colors = {"high_count_persistent": "#E5B35B",
                    "high_count_low_motion_persistent": "#DF8F78", "other": "#EDF1F5"}
    fig = plt.figure(figsize=(14, 13.2))
    grid = fig.add_gridspec(8, 1, left=.105, right=.97, top=.725, bottom=.17,
                           hspace=.58, height_ratios=[4, .65] * 4)
    fig.text(.105, .945, "Newport: one-hour traffic-state development demo", fontproperties=font,
             fontsize=22, weight="bold", color="#142B40")
    fig.text(.105, .906, "Offline 10-second summaries: visible counts → within-hour relative levels → persistent signals",
             fontproperties=font, fontsize=12, color="#4B6070")
    count_handles = [Patch(facecolor=colors[k], label=label) for k, label in
               [("empty", "No vehicles observed"), ("low", "Relatively low count"), ("medium", "Medium relative count"),
                ("high", "Relatively high count"), ("unknown", "Unknown / short tail")]]
    measurement_handles = [Line2D([0], [0], color="#203F5B", lw=2, label="10-second mean count"),
                           Line2D([0], [0], color="#526778", linestyle="--", lw=1.1,
                                  label="Count thresholds: P33 / P67")]
    state_handles = [Patch(facecolor=state_colors["high_count_persistent"], label="Persistent high count"),
                     Patch(facecolor=state_colors["high_count_low_motion_persistent"], label="Persistent high count +\nrelatively low image motion"),
                     Patch(facecolor=state_colors["other"], edgecolor="#C5D0D9", linewidth=.6,
                           label="Other / insufficient evidence")]
    legend_font = FontProperties(family="DejaVu Sans", size=10)
    for x, heading, handles in ((.105, "Count level (background)", count_handles),
                                (.40, "Count measurements", measurement_handles),
                                (.68, "Persistent state (categorical)", state_handles)):
        fig.text(x, .867, heading, fontproperties=font, fontsize=10.5,
                 weight="bold", color="#203F5B")
        fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(x - .006, .853),
                   frameon=False, prop=legend_font, handlelength=2.2, labelspacing=.55)
    window_rows = data["windows"]
    end_min = data["source"]["duration_s"] / 60
    for index, zone in enumerate(data["zones"]):
        ax = fig.add_subplot(grid[2 * index])
        band = fig.add_subplot(grid[2 * index + 1], sharex=ax)
        means, centers = [], []
        for window in window_rows:
            value = next(z for z in window["zones"] if z["zone_id"] == zone["id"])
            begin, end = window["start_s"] / 60, window["end_s"] / 60
            ax.axvspan(begin, end, color=colors[value["density_level"]], alpha=.80, lw=0)
            means.append(value["count_mean"])
            centers.append((begin + end) / 2)
            state = value["traffic_state"]
            shade = state_colors.get(state, state_colors["other"])
            band.axvspan(begin, end, color=shade, lw=0)
        ax.plot(centers, means, color="#203F5B", lw=1.5)
        if zone["density_low_upper"] < zone["density_high_lower"]:
            for limit in [zone["density_low_upper"], zone["density_high_lower"]]:
                ax.axhline(limit, color="#526778", linestyle="--", linewidth=.85, alpha=.85)
        ax.set_title(zone["label"], loc="left", fontproperties=font,
                     fontsize=13, color="#203F5B", pad=8)
        ax.set_ylabel("Mean vehicle count\n[vehicles]", fontproperties=font, fontsize=10, labelpad=10)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.set_xlim(0, end_min)
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
        ax.grid(axis="y", color="#A7B9C8", alpha=.45, linewidth=.6)
        ax.grid(axis="x", color="#879AA9", alpha=.20, linewidth=.55)
        for spine in ax.spines.values():
            spine.set_visible(False)
        band.set_ylim(0, 1)
        band.set_yticks([])
        band.set_ylabel("State\n(categorical)", fontproperties=font, fontsize=8.5,
                        rotation=0, labelpad=27, va="center")
        band.set_xticks(range(0, 61, 5))
        band.tick_params(axis="x", length=3, width=.6, color="#62788A",
                         bottom=index == 3, labelbottom=index == 3, labelsize=10)
        for spine in band.spines.values():
            spine.set_visible(False)
        if index == 3:
            band.set_xlabel("Video time [min]", fontproperties=font, labelpad=8)
    fig.text(.105, .098,
             "P33 / P67 are count percentiles within each region and hour, not road-capacity measurements.",
             fontproperties=font, fontsize=10, color="#4B6070")
    fig.text(.105, .076,
             "Relatively low image motion is region-specific, not evidence of stopped vehicles; normal signal waiting may occur.",
             fontproperties=font, fontsize=10, color="#4B6070")
    fig.text(.105, .054,
             "Other groups no signal, insufficient motion/calibration, no observation and partial windows; it does not mean free flow.",
             fontproperties=font, fontsize=10, color="#4B6070")
    fig.text(.105, .032,
             "No persistent signal does not imply free flow. Traffic-state accuracy has not been independently validated.",
             fontproperties=font, fontsize=10, color="#4B6070")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.data, args.output)
