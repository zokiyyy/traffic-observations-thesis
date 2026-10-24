# Materials index

All paths below are relative to this package. `release_manifest.json` is the exhaustive copied-file inventory with SHA-256 hashes.

| Material | Path |
|---|---|
| Main processing source | formal_research/traffic_yolo_system/src/ |
| Original configs and tests | formal_research/traffic_yolo_system/configs/ ; tests/ |
| Selected detector training records | formal_research/traffic_yolo_system/outputs/training/yolo11s_formal_a100_v1/ |
| Comparison detector records | formal_research/traffic_yolo_system/outputs/training/yolo11n_formal_baseline_v1/ |
| Main Test predictions and evaluation metadata | formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/ |
| Validation threshold comparison | formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/ |
| Original initial states | formal_research/traffic_yolo_system/outputs/five_intersection_states_2026-09-28_v1/ |
| Stricter supplementary states | formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/ |
| Paired recordings | formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/ |
| Persistence sensitivity | formal_research/traffic_yolo_system/outputs/persistence_sensitivity_2026-09-28/ |
| Regional-count audit and post-hoc groups | formal_research/12_thesis_draft/revision_2026-10-23/comprehensive_revision_work/roi_count_audit/ |
| Desktop Unity source | formal_research/traffic_unity_demo/ |
| Web Unity source | formal_research/traffic_unity_web/ |
| Final source split | formal_research/03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv |
| Extraction rules | formal_research/03_full_dataset_design/frame_extraction_protocol/ |
| Frame source manifest/logs | formal_research/05_full_dataset/representative_base_frames_v1/ |
| Annotation guideline | notes/traffic_vehicle_annotation_guideline_v1.2_english.md |

Video-run snapshots/configurations include the Newport run, initial `r2`, and supplementary v1/v2/v3 directories where they are dependencies or provenance. They are not three additional independent experiments. In particular the supplementary motion derivation points to v3 repair, while other frozen geometry checks refer to v1 snapshots. Do not substitute versions based on folder recency alone.

Release-only helpers: `tools/verify_release.py` (result checks), `tools/configure_detection.py` (new local detector configs), and `config/local_paths.example.json`. These are packaging additions, not original experiment implementations.

## Documentation preview

- `docs/images/prototype-replay.png`: unchanged thesis interface screenshot, used in README only.
- `docs/media_manifest.json` and `MEDIA_NOTES.md`: image provenance and the decision to keep the three Unity case images external.
