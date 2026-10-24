# Formal Extraction QA Finalisation v1

**Status:** Extraction QA closed

The evidence-based pHash QA Strategy Amendment v1 was approved after Focused Calibration v2, Formal Pass 1 pHash screening, and the Exact Duplicate Audit. pHash remains diagnostic similarity information only; no automatic classification, deletion, replacement, or mandatory review of all 3,234 flagged pairs is performed. Historical Protocol v1 is preserved.

Exactly two A-class large-offset cases were corrected by controlled sequential decoding, selecting the valid decoded frame nearest to the original frozen Planned Timestamp. Original frames were archived in `qa_corrections/pre_correction_A_class_frames/`. Thirteen B-class cases were retained with documented source-level timestamp/indexing limitations; no B-class frame was replaced.

The final 1,311-frame set remains Train 930, Validation 197, Main Test 184. The final SHA-256 audit found 0 duplicate groups and 0 exact duplicate images. Existing pHash records touching the corrected frames are explicitly marked as pre-QA-correction diagnostic records; no pHash review was reopened.

This closes the Extraction QA branch. No further calibration, threshold experiment, seek optimization, container debugging, or additional frame changes are authorized in this branch. Immediate next task: Representative Base Dataset Coverage Audit.
