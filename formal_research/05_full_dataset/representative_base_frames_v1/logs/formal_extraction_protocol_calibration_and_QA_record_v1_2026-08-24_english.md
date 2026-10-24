# Formal Extraction Protocol, Calibration, and Technical QA Research Record v1

**Date:** 2026-08-24  
**Document type:** Detailed research record for supervisor review and later paper drafting  
**Current status:** Formal Pass 1 completed; technical QA and root-cause diagnostic documented; no frame modification authorized in this record.

## 1. Problem and purpose

The project required a reproducible representative base-frame extraction procedure across 115 source videos while preserving source-video split independence and avoiding post-hoc selection based on model output or scene appearance. Before formal extraction, the protocol needed explicit technical-validity rules, deterministic replacement logic, planned-versus-actual timestamp logging, and a controlled approach to within-source similarity. The purpose was dataset construction reliability and auditability, not direct improvement of YOLO accuracy.

The protocol separates technical validity from substantive representativeness. Difficult scenes, including Night, low light, glare, shadows, low contrast, motion blur, occlusion, small or distant vehicles, and very-low traffic, are not automatically invalid. No YOLO, vehicle count, tracking output, or model confidence is used to choose representative base frames.

## 2. Frame-validity and near-duplicate protocol design

Before formal extraction, the design established: source existence and duration/seekability pre-checks; systematic timestamp generation; explicit Planned Timestamp, Actual Decoded Timestamp, and Seek / Decode Offset fields; technical validity review; deterministic local replacement for confirmed technical invalidity; and within-source similarity flagging.

The approved local replacement order was −1, +1, −2, +2 seconds, with all attempts recorded. Laplacian variance is a flagging signal only. pHash is diagnostic similarity information; it is not a sufficient automatic duplicate classifier. Main Test uses the same frozen technical rules but remains protected from model-dependent selection and threshold tuning.

## 3. Calibration v1 design and chronology

Calibration v1 used 10 Train/Validation source videos drawn from five intersections: exactly two sources per intersection, one Train and one Validation source per intersection, with Train/Validation totals of 5/5 and Main Test total of 0. The subset represented Day, Dawn/Dusk, and Night; Low, Medium, and High traffic; and short, partial, and long recordings. The 10 sources were not a statistically optimal sample size. They were a small, structured, diversity-oriented subset for protocol calibration.

Each source used four distributed temporal slots. Four slots provide basic within-video temporal spread, avoid a one-timestamp-only check, keep calibration small, and keep calibration separate from the formal duration-aware 5/8/10/12 extraction. This produced 10 × 4 = 40 planned observations. The four-slot design was not presented as statistically optimal.

## 4. Corrected Calibration v1 result

The final corrected result was 40 planned slots, 40 decoded, and 0 genuine decode failures. Naturally occurring very-low-traffic frames were retained. No confirmed technical black, white, or near-uniform frame was recorded. Laplacian variance was recorded as a flagging metric; no automatic blur threshold was approved.

Calibration v1 initially appeared to contain three extraction failures. The cause was later traced to an incorrect duration entered in the temporary calibration source table. The formal inventory and original source duration were correct. The affected source was `Bellevue_116th_NE12th__2017-09-11_15-08-36.mp4`; its corrected verified duration was 82.068 seconds, approximately 1:22. After recalculating the four temporal slots from 82.068 seconds, all 4/4 corrected slots decoded successfully. This is not a formal inventory failure, container-duration inconsistency, or genuine three-frame extraction failure.

## 5. Laplacian finding

Laplacian variance was evaluated as a blur/sharpness signal. The observed ranges overlapped across lighting conditions. Legitimate Night and low-light frames can have lower sharpness signals without being technically unusable. Therefore no automatic rejection threshold was approved. Final interpretation: Laplacian variance is a technical-review flag only; a difficult scene is not equivalent to an invalid frame.

## 6. Focused Calibration v2 and pHash evidence

Focused Calibration v2 addressed two open questions from Calibration v1: the apparent failure cause and the usefulness and threshold behavior of pHash. It reviewed 21 Train/Validation pairs: 10 confirmed near-duplicates, 10 meaningful traffic-change pairs, and 1 clearly different pair. Confirmed near-duplicates had pHash distances 0–4 with median 1; meaningful traffic-change pairs had distances 0–6 with median 4; the clearly different example had distance 8.

The ranges overlapped substantially. Fixed-camera traffic videos have stable background structure, while vehicle configuration and traffic state may change. Consequently pHash cannot be used as an automatic duplicate classifier, deletion criterion, or replacement criterion.

## 7. 115-video Duration Audit

The completed audit expected 115 videos, found 115 accessible videos, 0 missing videos, and 0 duplicate filenames. Formal source durations were technically audited. The formal inventory did not contain a systematic duration error. The historical 12:11 value was a temporary Calibration v1 source-table metadata assignment, not an error in the formal source inventory.

The duration-aware projection remained Train 930, Validation 197, Main Test 184, total 1,311.

## 8. Formal Representative Base-frame Extraction — Pass 1

