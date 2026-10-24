# Local release verification

Latest report: `release_checks/run_003/report.json`. Media checks: `release_checks/run_003/media_audit.json`. Distribution tests: `release_checks/run_002/distribution_audit.json`. Original/copy identity: `release_checks/run_002/source_identity.json`.

The latest verification ran against a temporary copy containing only files selected by Git. The two optional checkpoints were absent. `run_001` remains the earlier full-folder baseline, which included the local model files.

| Check | Result | Scope |
|---|---|---|
| Copied-file hashes | Passed for every manifest entry | Source/copy byte identity |
| Archived Python syntax | Passed | Parsing only; one historical invalid-escape SyntaxWarning was non-fatal |
| Main Test table reaggregation | Passed: 796 pairs, MAE 0.2286432161, bias -0.0301507538 | No image/box reprocessing |
| Empty/nonempty and camera breakdowns | Passed against saved summaries | 344 empty and 452 nonempty pairs |
| Frozen-rule boundary checks | 19 passed | Original test in a temporary copy |
| Stricter-display boundary checks | 11 passed | Original test in a temporary copy |
| 20/30/60 s persistence analysis | Passed: 66 region-threshold rows | Original analysis rerun; numeric rows/totals compared |
| Supplementary production rule recalculation | Passed: 6,342 region-windows | Retained count/motion observations, original calibration/classification/display functions |
| Retained evidence unchanged after checks | Passed | All manifest hashes rechecked |

Supplementary state counts: Unknown 3,798; no persistent hint 2,113; high-count hint 379; joint hint 52. Unknown windows with the persistent high-count flag: 41; Unknown windows whose current count level is high: 230.

Not run: training, new model inference/evaluation, original image/label SHA and box assignment audit, raw video processing, full historical provenance verification, Unity build or playback, online-link verification. Historical prototype reports included in the package are not new tests of this release.

The checks use only package contents. Original scripts with writes run in a temporary copy; the new helper refuses to write reports under the archived project directories. Use a fresh output directory for each new verification run.

## Distribution checks

- All 1,535 required research files were included, including 12 extraction records previously affected by the ignore rule.
- Both checkpoint binaries were excluded from the Git payload. Video-free verification passed without them. Strict model-required mode rejected missing models, and a deliberately corrupt present checkpoint was rejected. A separate temporary copy with both real models passed strict verification.
- The two Unity case-image folders and their original folder metadata were retained. The three missing case images per project remain external requirements, documented in `UNITY_SETUP.md`; no Unity build was performed.
- The local configuration helper generated detector configurations and relocated the Main Test manifest without changing expected content hashes or original training parameters. It correctly reported that none of the 368 required Main Test image/label files was present. Reusing an existing configuration output folder was rejected. Configuration generation did not start training or inference.
- All 1,537 original research files/checkpoints and their release copies still matched their recorded source hashes after the checks.

The Git file count in `distribution_audit.json` describes the tested snapshot before these final reports and documentation were added. `SHA256SUMS.txt` records the final repository file set, excluding itself. Checkpoint hashes are listed separately in `MODEL_MANIFEST.json`.

## README preview update

`run_003` repeated video-free verification on a Git-selected copy after adding the unchanged thesis interface screenshot. README/media-document links resolved, the image was included, both models remained external, and all 1,537 retained source/checkpoint hashes still matched. Both original Unity projects retained the three case images unchanged; those files remain excluded from the release. No Unity build or upload was performed.
