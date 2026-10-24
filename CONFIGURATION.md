# Configure paths without editing historical evidence

The archived `formal_research/.../configs` and manifests retain their original contents and SHA-256 values. They document the experiments; do not run their original absolute paths unchanged on a different machine.

## Video-free result checks

No path configuration or external inputs are needed:

```sh
python3 tools/verify_release.py --output release_checks/my_check_001
```

## Detector training / Main Test evaluation

1. Copy `config/local_paths.example.json` to `config/local_paths.json`.
2. Edit the seven settings. Relative paths resolve from this repository root; absolute paths are also accepted.
3. Generate a new local configuration folder:

```sh
python3 tools/configure_detection.py --paths config/local_paths.json --output local_configs/run_001
```

Generated files and personal paths are ignored by Git. This command **does not train or evaluate**. It generates path-adapted dataset YAML, two training YAML files and a Main Test evaluation config/manifest. Original hyperparameters, class definitions and expected image/label/model hashes remain unchanged. The Main Test manifest maps the exact saved basenames to `main_test_root/images/test` and `main_test_root/labels/test`; it does not reconstruct annotations.

Read `configuration_report.json` and `NEXT_STEPS.txt` in the new folder. Missing data are reported honestly; generating configuration is not successful inference or proof that the environment works. The YOLO11n training template retains MPS, and YOLO11s retains CUDA device 0. No device fallback is introduced. Pretrained **initial** weights for training are different from the fine-tuned thesis **best.pt** evaluation checkpoint.

The existing evaluation script supports `--check-only` to check inputs/device without inference. Remove that option only when intentionally starting a new evaluation, with outputs in a new `rerun_outputs` folder. No Main Test rerun occurred during packaging.

## Video and state archives

The standalone release check is portable and uses retained aggregate observations. Historical video, full provenance and image/box audit entry points still require their documented external inputs and path relationships; they are not all adapted or claimed runnable. Keep their configuration/hash records unchanged. A full reprocessing run requires a separate configured working copy, not a replacement of the retained results.

## Unity

See `UNITY_SETUP.md`. Project catalogues, input hashes and source-data hashes are coupled. Do not replace every string containing a username or URL: a changed catalogue/data JSON needs a distinct working version and consistent derived hashes. No automated Unity media relocation is claimed by this release.
