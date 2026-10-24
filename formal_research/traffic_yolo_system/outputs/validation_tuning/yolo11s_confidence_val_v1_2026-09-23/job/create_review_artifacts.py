"""Render saved validation predictions only; no model or test-set access."""
from pathlib import Path
import json
import math
import os

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "review" / ".mplconfig"))
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = ROOT / "review"
OUT.mkdir(exist_ok=True)
IMAGES = Path("/Users/zoki/Desktop/traffic_projects/formal_research/05_full_dataset/formal_yolo_trainval_v1/images/val")
ROWS = [json.loads(x) for x in (ROOT / "results/predictions_and_matches.jsonl").read_text().splitlines()]
SUMMARY = json.loads((ROOT / "results/summary.json").read_text())
LOOKUP = {r["metadata"]["annotation_id"]: r for r in ROWS}
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
BG, INK, MUTED = "#f4f6f9", "#15283c", "#526274"
GT, TP, UNMATCHED = "#5fd8ff", "#4bf293", "#ffad42"
NAME = {"car": "car", "motorcycle": "mcycle", "bus": "bus", "truck": "truck"}


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def total(row, threshold):
    return row["thresholds"][threshold]["class_aware_matching"]["total"]


def draw_overlay(row, threshold=None, crop=None, panel_width=960):
    im = Image.open(IMAGES / row["filename"]).convert("RGB")
    if crop is None:
        crop = (0, 0, im.width, im.height)
    x0, y0, x1, y1 = crop
    scale = panel_width / (x1 - x0)
    im = im.crop(crop).resize((panel_width, round((y1-y0)*scale)), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(im)
    if threshold is None:
        records = row["ground_truth"]
        matched = set()
    else:
        records = row["thresholds"][threshold]["detections"]
        matched = {m["prediction_index"] for m in row["thresholds"][threshold]["class_aware_matching"]["matches"]}
    boxes = []
    for obj in records:
        bx0, by0, bx1, by1 = obj["box_xyxy"]
        if bx1 <= x0 or bx0 >= x1 or by1 <= y0 or by0 >= y1:
            continue
        box = [(bx0-x0)*scale, (by0-y0)*scale, (bx1-x0)*scale, (by1-y0)*scale]
        color = GT if threshold is None else TP if obj["index"] in matched else UNMATCHED
        d.rectangle(box, outline=color, width=3)
        label = NAME[obj["class_name"]]
        if threshold is not None:
            label += f" {obj['confidence']:.2f}"
        boxes.append((box, label, color))
    # Place labels without overlapping previously placed labels where possible.
    occupied = []
    fs = 16 if crop == (0, 0, row["width"], row["height"]) else 20
    f = font(fs, True)
    for box, label, color in sorted(boxes, key=lambda x: x[0][1]):
        w = d.textbbox((0,0), label, font=f)[2] + 8
        h = fs + 6
        lx = max(0, min(im.width-w, box[0]))
        candidates = [box[1]-h, box[3]+2, box[1]+3, box[1]-2*h, box[3]+h+3]
        ly = max(0, min(im.height-h, candidates[0]))
        for cy in candidates:
            cy = max(0, min(im.height-h, cy))
            rect = (lx, cy, lx+w, cy+h)
            if not any(rect[0] < o[2] and rect[2] > o[0] and rect[1] < o[3] and rect[3] > o[1] for o in occupied):
                ly = cy
                break
        d.rectangle((lx, ly, lx+w, ly+h), fill="#102033", outline=color, width=1)
        d.text((lx+4, ly+1), label, fill=color, font=f)
        occupied.append((lx, ly, lx+w, ly+h))
    return im


def triptych(row, slug, title, reason, crop=None):
    panels = [draw_overlay(row, t, crop) for t in (None, "0.25", "0.35")]
    gap, margin, top, bottom = 16, 28, 150, 87
    width = 3*960+2*gap+2*margin
    height = top + panels[0].height + bottom
    im = Image.new("RGB", (width,height), BG)
    d = ImageDraw.Draw(im)
    m = row["metadata"]
    d.text((margin,18), title, font=font(32,True), fill=INK)
    d.text((margin,63), f"{m['annotation_id']}  |  {m['lighting']}  |  {m['intersection']}  |  Validation", font=font(23), fill=MUTED)
    for i, (p,t) in enumerate(zip(panels,(None,"0.25","0.35"))):
        x = margin + i*(960+gap)
        if t is None:
            heading = f"Human annotations  |  {len(row['ground_truth'])} objects in full image"
        else:
            z = total(row,t)
            heading = f"conf {t}  |  TP {z['tp']} / FP {z['fp']} / FN {z['fn']}"
        d.text((x,111), heading, font=font(23,True), fill=INK)
        im.paste(p,(x,top))
    y = top+panels[0].height+14
    d.text((margin,y), "Cyan: human boxes   |   Green: same-class match at IoU >= 0.50   |   Orange: unmatched prediction (not necessarily a nonexistent vehicle)", font=font(21), fill=INK)
    footer = "imgsz 640; NMS IoU 0.70; fixed-confidence diagnostic, not mAP. Original images and annotations unchanged."
    if crop:
        footer = f"CROP {list(crop)} in original pixels; headers report FULL-IMAGE counts. Cropped view is enlarged."
    d.text((margin,y+34), footer, font=font(21), fill=MUTED)
    path = OUT / f"{slug}.jpg"
    im.save(path,quality=95,subsampling=0)
    return {"annotation_id":m["annotation_id"],"lighting":m["lighting"],"filename":row["filename"],"selection_reason":reason,"path":str(path),"crop_xyxy":crop,"metrics_025":total(row,"0.25"),"metrics_035":total(row,"0.35")}


def chart():
    ts = ["0.20","0.25","0.35"]
    metrics = ["precision","recall","f1"]
    colors = ["#247aa5","#e59231","#438650"]
    fig, ax = plt.subplots(figsize=(11.8,6.7), dpi=180)
    fig.patch.set_facecolor("#f4f6f9")
    ax.set_facecolor("#f4f6f9")
    x = np.arange(3)
    for j,(k,c) in enumerate(zip(metrics,colors)):
        vals = [SUMMARY["thresholds"][t]["all"][k] for t in ts]
        bars = ax.bar(x+(j-1)*.24,vals,width=.22,color=c,label=k.capitalize())
        ax.bar_label(bars,labels=[f"{v:.3f}" for v in vals],padding=5,fontsize=12)
    ax.set_ylim(0,1.0)
    ax.set_xticks(x, [f"conf = {t}" for t in ts],fontsize=12)
    ax.set_ylabel("Micro score (0–1)",fontsize=12)
    ax.grid(axis="y",alpha=.18)
    ax.set_axisbelow(True)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.legend(loc="upper center",bbox_to_anchor=(.5,1.14),ncol=3,frameon=False,fontsize=12)
    fig.suptitle("YOLO11s: confidence-threshold comparison",fontsize=20,fontweight="bold",x=.075,ha="left",y=.98)
    fig.text(.075,.918,"197 validation images · 1,736 labeled objects · fixed-confidence micro P/R/F1 · NOT mAP",fontsize=11.2,color="#526274")
    fig.subplots_adjust(left=.075,right=.98,top=.80,bottom=.30)
    strings=[]
    for t in ts:
        z=SUMMARY["thresholds"][t]["all"]
        strings.append(f"conf {t}:  TP {z['tp']:,}   FP {z['fp']:,}   FN {z['fn']:,}")
    fig.text(.075,.09,"\n".join(strings),fontsize=11.5,linespacing=1.55,family="monospace",color="#15283c")
    fig.text(.55,.08,"Same-class, one-to-one matching at IoU ≥ 0.50.\nimgsz 640; NMS IoU 0.70; one trained model.\nCar-dominated aggregate; inspect rare classes separately.",fontsize=10.8,color="#526274",linespacing=1.6)
    path=OUT/"confidence_micro_metrics.png"
    fig.savefig(path,dpi=180)
    plt.close(fig)
    return str(path)


selected = [
    ("F0480","01_maximum_fp_reduction","Largest reduction in unmatched predictions; one match also lost","Maximum FP reduction from 0.25 to 0.35 across 197 validation images (6); TP decreases by 1."),
    ("F0178","02_motorcycle_tradeoff","Higher threshold removes a matched motorcycle","One of two images losing a motorcycle TP from 0.25 to 0.35; selected earliest annotation_id."),
    ("F0794","03_truck_tradeoff","A matched truck is removed; unmatched count is unchanged","Truck TP falls by 1 from 0.25 to 0.35, while FP count stays at 1; Dawn/Dusk contrast."),
    ("F0226","04_night_change","Night example: one unmatched box removed, matches retained","Night image with FP 1 to 0 and unchanged TP 4; illustrates a beneficial change without a simultaneous TP loss."),
    ("F1018","05_reduction_without_tp_loss","Three unmatched boxes removed; all 18 matches retained","Contrasting benefit: FP 3 to 0 with TP 18 retained at 0.35."),
]
artifacts=[]
for annotation_id,slug,title,reason in selected:
    artifacts.append(triptych(LOOKUP[annotation_id],slug,title,reason))
row=LOOKUP["F0178"]
g=next(g for g in row["ground_truth"] if g["class_name"]=="motorcycle")
x0,y0,x1,y1=g["box_xyxy"]
cx,cy=(x0+x1)/2,(y0+y1)/2
crop=(max(0,int(cx-155)),max(0,int(cy-115)),min(row["width"],int(cx+155)),min(row["height"],int(cy+115)))
artifacts.append(triptych(row,"06_motorcycle_crop","Motorcycle detail: same image region, enlarged","Detail of F0178 motorcycle removed at 0.35; only crop changes, no predictions re-run.",crop))
index={"scope":"Validation only; rendered from saved results; no inference or test-set access","chart":chart(),"matching":"fixed-confidence class-aware IoU >= 0.5, one-to-one; not mAP","artifacts":artifacts}
(OUT/"index.json").write_text(json.dumps(index,indent=2)+"\n")
print(json.dumps(index,indent=2))
