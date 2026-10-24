# Final thesis detector: YOLO11s

## Identity and download

This is the selected, trained YOLO11s vehicle detector used by the thesis, not the generic pretrained YOLO11s initialization. Its bytes are unchanged from `best.pt` in the original `yolo11s_formal_a100_v1` training run. The hash matches the frozen Main Test evaluation configuration and preflight record.

- Release: [thesis-v1.0](https://github.com/zokiyyy/traffic-observations-thesis/releases/tag/thesis-v1.0)
- Model attachment: [`yolo11s_thesis_best.pt`](https://github.com/zokiyyy/traffic-observations-thesis/releases/download/thesis-v1.0/yolo11s_thesis_best.pt)
- Checksum attachment: [`yolo11s_thesis_best.pt.sha256`](https://github.com/zokiyyy/traffic-observations-thesis/releases/download/thesis-v1.0/yolo11s_thesis_best.pt.sha256)
- Exact size: **19,160,602 bytes** (approximately 18.27 MiB).
- SHA-256: `07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334`
- Classes: `0 car`, `1 motorcycle`, `2 bus`, `3 truck`.
- Selected checkpoint epoch has not been independently extracted during packaging; the last row of the training log must not be taken as the checkpoint epoch.

## Verify and place the model

Download both attachments to the same folder, then run:

```sh
shasum -a 256 -c yolo11s_thesis_best.pt.sha256
```

The check should report `yolo11s_thesis_best.pt: OK`. Place the model in the cloned repository at this path, renaming the downloaded file to `best.pt`:

```text
formal_research/traffic_yolo_system/outputs/training/yolo11s_formal_a100_v1/weights/best.pt
```

Alternatively, set `main_test_model` to the downloaded file in your local configuration; see [CONFIGURATION.md](https://github.com/zokiyyy/traffic-observations-thesis/blob/thesis-v1.0/CONFIGURATION.md). The path-configuration helper preserves the expected model hash.

`tools/verify_release.py` checks this model's hash when it is present. Its default checks also work without model files. The optional `--require-models` mode requires **both** the YOLO11s and YOLO11n files listed in `MODEL_MANIFEST.json`; only YOLO11s is published in this release.

## Experimental settings and scope

The saved Main Test configuration records Ultralytics 8.4.115, image size 640, confidence 0.001 for detector evaluation and NMS IoU 0.70. The confidence threshold used to retain detections for the thesis regional-count diagnostic is 0.35. These thresholds serve different purposes; the 0.001 evaluation threshold is not a traffic-state rule. Consult the retained run-specific configurations before running other workflows.

This release provides an existing checkpoint, not a newly trained or newly evaluated model. Download integrity is checked separately from predictive performance. The checkpoint detects vehicle boxes/classes; tracking, ROI assignment, 10-second aggregation and state rules are separate pipeline stages. It does not directly predict congestion, road speed or calibrated physical traffic density.

Raw videos, exact annotated Train/Validation/Main Test images and the three Unity evaluation-case images are not included. Their absence and other setup requirements are documented in [DATA_ACCESS.md](https://github.com/zokiyyy/traffic-observations-thesis/blob/thesis-v1.0/DATA_ACCESS.md) and [UNITY_SETUP.md](https://github.com/zokiyyy/traffic-observations-thesis/blob/thesis-v1.0/UNITY_SETUP.md). Full model inference and Unity builds have not been rerun during publication.

The model was trained using the Bellevue traffic dataset and the Ultralytics YOLO workflow. This attachment does not relicense third-party software or source data. See [ACKNOWLEDGEMENTS.md](https://github.com/zokiyyy/traffic-observations-thesis/blob/thesis-v1.0/ACKNOWLEDGEMENTS.md).
