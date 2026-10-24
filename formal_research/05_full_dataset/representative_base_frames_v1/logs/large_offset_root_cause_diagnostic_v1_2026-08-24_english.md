# Large-offset Root-cause Diagnostic v1

## Scope and protection

This diagnostic inspected the existing 15 large-offset cases only. No formal frame was re-extracted, overwritten, replaced, deleted, or saved as a replacement. Formal Split v1, quotas, planned timestamps, and Main Test membership were unchanged.

## A. Cases reproduced

All 15/15 random-access requests reproduced the original actual timestamp exactly across three repeated requests per case. All 15 returned a timestamp earlier than planned. The method was the Pass 1 method: `AVAssetImageGenerator.copyCGImage` with preferred track transform and ±0.5 second request tolerances.

## B. Random-seek behaviour

Random seek was deterministic in this test (`random_stable=YES` for all cases). The repeated result matched the current Pass 1 actual timestamp for every case; signed offsets ranged from −1.314658s to −31.950111s.

## C. Sequential-decode behaviour

For each case, an AVAssetReader BGRA sequential decode was started from a safe position 60 seconds before planned (or 0 seconds near the start) and run through planned + 2 seconds. The nearest decoded presentation timestamp was recorded without writing an image. A frame was considered “close” only for this diagnostic when the absolute difference was <= `max(0.5s, 2/fps)`; this is not a sampling or rejection rule.

- Sequentially close to planned: 2 cases
- Not close under the diagnostic criterion: 13 cases

## D. Keyframe, codec, and timestamp findings

All cases reported codec `avc1` and nominal FPS values approximately 17.39–29.35. Keyframe attachments were scanned in the same local time windows; the diagnostic recorded keyframe counts and the nearest preceding keyframe in the CSV. Sequential PTS streams were readable; no automatic VFR conclusion or sampling correction was made. The evidence shows that several sources have a timestamp/indexing gap or discontinuity around the planned request, because sequential decoding also fails to produce a close PTS even though it advances through the local window.

## E. Root-cause classification

- A — extraction implementation / seek behaviour issue: 2 cases
- B — source timestamp / indexing anomaly: 13 cases
- C — other / unresolved: 0 cases

The CSV gives the case-level classification and evidence. Classification A is limited to cases where sequential decoding reached a close frame; classification B is used where it did not. No broader cause is inferred.

## F. Whether re-extraction is technically justified

No re-extraction is justified in this diagnostic pass. Two cases provide technical evidence that a sequentially close frame exists, but no replacement was authorized. For the other 13 cases, sequential decoding does not establish a close frame at the planned PTS, so replacement would risk inventing a content-based correction without resolving the source timestamp/index behavior.

## G. Recommended correction method

Keep the existing 1,311 frames unchanged. If a later approved correction is required, use a controlled sequential-decode procedure that records the nearest decoded PTS, source timebase, keyframe context, and original random-seek result before any replacement decision. Do not use traffic appearance to choose a frame.

## H. Remaining uncertainty

This diagnostic does not establish whether the underlying cause is a container index defect, edit-list/timebase discontinuity, or another source-level timestamp structure. It also does not approve any automatic offset threshold or replacement rule. Further container-level inspection would be needed before any correction.
