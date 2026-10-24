# Data access, included inputs and missing dependencies

## Source data

Source: City of Bellevue Traffic Video Dataset, https://github.com/City-of-Bellevue/TrafficVideoDataset . The project used the 115 recordings available to the study; this is not a claim about the complete released file count. The dataset repository and README were rechecked on 2026-10-25; research use is stated, but explicit redistribution terms were not located there. See `MEDIA_NOTES.md`.

Included: final source-video split manifest, representative-frame source manifest and extraction logs/rules, annotation guideline, five-camera ROI configurations, selected recording configurations and summary records. The original formal extraction script was not confirmed found; no replacement is presented as the historical script.

## What can be recomputed without external data

- Main Test MAE/bias and empty/nonempty/camera breakdowns from `roi_image_errors.csv`.
- Supplementary thresholds/states from saved region-window count/motion observations.
- Persistence-duration results from saved initial region-window states.
- Selected software boundary checks.

These are not fresh image inference, box-to-ROI assignment audits or real-traffic validation.

## External inputs required for other workflows

| Workflow | Missing/setup-dependent material |
|---|---|
| Training / detection evaluation | Exact formal Train/Validation/Main Test images and human-corrected labels; dataset paths; compatible recorded environment and hardware; appropriate checkpoint |
| Full original Main Test count audit | Exact images and labels matching manifest hashes, saved prediction boxes, upstream manifests and geometry/ROI snapshots; path mappings |
| Full video pipeline | Exact original videos, decoder/tool setup, matching model, camera configs, optional source-time/repair metadata; some large raw logs are omitted |
| Re-running full historical provenance verifiers | Referenced large logs and inputs, and original-path resolution; the packaging helper does not claim to run these verifiers |
| Unity desktop/web build and replay | External videos; restore excluded evaluation-case images if enabling that panel; confirm source catalogue/media paths or hosted URL access; Unity 6000.6.3f1 and project dependencies |

Unity case images `F0178.jpg`, `F0794.jpg`, `F1018.jpg` and their paired .meta files are excluded where present. Their original folder identity is retained with `.gitkeep`. The evaluation-panel build entry points require all three images; see `UNITY_SETUP.md`. A successful source copy does not establish a working build. Other media exclusions are enumerated in `EXCLUDED_FILES.csv`.

Raw videos, extracted dataset images and original human label files are not provided by this package. One unchanged thesis interface screenshot is included for README illustration; it is not a substitute for the dataset or Unity case images. No redistribution permission is inferred. Aggregate counts, predicted boxes, metadata and selected result records are included for local review; decide their publication scope before uploading.

The final YOLO11s checkpoint is provided as a separate Release attachment; see MODEL_CARD.md and MODEL_MANIFEST.json. The YOLO11n comparison checkpoint is not published. No claim is made that an original dataset URL alone provides the exact annotated split or that every omitted file is downloadable.

Use `CONFIGURATION.md` for machine-specific detector paths. Required repository files are separate from optional model attachments; base checks do not need weights.
