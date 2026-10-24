using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Threading;
using UnityEngine;
using UnityEngine.Video;

namespace TrafficDemo
{
    // Explicit opt-in only. A separate component owns the coroutine because source changes
    // correctly cancel all of the production controller's pending loading coroutines.
    public sealed partial class TrafficReplayController
    {
        private VideoPlayer multiVerificationSuppressedSeekPlayer;
        private MultiReplayVerificationReport multiReport;
        private readonly List<MultiReplayCheck> multiChecks = new List<MultiReplayCheck>();
        private readonly List<MultiReplayCapture> multiCaptures = new List<MultiReplayCapture>();
        private string multiDirectory;
        private bool multiFailed;

        private void BeginMultiVerificationIfRequested()
        {
            string[] args = Environment.GetCommandLineArgs();
            bool requested = Array.IndexOf(args, "--verify-five-sources") >= 0 || Array.IndexOf(args, "--verify-ten-sources") >= 0;
#if UNITY_EDITOR
            requested |= catalog != null && UnityEditor.SessionState.GetBool("TrafficDemoVerifyRequested", false);
            if (requested) UnityEditor.SessionState.SetBool("TrafficDemoVerifyRequested", false);
#endif
            if (!requested) return;
            int outputArgument = Array.IndexOf(args, "--verification-dir");
            multiDirectory = outputArgument >= 0 && outputArgument + 1 < args.Length
                ? Path.GetFullPath(args[outputArgument + 1])
                : Path.Combine(Application.persistentDataPath, "five_sources_verification");
            Directory.CreateDirectory(multiDirectory);
            multiReport = new MultiReplayVerificationReport
            {
                status = "running", started_utc = DateTime.UtcNow.ToString("o"),
                unity_version = Application.unityVersion, platform = Application.platform.ToString(),
                verification_kind = "Actual Unity VideoPlayer decoding and production interaction helpers; explicitly injected callback faults are labelled.",
                physical_input_tested = false, synthetic_clock_used = false,
                exact_container_frame_identity_verified = false,
                limitation = "Automated helper calls do not establish physical mouse/keyboard usability. Screenshots are captured for separate visual review. Native player timestamps can be quantized; source PTS mapping is recorded, not an assertion of exact container-frame identity."
            };
            multiReport.inputs_before = MultiInputHashes();
            MultiPersist();
            gameObject.AddComponent<MultiReplayVerificationRunner>().Initialize(this);
        }

        internal VideoPlayer MultiObservedPlayer { get { return video; } }

