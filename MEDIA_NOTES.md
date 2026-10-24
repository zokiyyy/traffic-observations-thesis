# Preview image and external case images

## README preview

`docs/images/prototype-replay.png` is an unchanged copy of the thesis replay-interface figure (`fig_4_2_replay_interface.png`, revision 2026-10-19). It illustrates video replay, road-region indicators and the timeline. It shows an initial-recording example using the original output rules, not the supplementary stricter output. It is a static illustration, not a newly verified build or evidence of traffic-state accuracy.

The camera view uses footage from the [City of Bellevue Traffic Video Dataset](https://github.com/City-of-Bellevue/TrafficVideoDataset). The interface and overlays are from this project. The screenshot is included for explanation; this package does not grant rights to the underlying third-party footage. Raw videos and the training/test image collections remain external. Image provenance and SHA-256 are recorded in `docs/media_manifest.json`.

## Unity evaluation cases

The original project contains three reviewed detection-case images:

| Resource | Original source review image | Purpose |
|---|---|---|
| F0178.jpg | `06_motorcycle_crop.jpg` | Motorcycle prediction removed at the higher threshold |
| F0794.jpg | `03_truck_tradeoff.jpg` | Correct truck-class prediction lost while another predicted box remains |
| F1018.jpg | `05_reduction_without_tp_loss.jpg` | Correct matches retained with fewer unmatched predictions |

These are annotated/cropped images derived from the same Bellevue footage, not images authored entirely from scratch. Their source records and hashes were checked locally. They and their paired `.meta` files remain excluded from this release copy.

On 2026-10-25, the dataset repository and its README were checked. The README states that the dataset is available for research purposes and provides download links and a disclaimer. No explicit redistribution licence was located in those materials. This check therefore did not establish permission to republish the three source-derived case files, nor does it establish that redistribution is prohibited. No claim about permission is made from the dataset's public availability alone.

If an appropriate distribution basis is confirmed later, restore the exact case files with their original paired `.meta` files in both Unity projects, then check the affected builds. The README preview does not replace these runtime resources. See `UNITY_SETUP.md` for the dependent build entry points. No Unity build, publication or GitHub upload was performed in this step.