Formal Pass 1 used the frozen Formal Source-video Split v1, verified durations, the frozen duration-aware 5/8/10/12 quotas, and 5%–95% normalized systematic sampling. No YOLO-dependent frame selection was used. The result was 1,311 planned, 1,311 extracted, 0 unresolved, and 0 replacements: Train 930, Validation 197, Main Test 184.

## 9. Full-pass pHash finding and QA refinement

Within-source pHash screening compared all 6,962 candidate pairs. A pHash distance of 0–4 flagged 3,234 pairs. These flags do not mean 3,234 true duplicates. Combined with Focused Calibration v2, the result indicates low specificity for mandatory pair-by-pair human review in fixed-camera traffic data.

The evidence-based QA refinement is documented as a proposed amendment, not a silent overwrite of Protocol v1: pHash remains diagnostic only; systematic frame membership should not change solely because of pHash distance; objective exact-duplicate audit and confirmed technical-invalidity review remain valid controls. The amendment remains pending human approval.

## 10. Exact Duplicate Audit

All 1,311 extracted images were checked using SHA-256. There were 0 duplicate hash groups and 0 exact duplicate images. Because no SHA-256 groups existed, no additional decoded-pixel equality comparison was necessary. No file was deleted or modified.

## 11. Large-offset technical QA

Fifteen large-offset cases were identified: Train 6, Validation 2, Main Test 7. All signed offsets were negative, ranging approximately from −1.315 to −31.950 seconds. Compact contact-sheet review showed no obvious black frame, white frame, severe corruption, or clearly unusable technical frame. The primary issue was planned-versus-actual timestamp discrepancy.

## 12. Large-offset root-cause diagnostic

All 15/15 cases reproduced deterministically. Three repeated Pass 1-style random seeks returned the same actual timestamp for each case, always earlier than planned. The method was `AVAssetImageGenerator.copyCGImage` with ±0.5 second tolerance.

Sequential diagnostic decoding used AVAssetReader from a safe position 60 seconds before planned, or from 0 seconds near the beginning, through planned + 2 seconds. Two cases had a sequentially decoded frame sufficiently close to planned; 13 did not. The diagnostic classification was:

- A — extraction / seek behaviour issue: 2 cases. Random seek landed substantially early, while controlled sequential decoding reached a frame close to planned.
- B — source-level timestamp / indexing irregularity: 13 cases. Sequential decoding also did not identify a sufficiently close PTS near planned.
- C — other / unresolved: 0 cases.

“Source-level timestamp/indexing irregularity” is a current diagnostic classification, not proof of container damage. Possible mechanisms include container indexing behavior, edit-list or timebase structure, timestamp discontinuity, or another source-level timestamp structure. Current evidence is insufficient to prove any one mechanism.

## 13. Current interpretation and decision

Two A-class cases have technical evidence supporting possible controlled sequential re-extraction in a future, separately authorized operation. The 13 B-class cases do not currently establish that a closer valid PTS exists near planned. Forcing replacement in B-class cases would introduce unsupported post-hoc selection. No frames were modified during the diagnostic work.

The current technical QA work is considered sufficient for methodological decision-making. Deep container-format debugging is not continued unless a future issue materially affects dataset validity. The project focus remains vehicle detection, tracking, traffic-state analysis, and a lightweight digital-twin-style representation, not video-container timestamp engineering.

## 14. Limitations and current status

The record does not prove the underlying cause of the 13 B-class cases and does not approve an automatic offset threshold. After finalisation, the two A-class frames were corrected under controlled sequential decoding and their originals were archived; the 13 B-class frames were not changed. Formal Split v1, the 5/8/10/12 rule, planned timestamps, and Main Test membership remain unchanged.

## 15. Final Extraction QA Disposition

The pHash QA Strategy Amendment v1 was approved after the Focused Calibration v2 overlap evidence, the 3,234 / 6,962 Formal Pass 1 pHash result, and the 0-group Exact Duplicate Audit. pHash is now formally diagnostic similarity information only; mandatory pair-by-pair review of all 3,234 flags is discontinued. The historical Protocol v1 decision remains preserved.

The two A-class cases were corrected using controlled sequential decoding. For each, the selected frame was the valid decoded frame whose presentation timestamp was nearest to the original frozen Planned Timestamp. Original frames were archived before correction. The 13 B-class cases were retained unchanged with the documented limitation “source-level timestamp/indexing irregularity — diagnostic classification”; no forced replacement was made.

The final exact-duplicate recheck covered 1,311 images and found 0 duplicate groups and 0 duplicate images. The final split count remains Train 930, Validation 197, Main Test 184. Existing pHash entries for the two corrected source videos are marked as pre-QA-correction diagnostic records; no pHash review was reopened and no membership decision was based on pHash.

Formal Extraction QA is closed. Remaining limitations are the unresolved underlying mechanism for the 13 B-class timestamp/indexing cases and the pending human visual review of their final disposition records. The next task is Representative Base Dataset Coverage Audit. No further calibration, threshold experiment, seek optimization, container debugging, annotation, YOLO, training, or tracking is part of this QA branch.