        internal IEnumerator RunMultiVerification(MultiReplayVerificationRunner runner)
        {
            // Allow Start's production load to finish; every source is explicitly reloaded below.
            yield return null;
            bool ten = catalog != null && catalog.entries != null && catalog.entries.Length == 10;
            MultiRecord("ten_source_catalog", ten, "data_contract", "This run requires five original and five supplementary catalogue entries.", runner);
            if (!ten) { MultiFinish(); yield break; }
            bool grouped = recordingGroups.Count == 5 && recordingGroups.TrueForAll(g => g.sources.Count == 2);
            MultiRecord("camera_recording_groups", grouped, "catalog_driven_navigation",
                "The current catalog forms five camera groups, each with two independently selectable recording times.", runner);
            var savedCatalog = catalog;
            var expandedEntries = new TrafficDemoCatalogEntry[savedCatalog.entries.Length + 1];
            Array.Copy(savedCatalog.entries, expandedEntries, savedCatalog.entries.Length);
            expandedEntries[expandedEntries.Length - 1] = new TrafficDemoCatalogEntry {
                id = "Bellevue_150th_Newport__2017-09-12_09-00-00.mp4", label = "Newport"
            };
            catalog = new TrafficDemoCatalog { schema_version = savedCatalog.schema_version, entries = expandedEntries };
            RebuildRecordingGroups();
            bool thirdFits = recordingGroups.Count == 5 && ActiveRecordingGroup() != null &&
                ActiveRecordingGroup().sources.Count == 3;
            catalog = savedCatalog;
            RebuildRecordingGroups();
            MultiRecord("third_recording_groups_without_ui_change", thirdFits, "catalog_driven_navigation",
                "A third source at the same camera appears in its recording group without another fixed A/B button.", runner);

            for (int source = 0; source < catalog.entries.Length; source++)
            {
                SwitchSource(source);
                MultiRecord("switch_hides_previous_state", !CanShowState && CurrentWindow == null,
                    "production_source_switch", "Immediately after source selection, no old confirmed state can be displayed.", runner);
                yield return MultiWaitReady(runner, 180);
                bool loaded = MultiFrameValid(runner) && sourceIndex == source && !video.isPlaying &&
                    PresentedTime >= 0 && PresentedTime < .2 && CurrentWindow == null;
                MultiRecord("source_initial_decoded_frame", loaded, "real_decoder", "Requires the selected source, a real frameReady event, decoded texture, source PTS near zero, paused playback and no early window result.", runner);
                if (!loaded) continue;

                if (source >= 5)
                    MultiRecord("saved_full_length_boxes_loaded", detectionOverlay != null &&
                        detectionOverlay.FrameCount == data.source.frame_count,
                        "saved_detection_and_video_identity", "The full-length tracked boxes are loaded from a SHA256-verified, video-bound rendering cache; no inference is run in Unity.", runner);

                MultiCheckDataAndGeometry(runner);
                yield return MultiCapture("initial_overview", runner, true);
                if (source == 0)
                {
                    showRecordingPicker = true;
                    yield return MultiCapture("recording_picker", runner, true);
                    showRecordingPicker = false;
                }

                double[] boundaries = { .005, 9.7, 9.995, 10.005, 10.25, 20.25 };
                foreach (double target in boundaries)
                {
                    SeekTo(target, false);
                    bool hiddenWhilePending = !seeking || (!CanShowState && CurrentWindow == null);
                    yield return MultiWaitReady(runner, 40);
                    bool good = hiddenWhilePending && MultiFrameValid(runner) && !video.isPlaying &&
                        Math.Abs(video.time - target) <= .12 && MultiNoFutureWindow();
                    if (target < 9.8) good &= CurrentWindow == null;
                    if (target == 10.25 && CurrentObservingWindow != null && CurrentObservingWindow.evidence_eligible && data.windows[0].evidence_eligible)
                        good &= CurrentWindow != null && CurrentWindow.index == data.windows[0].index;
                    MultiRecord("seek_boundary_" + target.ToString("0.000", CultureInfo.InvariantCulture), good,
                        "real_decoder_and_confirmation", "The target is navigation only. Results must use the actually presented PTS; unfinished windows never form a confirmed result, including quantized boundaries.", runner, target);
                    if (!MultiFrameValid(runner)) break;
                }
                if (!MultiFrameValid(runner)) continue;

                foreach (float rate in new[] { .5f, 1f, 2f, 4f })
                {
                    if (!video.canSetPlaybackSpeed)
                    {
                        MultiRecord("play_rate_" + rate, false, "real_decoder", "The active decoder does not support playback speed changes.", runner);
                        break;
                    }
                    speed = rate;
                    video.playbackSpeed = rate;
                    video.Play();
                    yield return MultiDelay(.3);
                    long framesBefore = runner.totalFrameEvents;
                    double nativeBefore = video.time;
                    double unscaledBefore = Time.unscaledTimeAsDouble;
                    double gameBefore = Time.timeAsDouble;
                    var elapsed = Stopwatch.StartNew();
                    while (elapsed.Elapsed.TotalSeconds < 2 && string.IsNullOrEmpty(error)) yield return null;
                    double measured = (video.time - nativeBefore) / elapsed.Elapsed.TotalSeconds;
                    MultiRecord("play_rate_" + rate.ToString("0.0", CultureInfo.InvariantCulture),
                        MultiFrameValid(runner) && video.isPlaying && runner.totalFrameEvents > framesBefore &&
                        Math.Abs(video.playbackSpeed - rate) < .01 && measured > rate * .8 && measured < rate * 1.2 && MultiNoFutureWindow(),
                        "real_decoder", "Measured native video advancement over two real seconds after 0.3s settling; tolerance 80-120% of selected speed. Clock mode=" + video.timeUpdateMode +
                        "; unscaled/wall=" + ((Time.unscaledTimeAsDouble - unscaledBefore) / elapsed.Elapsed.TotalSeconds).ToString("F3") +
                        "; game/wall=" + ((Time.timeAsDouble - gameBefore) / elapsed.Elapsed.TotalSeconds).ToString("F3"), runner, -1, measured);
                    if (!MultiFrameValid(runner)) break;
                    video.Pause();
                    yield return MultiDelay(.25);
                    double pausedTime = video.time;
                    long pausedFrame = video.frame;
                    yield return MultiDelay(.5);
                    MultiRecord("pause_holds_frame_at_" + rate.ToString("0.0", CultureInfo.InvariantCulture),
                        MultiFrameValid(runner) && !video.isPlaying && video.frame == pausedFrame && Math.Abs(video.time - pausedTime) < 1e-6,
                        "real_decoder", "After pending decode settles, both native presentation time and decoded frame remain fixed.", runner);
                }
                if (!MultiFrameValid(runner)) continue;
                speed = 1;
                video.playbackSpeed = 1;

                // Exercise the same scrub/commit functions used by the on-screen timeline.
                video.Play();
                UpdateScrub(200.2);
                UpdateScrub(120.2);
                bool scrubHidden = dragging && !CanShowState && CurrentWindow == null && !video.isPlaying;
                CommitScrub();
                yield return MultiWaitReady(runner, 40);
                MultiRecord("timeline_scrub_commit_resumes", scrubHidden && MultiFrameValid(runner) && video.isPlaying &&
                    Math.Abs(lastCompletedSeekTime - 120.2) <= .12 && MultiNoFutureWindow(), "production_interaction_helper_and_real_decoder",
                    "Two drag updates and one commit must seek the real decoder, restore play intent and synchronize the observing/confirmed windows. No physical drag is synthesized.", runner, 120.2);
                if (!MultiFrameValid(runner)) continue;
                video.Pause();
                double focusTime = video.time;
                UpdateScrub(250.2);
                OnApplicationFocus(false);
                MultiRecord("timeline_focus_loss_cancels_preview", !dragging && MultiFrameValid(runner) && Math.Abs(video.time - focusTime) < .1,
                    "production_interaction_helper", "Losing application focus cancels an uncommitted timeline preview without changing the displayed timestamp.", runner);

                for (int region = 0; region < data.zones.Length; region++)
                {
                    bool selected = SelectRegion(region, true);
                    MultiRecord("region_select_" + region, selected && selectedRegion == region && showInsights,
                        "presentation_helper", "SelectRegion must accept every configured polygon, including the fifth region when present.", runner);
                }
                int oldSelection = selectedRegion;
                MultiRecord("region_reject_invalid_index", !SelectRegion(data.zones.Length, true) && selectedRegion == oldSelection,
                    "presentation_helper", "An invalid region index cannot replace the selected region.", runner);
                insightsMotionTab = false;
                yield return MultiCapture("count_details", runner, false);
                insightsMotionTab = true;
                yield return MultiCapture("motion_details", runner, false);
                showInsights = false;
                showSettings = true;
                yield return MultiCapture("methods", runner, false);
                showSettings = false;

                int held = Array.FindIndex(data.windows, w => w.held_duration_s > 0 && !w.evidence_eligible && w.end_s - w.start_s > 1);
                if (held >= 0)
                {
                    double target = data.windows[held].start_s + (data.windows[held].end_s - data.windows[held].start_s) * .5;
                    SeekTo(target, false);
                    yield return MultiWaitReady(runner, 40);
                    MultiRecord("held_window_has_no_confirmed_state", MultiFrameValid(runner) && CurrentObservingWindow != null &&
                        !CurrentObservingWindow.evidence_eligible && CurrentWindow == null, "real_decoder_and_evidence_gate",
                        "Seek inside a real long-hold window. Its incomplete visual evidence must not inherit a preceding confirmed traffic hint.", runner, target);
                    yield return MultiCapture("held_window", runner, true);
                }
                if (!MultiFrameValid(runner)) continue;

                double nearEnd = Math.Max(0, data.source.last_frame_time_s - .8);
                SeekTo(nearEnd, false);
                yield return MultiWaitReady(runner, 40);
                if (MultiFrameValid(runner))
                {
                    long beforeEnd = runner.totalFrameEvents;
                    video.Play();
                    var eofClock = Stopwatch.StartNew();
                    while (!ended && eofClock.Elapsed.TotalSeconds < 8 && string.IsNullOrEmpty(error)) yield return null;
                    bool tailCorrect = CurrentObservingWindow == null || CurrentObservingWindow.evidence_eligible || CurrentWindow == null;
                    MultiRecord("natural_eof_and_tail_state", ended && MultiFrameValid(runner) && !video.isPlaying &&
                        runner.totalFrameEvents > beforeEnd && MultiNoFutureWindow() && tailCorrect, "real_decoder_and_evidence_gate",
                        "Requires an actual loopPointReached event reached by playback. An incomplete terminal window remains insufficient; exact final-container-frame identity is not claimed.", runner);
                    yield return MultiCapture("end_of_video", runner, true);
                }
            }

            yield return MultiCheckSwitchAndRecovery(runner);
            SwitchSource(0);
            yield return MultiWaitReady(runner, 180);
            speed = 1;
            if (video != null && video.canSetPlaybackSpeed) video.playbackSpeed = 1;
            selectedRegion = -1;
            showInsights = showExamples = showSettings = showCalculationDetails = false;
            showSourceHeader = false;
            MultiRecord("restore_first_source_paused", MultiFrameValid(runner) && sourceIndex == 0 &&
                !video.isPlaying && PresentedTime < .2 && CurrentWindow == null, "real_decoder", "Leave the application at the first source, first decoded frame, paused with all panels closed.", runner);
            yield return MultiCapture("final_overview", runner, true);
            multiReport.inputs_after = MultiInputHashes();
            bool unchanged = multiReport.inputs_before.Length == multiReport.inputs_after.Length;
            for (int i = 0; unchanged && i < multiReport.inputs_before.Length; i++)
                unchanged &= multiReport.inputs_before[i].path == multiReport.inputs_after[i].path &&
                    multiReport.inputs_before[i].sha256 == multiReport.inputs_after[i].sha256;
            MultiRecord("packaged_inputs_unchanged", unchanged, "file_integrity", "Catalogue, data and PTS files are hashed before and after the runtime tests. Runtime tests never modify data or thresholds.", runner);
            MultiFinish();
            if (Array.IndexOf(Environment.GetCommandLineArgs(), "--verification-quit") >= 0)
            { yield return MultiDelay(.5); Application.Quit(multiFailed ? 1 : 0); }
        }

