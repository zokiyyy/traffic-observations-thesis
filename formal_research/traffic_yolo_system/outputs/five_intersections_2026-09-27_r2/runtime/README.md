# Fixed four-source server pipeline

This package processes exactly the four new Validation sources in `selection/selected_sources.json`; Newport is reuse-only and no Newport or Main Test media is opened. The parent controller owns upload, detached launch, status, and download. This runner does not perform SSH or network transfers.

## Invocation and lifecycle

Fixed server root: `/home/ubuntu/traffic_projects/five_intersections_20260927_r2`.

```
/home/ubuntu/venvs/traffic-yolo/bin/python formal_research/traffic_yolo_system/outputs/five_intersections_2026-09-27_r2/runtime/server_pipeline.py --expected-job-sha256 APPROVED_SHA256
```

Add `--validate-only` to verify package scope, dependency hashes and raw frozen configurations without opening source/model media or starting inference. Run from the project root for a readable command; the runner resolves its own fixed root. A launcher must use `start_new_session=True`, stdin `/dev/null`, and a retained log file, then store actual PID/session/start token. The pipeline also records its own PID/session/start token and child ownership every 15 seconds. `flock` is held by both parent and live child so a parent crash cannot permit a duplicate worker. Lock contention returns exit10. Signals preserve incomplete stage evidence. The server runs independently of Mac sleep or chat availability after launch.

The only path-selection CLI option is the reviewed manifest SHA. The manifest path and allowed sources are fixed in code. No arbitrary source/config override is accepted.

## Job manifest

Sibling `job_manifest.json` uses schema `five_intersections_server_job_v1`, job_id `five_intersections_20260927_r2`. Every file path below is relative to the fixed server root (no absolute paths, symlinks or `..`).

```
{
  "schema_version": "five_intersections_server_job_v1",
  "job_id": "five_intersections_20260927_r2",
  "selection": {"path": "formal_research/traffic_yolo_system/outputs/five_intersections_2026-09-27_r2/selection/selected_sources.json", "sha256": "..."},
  "split_manifest": {"path": "...existing frozen split metadata CSV...", "sha256": "..."},
  "inputs": [{"path": "...code/config/metadata file...", "sha256": "...", "roles": ["native", "inference", "qc", "render", "runtime"]}],
  "sources": [{"source_id": "...exact fixed filename...", "camera_id": "...matching camera...", "split": "Validation", "source_sha256": "...", "source_bytes": 123, "full_config": {"path": "...camera full_remote.json...", "sha256": "..."}, "short_start_seconds": 60.0, "short_target_seconds": 60.0}]
}
```

Exactly four sources must appear. Copy source identities/hashes/bytes from the frozen selection. All four short start/duration values are60/60 seconds. NE12th replacement is September10 23:08:29 (night), locally fully decoded with zero error logs; the server must still pass its own unchanged strict native audit. The source SHA is785fc1ed24a6a2aff94601b79474c88a63660a751a586697b6bfd9120fd371be. Its106460 frames cover3600.009766667 seconds; the observed longest original frame duration16.9004 seconds is bounded by the reviewed17.0-second configuration limit. Actual short boundaries are original PTS: ceiling of start, floor of end, end exclusive. The runtime never expands those declared intervals. The prior82-second NE12th daytime source and its real decoder failure stay preserved under the original job. This revision must use a separately approved whole night source, with any original long frame holds preserved.

Roles explicitly bind dependencies to stage receipts. Assign `src/**` to inference/native/render as applicable; `selection_binding.py` to native/qc/render; all config/ROI files to inference/qc/render; native helper to native; QC helper to qc; renderer/smoke helpers to render; runtime files to runtime. Runtime binds each camera, its geometry and applicability evidence explicitly per source; `/config/` entries are excluded from global code bindings so a reviewed later-camera repair cannot invalidate an unrelated completed camera. A downstream helper correction can then be reviewed without invalidating completed inference. Only changed upstream stage inputs make old completion receipts incompatible, which fails closed instead of silently rerunning them.

## Packaged dependencies

- `runtime/server_pipeline.py`, `runtime/short_contacts.py`, reviewed `runtime/job_manifest.json`.
- `validation/selection_binding.py`, `validation/prepare_selected_time_audit.py`, `validation/audit_selected_output.py`.
- `presentation/render_selected_pts.py`, `presentation/smoke_generated_pts.py`.
- Existing `src/full_video/**` plus its imported sibling modules; package the reviewed `src/**` tree. Renderer requires `src/render_video_tracking_pilot.py` and `src/roi_geometry.py`.
- Exact selection, frozen split metadata and four new camera/ROI/containment/full configurations.
- Exactly the four original source files under `videos/original/<camera>/<source_id>`; no115-source media tree.

Read-only existing dependencies outside the package: frozen checkpoint `/home/ubuntu/traffic_projects/runs/training/yolo11s_formal_a100_v1/weights/best.pt` with SHA `07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334`; Python `/home/ubuntu/venvs/traffic-yolo/bin/python`; ffprobe `/usr/bin/ffprobe`; PyAV17 `/home/ubuntu/traffic_projects/full_v5_20260924/tools/pyav17`. No package is installed by this runner.

## Per-source stages and gates

1. Synthetic VFR codec smoke once before any source inference (includes actual encoding).
2. Native source decode/timing audit and source SHA check.
3. Exact original short-segment inference, independent QC with plots, three source-pixel-verified annotated frames/contact sheet.
4. Whole-source inference, independent QC with curves, PTS-preserving annotated video, exact source pixel checks.
5. Input-bound data/summary archives and camera completion receipt. Only then proceed to the next camera.

The short check confirms technical consistency and retains visual examples. It is not a new accuracy study or manual traffic-count validation. Plots describe current visible vehicles, not cumulative flow or congestion. Long source frame holds are preserved and must be disclosed when interpreting duration-weighted statistics.

## Recovery and outputs

Reinvoke the same manifest command after interruption. Completed stages skip only after input-binding and complete output-inventory SHA verification. A downstream failure does not rerun completed inference. Unknown/live process ownership, changed completed files, changed upstream input, non-transient failure or exhausted retry budget stops the job and preserves evidence.

Each incomplete stage permits at most one retry (two attempts with the same input binding). Automatic retries apply to explicit temporary exit75 or killed worker exit-9; a proven-dead parent/child can resume an incomplete stage. Incomplete inference restarts its **whole configured interval with fresh tracker state**; there is no frame checkpoint. A new reviewed downstream code binding can repair downstream failures without changing completed inference. No files are deleted.

Top-level `batch_status.json`, append-only `batch_events.jsonl`, `batch_completion.json` are under the new output directory. Per-camera paths:

- `results/<camera>/camera_completion.json`
- `results/<camera>/delivery/summary.tar.gz` (full independent QC/curves, short bounds, short contact evidence, renderer receipt)
- `results/<camera>/delivery/data.tar.gz` (complete inference attempt, native timing audit)
- `results/<camera>/delivery/annotated.mp4`
- `results/<camera>/delivery/render_receipt.json`
- `results/<camera>/stages/*/completion.json` (immutable hash-bound stage receipts)

Archive receipts verify both their input inventory and final archive SHA; a crash-created unreceipted archive is adopted only after every archive member is independently matched to the current verified inputs. Newport's existing files are never copied, rewritten or reanalysed by this job.
