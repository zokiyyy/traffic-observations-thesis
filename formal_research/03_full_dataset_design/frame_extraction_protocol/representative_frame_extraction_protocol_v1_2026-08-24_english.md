# Representative Frame Extraction Protocol v1

**Date:** 2026-08-24  
**Status:** **Approved and Frozen for Formal Base-frame Extraction**  
**Scope:** Operational protocol only. This finalisation run did not execute formal extraction, create the projected 1,311-image dataset, annotate, run YOLO, run tracking, or modify Formal Source-video Split v1.

## 1. Evidence base and fixed design inputs

- Formal Source-video Split v1 is approved and frozen: Train / Validation / Main Test = **81 / 17 / 17** source videos.
- The unchanged duration-aware quota rule is: under 5 minutes → 5 frames; 5 to under 20 minutes → 8 frames; 20 to under 45 minutes → 10 frames; 45 to 60 minutes → 12 frames.
- The projected base total remains **1,311 images**. It is a projection, not an extracted dataset.
- The 115-video Duration Audit found 115 / 115 accessible files, no missing files, no duplicate filenames, and technically consistent source durations.
- Calibration v1 used 10 Train/Validation sources across five intersections and, after controlled correction, produced **40 / 40 decoded slots with 0 genuine decode failures**.
- Focused Calibration v2 reviewed 21 pairs: 10 confirmed near-duplicates, 10 meaningful traffic-change pairs, and 1 clearly different pair. Their pHash ranges overlap.

## 2. Final operational architecture

```text
Formal Source Video
↓ Confirm frozen Formal Split membership
↓ Read verified technical duration
↓ Assign duration-aware frame quota
↓ Calculate systematic normalized positions and planned timestamps
↓ Decode scheduled frame
↓ Technical validity check
↓ If valid → continue
↓ If technically invalid → deterministic local replacement
↓ Near-duplicate flagging within the same source video
↓ pHash review flag where applicable
↓ Human verification of flagged pairs
↓ If confirmed near-duplicate → deterministic replacement
↓ Record planned/actual timestamps, offsets, and decisions
↓ Final representative base frame
```

### 2.1 Normalized temporal positions

For a source assigned `n` base frames, generate `n` equally spaced normalized positions between 5% and 95% of the verified technical duration:

`p_i = 0.05 + (i - 1) × 0.90 / (n - 1)`, for `i = 1, ..., n`

`Planned Timestamp = Verified Duration × p_i`.

This applies to every duration tier: under 5 minutes → `n=5`; 5 to under 20 minutes → `n=8`; 20 to under 45 minutes → `n=10`; and 45 to 60 minutes → `n=12`. The 5%–95% boundary is a reproducible operational rule, not a statistically optimal range.

## 3. Frame-validity rules

### Invalid only when confirmed

1. True read or decode failure.
2. Confirmed technical blank or near-uniform image.
3. Confirmed severe technical blur causing substantial loss of usable visual information.
4. Confirmed camera-level technical obstruction or severe image corruption.

### Not automatically invalid

Night, low-light, Dawn/Dusk, glare, shadows, difficult contrast, vehicle motion blur, vehicle occlusion, small or distant vehicles, low traffic, and no visible vehicles are not automatic invalidity conditions. Difficult scene does not equal invalid frame. Vehicle count is not a validity criterion. YOLO output must not determine base-frame validity.

## 4. Blur handling

Laplacian variance is a flagging metric only. Calibration did not validate an automatic blur-rejection threshold. An unusually low-sharpness flag requires human technical-validity review. Retain the frame when it is a legitimate difficult traffic scene; reject it only when severe technical blur makes the image genuinely unusable.

## 5. Near-duplicate handling

pHash is the primary algorithm-assisted review metric within a source video. For each source, perform pairwise pHash screening among all selected candidate base frames from that source. For `n` frames, compare all `n(n−1)/2` pairs. Only pHash distance **0–4** pairs are flagged for human near-duplicate review. This is only a priority flag for human review, not an automatic duplicate classification or deletion threshold; no frame is automatically classified or deleted based on pHash.

Human review distinguishes:

- confirmed near-duplicate: almost unchanged traffic configuration and visual information;
- similar fixed-camera background with meaningful traffic change: retain both frames;
- clearly different: retain.

No global cross-video duplicate deletion is permitted.

## 6. Deterministic replacement

Default local search sequence:

`-1 second → +1 second → -2 seconds → +2 seconds`

This is an operational protocol choice, not a statistically proven optimal replacement window. Replacement is permitted only for confirmed technical invalidity or a confirmed near-duplicate requiring replacement. It must not seek more vehicles, more difficult scenes, better-looking frames, or better YOLO detection. If no valid replacement exists in the sequence, log the slot as unresolved / unavailable; do not choose an arbitrary distant timestamp.

## 7. Duration and seekability pre-check

Before timestamp generation for each source:

1. Read the verified technical duration.
2. Confirm that planned timestamps fall within the readable source range.
3. Do not rely on temporary calibration-table values.
4. If a source-level duration discrepancy is detected, stop normal extraction for that source and flag it for investigation.

The rule addresses the temporary Calibration v1 table error. It does not imply a systematic duration problem in the formal 115-video inventory.

## 8. Planned versus actual timestamps

The extraction log must always record Planned Timestamp, Actual Decoded Timestamp, and Seek / Decode Offset = Actual − Planned. A noticeably large offset must be flagged for technical review. No automatic rejection threshold or maximum acceptable offset is approved at this stage. The corrected calibration slot planned at 53.3442 seconds produced an actual sequentially decodable frame at approximately 57.568 seconds.

## 9. Main Test protection

The same rules apply to Train, Validation, and Main Test. Main Test must not receive model-dependent selection, manual better-frame selection, difficulty selection, or YOLO-based replacement. Main Test replacement is permitted only for technical invalidity or confirmed near-duplicate under the same predefined rules. Main Test visual content must not be used for threshold tuning.

## 10. Calibration rationale and evidence

Calibration v1 used 10 source videos as a small, structured, diversity-oriented subset: five intersections; two sources per intersection; one Train and one Validation source per intersection; Day, Dawn/Dusk, and Night; Low, Medium, and High traffic; short, partial, and long recordings; Main Test = 0. Ten sources were not a statistically optimal sample size. The purpose was protocol calibration, not population-level inference or model evaluation.

Four distributed temporal slots per source gave **10 × 4 = 40 planned observations**. Four slots provided basic within-video temporal spread, avoided reliance on one timestamp, kept calibration small, and kept it separate from formal duration-aware extraction. Four slots are not claimed to be statistically optimal.

The corrected Calibration v1 result was 40 / 40 decoded, with no confirmed technical black/white/near-uniform frame; naturally occurring very-low-traffic frames were retained. Focused v2 supports pHash review only because confirmed near-duplicate and meaningful-traffic-change ranges overlap. The apparent Calibration v1 failures were caused by an incorrect duration entered in the temporary calibration source table; the formal inventory and original source duration were correct. The 115-video Duration Audit remains the source-pool duration evidence.

## 11. Freeze and change control

**Protocol v1 is Approved and Frozen for Formal Base-frame Extraction.** Once extraction begins, these rules must not be silently changed. A genuine future methodology change requires documenting the reason and creating Protocol v2 while preserving Protocol v1 history.

The next task is **Formal Representative Base-frame Extraction**. This protocol finalisation did not execute it.