        private IEnumerator MultiCheckSwitchAndRecovery(MultiReplayVerificationRunner runner)
        {
            SwitchSource(0);
            SwitchSource(1);
            bool hiddenDuringHash = !CanShowState && CurrentWindow == null;
            yield return MultiWaitReady(runner, 180);
            MultiRecord("switch_during_hash_uses_latest_source", hiddenDuringHash && MultiFrameValid(runner) && sourceIndex == 1 && PresentedTime < .2,
                "production_source_switch", "Immediately replace a just-started hash/load with another source. Only the latest generation may create a usable decoder.", runner);
            if (!MultiFrameValid(runner)) yield break;

            VideoPlayer stale = video;
            SeekTo(930.2, false);
            bool seekPending = seeking && !CanShowState;
            SwitchSource(2);
            OnPrepared(stale);
            OnSeekCompleted(stale);
            OnFrameReady(stale, 123);
            OnVideoError(stale, "verification: obsolete decoder callback");
            bool ignored = string.IsNullOrEmpty(error) && !CanShowState && CurrentWindow == null;
            yield return MultiWaitReady(runner, 180);
            MultiRecord("switch_during_seek_ignores_old_callbacks", seekPending && ignored && MultiFrameValid(runner) && sourceIndex == 2 && PresentedTime < .2,
                "injected_obsolete_callbacks_and_real_decoder", "The old decoder's production handlers are explicitly invoked after a source switch; they must be ignored. The replacement must independently decode its actual first frame.", runner);
            if (!MultiFrameValid(runner)) yield break;

            multiVerificationSuppressedSeekPlayer = video;
            VideoPlayer suppressed = video;
            SeekTo(123.4, false);
            yield return MultiWaitReady(runner, 45);
            bool recovered = MultiFrameValid(runner) && video != suppressed && seekRecoveryCount == 1 &&
                Math.Abs(lastCompletedSeekTime - 123.4) <= .12 && !video.isPlaying;
            multiVerificationSuppressedSeekPlayer = null;
            MultiRecord("recover_dropped_seek_callbacks_once", recovered, "injected_callback_suppression_and_real_decoder",
                "Opt-in fault injection drops production seek callbacks from one decoder. The watchdog must replace it once, decode the unchanged real MP4 and finish the requested paused position.", runner, 123.4);

            string correctPath = data.source.video_path;
            pathText = Path.Combine(Path.GetTempPath(), "traffic_missing_" + Guid.NewGuid().ToString("N") + ".mp4");
            OpenVideo();
            MultiRecord("missing_video_error_hides_state", !string.IsNullOrEmpty(error) && !CanShowState && CurrentWindow == null,
                "real_file_error_path", "The file does not exist; the actual local-file resolver must show an error and hide traffic state.", runner);
            yield return MultiCapture("missing_video_error", runner, false);
            pathText = correctPath;
            OpenVideo(true);
            yield return MultiWaitReady(runner, 180);
            showSettings = false;
            MultiRecord("reload_recovers_from_missing_video", MultiFrameValid(runner) && string.IsNullOrEmpty(error) &&
                !video.isPlaying && PresentedTime < .2, "real_decoder_and_production_reload",
                "Restore the existing correct video path and use the same Verify and load path as the UI; hash verification and a fresh decoded first frame must succeed.", runner);
        }

