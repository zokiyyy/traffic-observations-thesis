# Newport Traffic-State Demonstration: Methods and Preliminary Results

**Date:** 27 September 2026  
**Status:** Offline development demonstration using one Validation video; no independent traffic-state accuracy evaluation.

## Purpose and source data

This demonstration converts existing vehicle detections, tracks and region counts into interpretable traffic-state indicators for subsequent Unity visualisation. It reports relative observed vehicle counts and image-motion proxies, with a persistence rule to highlight sustained conditions. These outputs are descriptive development results, not validated congestion classifications.

The source is `Bellevue_150th_Newport__2017-09-11_08-08-31.mp4`, from the Validation split. The existing full-video output contains 107,932 frame records spanning 3,600.015 seconds. Four image regions are analysed: left road, upper road, right road and junction centre. The regions are image polygons rather than surveyed road segments. Processing produces 360 complete, non-overlapping 10-second windows and one final partial window, yielding 1,444 region-window records. No new detector inference or manual annotation was performed, and Main Test data were not accessed.

## Method

**Observed counts.** Each tracked vehicle is assigned using its bounding-box bottom-centre point. Counts describe vehicles observed in a region at a given time; they do not measure unique vehicle flow, queue length or physical density. Each window mean is weighted by frame duration, using the recorded presentation timestamps. Intervals crossing a window boundary are divided between the corresponding windows. Recomputed means are checked against the existing full-video window summaries.

Count thresholds are calibrated separately for each region using the 33rd and 67th percentiles of its 360 complete-window means. Positive counts at or below the first threshold are labelled relatively low; those above the first and at or below the second are relatively medium; those above the second are relatively high. Zero observations have a separate label. Coincident thresholds produce an insufficient-calibration result rather than artificial three-level grading. The legacy field name `density_level` denotes this relative count classification only; no road area, road length or capacity has been calibrated.

**Image motion.** For a continuously observed track within one region, motion is estimated from bottom-centre displacement over approximately one second, with an admissible interval of 1.0–1.2 seconds. Missing observations, invalid frames, region changes and nonconsecutive frame indices reset the history. Within each window, track motion rates are averaged with duration weights; the region proxy is the duration-weighted median of these track averages. Its unit is source-image pixels per second, not kilometres per hour.

A motion estimate is available only for a complete, valid window with at least five vehicle-seconds of eligible motion observations and coverage of at least 50% of observed vehicle-time. Insufficient evidence remains unknown and is not replaced with zero motion. The relatively low-motion threshold is the 25th percentile of eligible window proxies in the same region and hour, requiring at least 30 eligible windows. Perspective, tracking errors, occlusion and bounding-box movement affect this proxy; values are not directly comparable between regions.

**Persistence and timing.** A high-count indicator requires at least 30 seconds of consecutive complete windows above the region's high-count threshold. A joint indicator additionally requires relatively low, available image motion throughout the same minimum duration. Missing or incomplete windows and failures of the relevant condition break its streak. The 30-second duration is a development rule, not an empirically validated congestion criterion. The partial tail retains count data but is excluded from grading, calibration and persistence. Each displayed state uses the entire 10-second window: it is an offline summary, not an instantaneous or causal real-time assessment.

## Preliminary outputs

The following thresholds were obtained from the source hour. Count thresholds are duration-weighted mean vehicle counts; motion thresholds are pixels per second. Values are rounded here; the JSON retains full precision.

| Region | Count P33 | Count P67 | Motion P25 | Eligible motion windows / 360 |
|---|---:|---:|---:|---:|
| Left road | 1.250 | 2.222 | 0.594 | 287 |
| Upper road | 4.162 | 6.256 | 3.560 | 356 |
| Right road | 2.550 | 4.499 | 1.073 | 336 |
| Junction centre | 1.065 | 1.660 | 85.273 | 217 |

The final mutually exclusive state counts in `traffic_demo.json` are:

| Output state | Region-window records |
|---|---:|
| No persistent signal | 1,020 |
| Insufficient motion evidence | 230 |
| Persistent high count | 155 |
| Persistent high count and relatively low image motion | 23 |
| No vehicles observed | 12 |
| Incomplete window | 4 |
| **Total** | **1,444** |

The 155 high-count-only records exclude the 23 joint-indicator records. These are correlated region-window observations, not numbers of independent events or confirmed congestion episodes. Relatively low motion is a within-region rank: even the junction-centre threshold of 85.273 pixels per second must not be interpreted as stationary traffic. Ordinary traffic-signal waiting can produce a joint indicator. Conversely, no persistent signal does not establish free flow, and no observed vehicles does not prove that a road is empty.

## Verification, interpretation and reproducibility

The recorded software validation passed all 18 tests, covering time weighting, window boundaries, missing observations, motion eligibility, calibration and persistence. These tests verify implementation behaviour; they provide no estimate of traffic-state accuracy. The generation receipt records checks of the detailed frame records, region configuration and authoritative window outputs against their provenance, together with unchanged input file statistics. Video identity is inherited from the existing verified delivery record; this state-generation step did not independently decode or revalidate video playback.

Threshold development and state production use the same hour. There is no independent human traffic-state ground truth, held-out state evaluation or demonstrated generalisation across cameras, times or conditions. Accordingly, the outputs support an auditable visual demonstration and descriptive case discussion, not claims of congestion accuracy, physical density or measured road speed. Unity runtime playback, synchronisation and build acceptance are outside the verification reported here.

The reproducible result package comprises `traffic_demo.json` (visualisation data), `traffic_states_10s.csv` (region-window results), `rules_and_thresholds.json` (parameters and calibration), `software_validation.json` (test record) and `generation_receipt.json` (inputs, integrity checks and output totals).
