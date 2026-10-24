# Dependencies

## Verified release checks

`python3 tools/verify_release.py --output <new-folder>` uses the Python standard library. It was run using the host's bundled Python; the exact version is recorded in RELEASE_STATUS.json. It does not need Torch, Ultralytics, OpenCV or Unity. Syntax checking does not import every archived script.

## Original detection/video workflows

The original `formal_research/traffic_yolo_system/requirements.txt` is retained unchanged. It records Ultralytics 8.4.115, Torch 2.13.0, PyYAML 6.0.3 and Pillow 12.3.0. It is a historical environment record, not a tested cross-platform installation lock. Inspect saved effective-runtime records for each experiment. Additional analysis/video entry points use NumPy/OpenCV or plotting packages; install only after consulting their imports and the recorded environment for the intended stage.

The Mac and server training configurations differ in device selection. Do not claim that all runs used the same platform or silently substitute devices when reproducing performance results.

FFmpeg/FFprobe binaries are not copied. Original configurations and provenance may record different decoder builds/platforms. Match the required version and architecture for the intended run and set its executable path. Decoder installation was not performed or tested during packaging.

## Unity

Both source projects record Unity 6000.6.3f1 (revision 45d8eee7de74). Project-specific package dependencies are in `Packages/manifest.json` and `packages-lock.json`. Open the intended desktop or web project separately. No fresh package restore, build or playback verification was performed for this candidate.

Path-adapted detector configs can be generated with `tools/configure_detection.py` using only the standard library. This does not install dependencies or establish that training/inference works on a new host. See CONFIGURATION.md.
