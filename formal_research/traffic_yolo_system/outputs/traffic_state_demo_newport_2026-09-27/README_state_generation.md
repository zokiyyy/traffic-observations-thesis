# Newport one-hour offline traffic-state demonstration

Derived from 107,932 existing frames into 361 windows and 1,444 region-window records. No model inference or new manual annotation was performed.

## Interpretation and scope

- Offline 10-second summaries use all observations within each window; they are not instantaneous or causal real-time decisions.
- Count levels compare the same road region within this hour only; actual road density and capacity are not calibrated.
- Image motion uses bottom-center displacement over about one second of continuously observed tracks, measured in pixels/second, not km/h.
- Relatively low image motion means at or below the 25th percentile of valid motion windows in this region and hour; it does not mean stopped or nearly stationary. Pixels/second cannot be compared directly across regions.
- Persistent high count and relatively low image motion may include normal signal waiting; this is not a congestion conclusion validated against ground truth.
- Track fragmentation, box jitter, occlusion and perspective affect these proxies. Insufficient evidence remains unknown and is not replaced by zero.
- Thresholds are calibrated for development on this same Validation hour; no new manual annotations or Main Test data are used.
- The final window shorter than 10 seconds retains count data but is not classified, used for calibration, or included in persistence durations.
- No persistent signal does not imply free flow; no observed vehicles does not prove that the road is empty.

## Computation

Count means retain the original `windows.jsonl` values and are independently checked by integrating count times frame duration using integer PTS ticks. Frame holding intervals are split exactly at window boundaries.

Image motion compares the current bottom-center point with the most recent continuously observed point 1.0-1.2 seconds earlier. Missing observations, invalid frames, nonconsecutive frame indices and region changes reset the history. Rates are averaged within each window, region and track using valid motion duration, then combined as a median weighted by each track's valid motion duration. Missing evidence is not interpreted as stationary traffic.

Each region's count thresholds are the 33rd and 67th percentiles of complete valid windows, using linear interpolation. Zero counts are separate; indistinguishable quantiles leave nonempty windows unknown. The relatively low image-motion threshold is the 25th percentile of at least 30 valid motion windows in the same region and hour. It does not mean stopped or nearly stationary: the junction-center threshold is approximately 85.27 pixels/second. Motion is available only for complete 10-second windows with at least 50% coverage and 5 vehicle-seconds of motion evidence.

Persistent high count requires 30 continuous seconds of relatively high count. The combined signal requires both high count and relatively low image motion for 30 continuous seconds and takes precedence. Broken continuity or missing required evidence resets the corresponding duration. These are explicit development rules; they do not establish traffic-state accuracy.

## Generation and verification

Derivation took 12.248 seconds. Region-window counts by state (not numbers of separate events): `{'no_persistent_signal': 1020, 'motion_insufficient': 230, 'high_count_persistent': 155, 'high_count_low_motion_persistent': 23, 'no_observed_vehicles': 12, 'insufficient_window': 4}`.

The original detailed records are streamed and SHA256-hashed during reading, then matched against completion and render receipts. ROI and original window hashes are also checked. The video is checked against its existing verified delivery receipt and current size; this derivation does not decode the video or recompute its hash. All original input sizes and modification times are unchanged before and after the run.

This report verifies derived data and software boundary tests only. Copying the Unity JSON does not verify the Unity application itself; player and visual acceptance are recorded separately. The final short window is retained, but it need not correspond to a separately seekable frame.

## Deliverables

- `traffic_demo.json`: English application data, also copied to Unity StreamingAssets.
- `traffic_states_10s.csv`: readable region-window table.
- `rules_and_thresholds.json`: development parameters and per-region thresholds.
- `generation_receipt.json` and `software_validation.json`: source hashes, input checks and test evidence.
- `traffic_state_overview.png`: English overview, rendered and visually reviewed separately.

Previously generated Chinese notes and figures are retained for informal understanding; this README and the English outputs are the formal deliverables.
