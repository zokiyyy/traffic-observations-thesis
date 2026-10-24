# Formal Extraction QA Strategy Amendment v1

**Status: APPROVED — Evidence-based QA Strategy Amendment v1**

This amendment was approved after Focused Calibration v2 evidence, Formal Pass 1 pHash evidence, and the Exact Duplicate Audit. It refines post-extraction QA practice and does not overwrite or silently revise the historical Protocol v1 decision.

## Evidence and rationale

Focused Calibration v2 showed overlap between pHash distances associated with confirmed near-duplicates and pHash distances associated with meaningful traffic change. Formal Pass 1 produced 3,234 pHash distance 0–4 flags from 6,962 within-source pairs. Therefore mandatory pair-by-pair pHash review is not sufficiently specific or efficient for fixed-camera traffic video.

## Proposed QA rule

pHash remains diagnostic information only. It must not automatically classify, delete, replace, or invalidate a frame. Systematically sampled frames should remain unless there is confirmed technical invalidity or objective exact duplication, such as deterministic file-hash duplication confirmed by an appropriate equality check.

The approved amendment does not change Formal Split v1, the duration-aware 5/8/10/12 quota rule, the 5%–95% systematic sampling rule, frozen planned timestamps, or Main Test membership. Systematically sampled frames are not removed solely because of pHash distance. Hard technical QA remains focused on confirmed technical invalidity, objective exact duplication, and confirmed extraction/timestamp anomalies requiring controlled correction.