        private void MultiCheckDataAndGeometry(MultiReplayVerificationRunner runner)
        {
            string id = catalog.entries[sourceIndex].id;
            int expectedZones = id.IndexOf("NE12", StringComparison.OrdinalIgnoreCase) >= 0 || id.IndexOf("Eastgate", StringComparison.OrdinalIgnoreCase) >= 0 ? 5 : 4;
            MultiRecord("dynamic_region_count", data.zones.Length == expectedZones, "data_contract", "Expected configured region count is " + expectedZones + ".", runner);
            bool boundaries = true, noFakeCount = true, noFakeMotion = true, sawMissingMotion = false;
            int unavailableCount = 0, unavailableMotion = 0, unknownLevels = 0;
            for (int i = 0; i < data.windows.Length; i++)
            {
                var window = data.windows[i];
                boundaries &= data.WindowAt(window.start_s) == i && data.WindowAt(window.end_s - 1e-6) == i;
                if (i + 1 < data.windows.Length && Math.Abs(data.windows[i + 1].start_s - window.end_s) < 1e-7)
                    boundaries &= data.WindowAt(window.end_s) == i + 1;
                else boundaries &= data.WindowAt(window.end_s) == -1;
                for (int z = 0; z < data.zones.Length; z++)
                {
                    var value = window.ForZone(data.zones[z].id);
                    double count, motion;
                    bool countPlotted = TryTrendValue(window, z, false, out count);
                    bool motionPlotted = TryTrendValue(window, z, true, out motion);
                    bool usable = window.complete && window.evidence_eligible && value.valid_duration_s >= 10 - 1e-6 && value.missing_duration_s <= 1e-6;
                    bool expectedCount = usable && value.count_available && TrafficDemoData.Finite(value.count_mean) && value.count_mean >= 0;
                    bool expectedMotion = usable && value.motion_available && TrafficDemoData.Finite(value.motion_median_px_s) && value.motion_median_px_s >= 0;
                    noFakeCount &= countPlotted == expectedCount && (!countPlotted || Math.Abs(count - value.count_mean) < 1e-9);
                    noFakeMotion &= motionPlotted == expectedMotion && (!motionPlotted || Math.Abs(motion - value.motion_median_px_s) < 1e-9);
                    if (!value.count_available) unavailableCount++;
                    if (!value.motion_available) { unavailableMotion++; sawMissingMotion = true; }
                    if (value.relative_count_level == "unknown") unknownLevels++;
                }
            }
            MultiRecord("half_open_window_lookup", boundaries && data.WindowAt(data.source.duration_s) == -1 && data.WindowAt(-1) == -1,
                "data_helper", "Every real window start/end is checked as [start,end); out-of-range times do not borrow a stale window.", runner);
            MultiRecord("count_series_preserves_missing_evidence", noFakeCount, "actual_chart_value_helper",
                "All zone/window rows checked; unavailable count rows=" + unavailableCount + ". Unavailable or incomplete samples are omitted, never plotted as zero.", runner);
            MultiRecord("motion_series_preserves_missing_evidence", noFakeMotion && sawMissingMotion, "actual_chart_value_helper",
                "All zone/window rows checked; unavailable motion rows=" + unavailableMotion + ". Missing motion cannot become an apparent stopped vehicle sample.", runner);
            if (sourceIndex == 1)
            {
                bool noCalibration = true;
                foreach (var zone in data.zones) noCalibration &= !zone.count_calibration_available;
                MultiRecord("night_source_has_no_reliable_count_grading", noCalibration && unknownLevels > 0,
                    "data_and_presentation_contract", "The night source retains count observations while region count calibration is unavailable; unknown levels must remain unknown.", runner);
            }

            if (sourceIndex >= 5)
            {
                bool conservative = true;
                int unknown = 0, countOnlyUnknown = 0;
                foreach (var window in data.windows)
                    foreach (var value in window.zones)
                    {
                        string display = TrafficDemoData.DisplayState(value);
                        conservative &= value.evidence_state == "unknown" ? display == "evidence_unknown" :
                            value.evidence_state == "joint_hint" ? display == "high_count_low_motion_persistent" :
                            value.evidence_state == "high_count_hint" ? display == "high_count_persistent" :
                            display == "no_persistent_signal";
                        if (value.evidence_state == "unknown") unknown++;
                        if (value.evidence_state == "unknown" && value.high_count_persistent_count_only)
                        { countOnlyUnknown++; conservative &= display == "evidence_unknown"; }
                    }
                MultiRecord("supplementary_conservative_evidence_display", conservative && unknown > 0,
                    "saved_data_and_actual_display_selector", "Every supplementary zone/window uses evidence_state; " + unknown +
                    " unknown rows and " + countOnlyUnknown + " count-only unknown rows cannot be promoted by frozen state_hint.", runner);
                if (sourceIndex == 9)
                    MultiRecord("supplementary_ne8th_all_unknown", unknown == data.windows.Length * data.zones.Length,
                        "saved_data_and_actual_display_selector", "Night NE8th keeps every composite region/window unknown.", runner);
                if (sourceIndex == 6)
                    MultiRecord("supplementary_ne12th_short_train", data.source.split == "Train" &&
                        Math.Abs(data.source.duration_s - 408.57685555555554) < .001,
                        "source_contract", "The supplementary NE12th recording is the 408.577-second Train case.", runner);
            }

            Rect target = new Rect(17, 31, 1280, data.source.video_height);
            bool geometry = data.source.width == 1280 && data.source.height == 720 && data.source.video_width == 1280 &&
                data.source.video_height == data.source.content_y + 720 && data.source.content_x == 0 &&
                (sourceIndex < 5 ? data.source.content_y == 150 : data.source.content_y == 0);
            foreach (var zone in data.zones)
                foreach (var point in zone.polygon)
                {
                    Vector2 full = RegionPointToCanvas(point, target, false);
                    Vector2 cropped = RegionPointToCanvas(point, new Rect(17, 31, 1280, 720), true);
                    geometry &= Math.Abs(full.x - (17 + point.x * 1280)) < .001 &&
                        Math.Abs(full.y - (31 + data.source.content_y + point.y * 720)) < .001 &&
                        Math.Abs(cropped.x - (17 + point.x * 1280)) < .001 && Math.Abs(cropped.y - (31 + point.y * 720)) < .001;
                }
            MultiRecord("polygon_video_alignment", geometry, "actual_coordinate_helper_and_decoder_dimensions",
                "Every polygon vertex is checked against its real content rectangle: original exports add a 150-pixel header, supplementary full originals do not.", runner);
        }

