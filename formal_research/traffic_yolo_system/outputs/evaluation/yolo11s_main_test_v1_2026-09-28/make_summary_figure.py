"""Draw the held-out detector result from saved metrics; no inference."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
result = json.loads((ROOT / 'run/metrics.json').read_text())
rows = result['per_class']
labels = ['All classes\nmacro mean'] + [f"{r['class_name']}\n{r['ground_truth_objects']:,} GT objects" for r in rows]
ap50 = [result['overall']['map50']] + [r['ap50'] for r in rows]
ap = [result['overall']['map50_95']] + [r['ap50_95'] for r in rows]
fig, ax = plt.subplots(figsize=(10.8, 6.3), constrained_layout=True)
x = np.arange(len(labels)); width = .34
for values, offset, name, color in [(ap50, -width/2, 'AP at IoU 0.50', '#2175a5'), (ap, width/2, 'AP averaged over IoU 0.50–0.95', '#d07828')]:
    bars = ax.bar(x+offset, values, width, label=name, color=color)
    ax.bar_label(bars, labels=[f'{v:.3f}' for v in values], padding=4, fontsize=10)
ax.set_ylim(0, 1.12)
ax.set_yticks(np.arange(0, 1.01, .2))
ax.set_ylabel('Average precision (0–1)')
ax.set_xticks(x, labels)
ax.set_xlabel('Vehicle class and ground-truth support')
ax.set_title('YOLO11s · Held-out Main Test\n184 images · 2,052 vehicle annotations · 17 source videos', loc='left', fontsize=15, pad=18)
ax.legend(loc='upper left', frameon=False)
ax.spines[['top','right']].set_visible(False)
ax.grid(axis='y', alpha=.18); ax.set_axisbelow(True)
fig.text(.01, -.025, 'Frozen detector, CPU FP32 · Source-video split, not unseen intersections. Motorcycle (n=4) and bus (n=13) estimates have limited support.', fontsize=10)
fig.savefig(ROOT / 'main_test_summary.png', dpi=180, bbox_inches='tight')
plt.close(fig)
