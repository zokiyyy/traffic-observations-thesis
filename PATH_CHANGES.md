# Path and provenance handling

## Changes made

- No copied historical source, configuration, protocol or result file was modified. Required-file and model manifests record identical source/copy SHA-256 values.
- `tools/verify_release.py` is newly added. It resolves its repository root from its own location and reads only packaged inputs. Test/report-writing scripts run in a temporary copy; original reports are not replaced.
- No historical absolute path or protocol hash was globally rewritten, no integrity assertion was removed, and no frozen-engine hash was updated.

## Distribution correction

- Scoped Unity cache/log ignore rules to each Unity project; research extraction logs are retained even on a case-insensitive filesystem.
- Removed model binaries from the required repository-file manifest; retained their original hashes in the separate model manifest. Base checks allow absent attachments, validate present ones, and can require both with `--require-models`.
- Added `config/local_paths.example.json` and `tools/configure_detection.py` to generate separate, ignored machine-specific detection configs. The helper remaps image/label paths without changing content hashes or original annotations; it does not run inference.
- Retained missing Unity case-image directories using `.gitkeep`; no original image or .meta file was fabricated. See `UNITY_SETUP.md` for the exact build limitations.
- Updated distribution docs/checksum inventory; original experimental reports remain unchanged.

## Remaining historical paths

`LOCAL_PATH_INVENTORY.json` lists copied text files containing local `/Users/...` or `/home/...` locations. These are historical metadata and/or operational dependencies, not universal download locations. Some contain the author's local username. These archival path strings are disclosed as historical metadata; new configurable detector paths are kept in Git-ignored files. No blanket redaction of hash-bearing evidence was performed.

Original entry points such as the full Main Test audit still reference image/label manifests and original input locations. Merely substituting a username is insufficient: referenced labels, images, ROI snapshots and upstream manifests must exist. This release does not claim those entry points are portable.

If further portability edits are made, retain original bytes/hashes as evidence, document old/new path mapping and output locations, and separately verify numerical equivalence. Do not update original experiment hashes to mask a packaging change.

## Documentation image addition

Added an unchanged thesis screenshot at `docs/images/prototype-replay.png` and a relative README image link. No archived code/configuration, Unity runtime resource or original research file was changed. See `MEDIA_NOTES.md`.