        private bool MultiNoFutureWindow()
        {
            if (ended) return CurrentWindow == null && CurrentObservingWindow == data.windows[data.windows.Length - 1];
            var current = CurrentWindow;
            var observing = CurrentObservingWindow;
            if (current != null && (!current.complete || !current.evidence_eligible || current.end_s > PresentedTime + 1e-7)) return false;
            if (observing != null && !observing.evidence_eligible && current != null) return false;
            int wi = data.WindowAt(PresentedTime);
            return observing == null ? wi < 0 : wi >= 0 && observing.index == data.windows[wi].index;
        }

        private bool MultiFrameValid(MultiReplayVerificationRunner runner)
        {
            return CanControl && CanShowState && video != null && video.texture != null && video.frame >= 0 &&
                runner.observedPlayer == video && runner.playerFrameEvents > 0 && data != null && PresentedTime >= 0 &&
                TrafficDemoData.Finite(PresentedTime) && string.IsNullOrEmpty(error);
        }

        private IEnumerator MultiWaitReady(MultiReplayVerificationRunner runner, double timeout)
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed.TotalSeconds < timeout)
            {
                runner.ObservePlayer();
                if (!string.IsNullOrEmpty(error)) yield break;
                if (MultiFrameValid(runner)) yield break;
                yield return null;
            }
        }

        private static IEnumerator MultiDelay(double seconds)
        { var clock = Stopwatch.StartNew(); while (clock.Elapsed.TotalSeconds < seconds) yield return null; }

        private MultiReplayInput[] MultiInputHashes()
        {
            string root = Path.Combine(Application.streamingAssetsPath, "five_sources");
            var hashes = new List<MultiReplayInput>();
            if (!Directory.Exists(root)) return hashes.ToArray();
            var files = Directory.GetFiles(root, "*", SearchOption.AllDirectories);
            Array.Sort(files, StringComparer.Ordinal);
            foreach (string file in files)
            {
                if (file.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)) continue;
                hashes.Add(new MultiReplayInput { path = file.Substring(root.Length + 1), sha256 = HashFile(file, CancellationToken.None) });
            }
            return hashes.ToArray();
        }

        private void MultiRecord(string name, bool passed, string scope, string evidence, MultiReplayVerificationRunner runner,
            double target = -1, double rate = -1)
        {
            multiFailed |= !passed;
            multiChecks.Add(new MultiReplayCheck
            {
                name = name, passed = passed, scope = scope, evidence = evidence, utc = DateTime.UtcNow.ToString("o"),
                source_index = sourceIndex, source_id = data == null ? "" : data.source.id,
                requested_time_s = target, native_player_time_s = video == null ? -1 : video.time,
                presented_source_pts_s = PresentedTime, decoded_frame_index = video == null ? -1 : video.frame,
                frame_ready_events = runner.totalFrameEvents, active_player_frame_ready_events = runner.playerFrameEvents,
                observing_window_index = CurrentObservingWindow == null ? -1 : CurrentObservingWindow.index,
                confirmed_window_index = CurrentWindow == null ? -1 : CurrentWindow.index,
                confirmed_window_end_s = CurrentWindow == null ? -1 : CurrentWindow.end_s,
                selected_region = selectedRegion, region_count = data == null ? 0 : data.zones.Length,
                selected_speed = speed, measured_playback_rate = rate, recovery_count = seekRecoveryCount,
                playing = video != null && video.isPlaying, seeking = seeking, ended = ended,
                decoder_width = video == null || video.texture == null ? 0 : video.texture.width,
                decoder_height = video == null || video.texture == null ? 0 : video.texture.height,
                verified_video_path = verifiedPath, controller_error = error, operation = operation
            });
            MultiPersist();
            UnityEngine.Debug.Log("FiveSourcesVerification " + (passed ? "PASS" : "FAIL") + " " + sourceIndex + "/" + name + " : " + evidence);
        }

        private IEnumerator MultiCapture(string kind, MultiReplayVerificationRunner runner, bool texture)
        {
            yield return null;
            yield return null;
            string source = data == null ? "no_source" : data.source.id;
            foreach (char c in Path.GetInvalidFileNameChars()) source = source.Replace(c, '_');
            string stem = source + "_" + kind + "_" + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss_fff");
            string screenshot = Path.Combine(multiDirectory, stem + ".png");
            string decoded = texture ? MultiCaptureTexture(stem) : "";
            string captureError = "";
            try { ScreenCapture.CaptureScreenshot(screenshot); }
            catch (Exception e) { captureError = e.Message; }
            var wait = Stopwatch.StartNew();
            while (wait.Elapsed.TotalSeconds < 5 && (!File.Exists(screenshot) || new FileInfo(screenshot).Length <= 64)) yield return null;
            bool exists = File.Exists(screenshot) && new FileInfo(screenshot).Length > 64;
            multiCaptures.Add(new MultiReplayCapture
            {
                kind = kind, source_id = source, screenshot_path = exists ? screenshot : "", decoded_texture_path = decoded,
                capture_error = captureError, status = exists ? "captured_not_visually_reviewed" : "capture_failed",
                native_player_time_s = video == null ? -1 : video.time, presented_source_pts_s = PresentedTime,
                frame_index = video == null ? -1 : video.frame, selected_region = selectedRegion,
                physical_input_tested = false, exact_container_frame_identity_verified = false
            });
            MultiRecord("capture_" + kind, exists && (!texture || !string.IsNullOrEmpty(decoded)), "evidence_capture",
                "Captured the application's rendered UI" + (texture ? " and the decoder's actual texture" : "") + "; visual review is separate.", runner);
        }

        private string MultiCaptureTexture(string stem)
        {
            if (video == null || video.texture == null) return "";
            RenderTexture previous = RenderTexture.active;
            RenderTexture target = null;
            Texture2D pixels = null;
            try
            {
                target = RenderTexture.GetTemporary(video.texture.width, video.texture.height, 0, RenderTextureFormat.ARGB32);
                Graphics.Blit(video.texture, target);
                RenderTexture.active = target;
                pixels = new Texture2D(target.width, target.height, TextureFormat.RGB24, false);
                pixels.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0);
                pixels.Apply();
                string path = Path.Combine(multiDirectory, "decoded_" + stem + ".png");
                File.WriteAllBytes(path, pixels.EncodeToPNG());
                return path;
            }
            catch (Exception e) { UnityEngine.Debug.LogWarning("Five-source texture capture: " + e.Message); return ""; }
            finally
            {
                RenderTexture.active = previous;
                if (target != null) RenderTexture.ReleaseTemporary(target);
                if (pixels != null) Destroy(pixels);
            }
        }

        private void MultiFinish()
        {
            multiReport.status = multiFailed ? "failed" : "passed";
            multiReport.finished_utc = DateTime.UtcNow.ToString("o");
            MultiPersist();
            UnityEngine.Debug.Log("FiveSourcesVerification complete: " + multiReport.status + " " + Path.Combine(multiDirectory, "runtime_verification.json"));
        }

        internal void MultiAbort(string message)
        {
            if (multiReport == null || multiReport.status != "running") return;
            multiReport.status = "interrupted";
            multiReport.unhandled_error = message;
            multiReport.finished_utc = DateTime.UtcNow.ToString("o");
            MultiPersist();
        }

        private void MultiPersist()
        {
            if (multiReport == null) return;
            multiReport.checks = multiChecks.ToArray();
            multiReport.captures = multiCaptures.ToArray();
            multiReport.passed_checks = 0;
            multiReport.failed_checks = 0;
            foreach (var check in multiChecks) if (check.passed) multiReport.passed_checks++; else multiReport.failed_checks++;
            File.WriteAllText(Path.Combine(multiDirectory, "runtime_verification.json"), JsonUtility.ToJson(multiReport, true));
        }
    }

    internal sealed class MultiReplayVerificationRunner : MonoBehaviour
    {
        internal VideoPlayer observedPlayer;
        internal long totalFrameEvents, playerFrameEvents;
        private TrafficReplayController controller;
        internal void Initialize(TrafficReplayController owner)
        { controller = owner; ObservePlayer(); StartCoroutine(Drive(owner.RunMultiVerification(this))); }
        private void Update() { ObservePlayer(); }
        internal void ObservePlayer()
        {
            if (controller == null || observedPlayer == controller.MultiObservedPlayer) return;
            if (observedPlayer != null) observedPlayer.frameReady -= FrameReady;
            observedPlayer = controller.MultiObservedPlayer;
            playerFrameEvents = 0;
            if (observedPlayer != null) observedPlayer.frameReady += FrameReady;
        }
        private void FrameReady(VideoPlayer player, long index)
        { if (player == observedPlayer) { totalFrameEvents++; playerFrameEvents++; } }
        private IEnumerator Drive(IEnumerator work)
        {
            var stack = new Stack<IEnumerator>();
            stack.Push(work);
            while (stack.Count > 0)
            {
                object next = null;
                bool advanced;
                try { advanced = stack.Peek().MoveNext(); if (advanced) next = stack.Peek().Current; }
                catch (Exception e) { controller.MultiAbort(e.ToString()); UnityEngine.Debug.LogException(e); yield break; }
                if (!advanced) { stack.Pop(); continue; }
                var nested = next as IEnumerator;
                if (nested != null) { stack.Push(nested); continue; }
                yield return next;
            }
        }
        private void OnDisable()
        {
            if (observedPlayer != null) observedPlayer.frameReady -= FrameReady;
            if (controller != null) controller.MultiAbort("Verification runner disabled before completion.");
        }
    }

    [Serializable] internal sealed class MultiReplayVerificationReport
    {
        public string status, started_utc, finished_utc, unity_version, platform, verification_kind, limitation, unhandled_error;
        public bool physical_input_tested, synthetic_clock_used, exact_container_frame_identity_verified;
        public int passed_checks, failed_checks;
        public MultiReplayInput[] inputs_before, inputs_after;
        public MultiReplayCheck[] checks;
        public MultiReplayCapture[] captures;
    }
    [Serializable] internal sealed class MultiReplayInput { public string path, sha256; }
    [Serializable] internal sealed class MultiReplayCheck
    {
        public string name, scope, evidence, utc, source_id, verified_video_path, controller_error, operation;
        public bool passed, playing, seeking, ended;
        public int source_index, observing_window_index, confirmed_window_index, selected_region, region_count, decoder_width, decoder_height, recovery_count;
        public long decoded_frame_index, frame_ready_events, active_player_frame_ready_events;
        public double requested_time_s, native_player_time_s, presented_source_pts_s, confirmed_window_end_s, measured_playback_rate;
        public float selected_speed;
    }
    [Serializable] internal sealed class MultiReplayCapture
    {
        public string kind, source_id, screenshot_path, decoded_texture_path, capture_error, status;
        public double native_player_time_s, presented_source_pts_s;
        public long frame_index;
        public int selected_region;
        public bool physical_input_tested, exact_container_frame_identity_verified;
    }
}
