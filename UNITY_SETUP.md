# Unity source package: setup and missing resources

Two separate source projects are included: `formal_research/traffic_unity_demo` (desktop) and `formal_research/traffic_unity_web` (web). Both record Unity 6000.6.3f1. No build or playback test of this distribution has been performed.

## Evaluation-case images

Both projects retain the folder `Assets/TrafficDemo/Resources/TrafficEvaluationCases/` using `.gitkeep`, with its original folder `.meta`. These original case images and their individual .meta files are deliberately not included:

- F0178.jpg
- F0794.jpg
- F1018.jpg

The detection-case view is incomplete without these inputs. `TrafficDemoEvaluationPanelBuild.BuildWebPanel()` and `BuildMacPanel()` explicitly require all three and will throw an error if they are absent. This is not a self-contained evaluation-panel build. No replacement or blank image is supplied as research evidence.

If the exact original files can be provided through an approved access method, restore each file with its original paired `.meta` to the folder in the selected working project. No download location is claimed here. Other build paths also remain unverified.

The original three images were located and their provenance checked on 2026-10-25, but no explicit redistribution terms were located on the dataset repository/README. They remain external; see `MEDIA_NOTES.md`. The README screenshot is a separate documentation asset and does not satisfy the build requirements above.

## Videos and catalogues

Videos are not included. `Assets/StreamingAssets/five_sources/catalog.json` lists the ten sources. Each entry's `data_path` is relative to StreamingAssets and points to saved analysis data; `video_path` and `video_sha256` identify the required media. The referenced data JSON also contains `source.video_path` and hashes. Desktop local paths or web URLs recorded there are historical, not guaranteed available on another computer.

For a local reconstruction, supply the matching videos and configure a separate working project/import workflow. If changing data JSON or catalogue paths, retain the research snapshots and record the new hashes coherently; do not silently change the original media identity. The import/build tools need their other declared inputs as well.

## Online demonstration

Recorded link:
https://play.unity.com/en/games/9c5f58ce-768e-4850-9f1e-20947121b19f/traffic-observations-10

The page could not be verified by the web retrieval tool during the distribution audit. This does not establish that the link is broken. Availability, playback and correspondence to the packaged source are not confirmed by this release check. Verify these separately before using the link as the final examiner demonstration.
