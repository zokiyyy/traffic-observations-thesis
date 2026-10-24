#if UNITY_EDITOR
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace TrafficDemo
{
    // Opt-in engine integration checks. No OS input synthesis and no synthetic playback clock.
    public sealed partial class TrafficReplayController
    {
        private long verificationDecodedFrames, verificationLastFrameIndex = -1;
        private ReplayVerificationReport verificationReport;
        private readonly List<ReplayVerificationStep> verificationSteps = new List<ReplayVerificationStep>();
        private readonly List<ReplayTimingDiagnostic> verificationDiagnostics = new List<ReplayTimingDiagnostic>();
        private readonly List<ReplayUiFeatureCheck> verificationUiChecks = new List<ReplayUiFeatureCheck>();
        private readonly List<ReplayViewCapture> verificationViewCaptures = new List<ReplayViewCapture>();
        private bool verificationFailed, verificationChecksFinished;
        private string verificationDirectory;

        private void BeginEditorVerificationIfRequested()
        {
            if (!SessionState.GetBool("TrafficDemoVerifyRequested", false)) return;
            SessionState.SetBool("TrafficDemoVerifyRequested", false);
            if (Application.isBatchMode) return;
            StartCoroutine(RunEditorVerification());
        }

        private IEnumerator RunEditorVerification()
        {
            verificationDirectory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Verification"));
            Directory.CreateDirectory(verificationDirectory);
            verificationReport = new ReplayVerificationReport
            {
                status = "running", started_utc = DateTime.UtcNow.ToString("o"),
                editor_version = Application.unityVersion,
                verification_kind = "real VideoPlayer integration plus presentation-helper checks; physical UI button clicks were not tested",
                synthetic_playback_clock = false, ui_button_clicks_tested = false,
                screenshot_status = "not_attempted"
            };
            verificationReport.input_json_path = Path.Combine(Application.streamingAssetsPath, "traffic_demo.json");
            verificationReport.input_json_sha256_before = ReadVerificationInputHash();
            showSettings = false;
            showSourceHeader = false;
            showInsights = showExamples = false;
            selectedRegion = -1;
            yield return WaitForVerificationReady(120);
            bool firstFrame = VerificationFrameValid() && video.frame == 0 && video.time >= 0 && video.time <= 0.1 && !video.isPlaying;
            RecordVerification("initial_decoded_frame_paused", firstFrame, 0, 0,
                "Requires a real frameReady event, a decoded texture, actual first frame, and matching data window.");
            if (firstFrame) yield return VerifyPlaybackAndSeeks();
            if (!verificationFailed) yield return VerifySeekStability();

            if (CanControl && video.canSetTime)
            {
                speed = 1;
                if (video.canSetPlaybackSpeed) video.playbackSpeed = 1;
                SeekTo(0, false);
                yield return WaitForVerificationReady(35);
                RecordVerification("restore_first_frame_paused", VerificationFrameValid() && video.frame == 0 &&
                    video.time <= 0.1 && !video.isPlaying, 0, 0, "Restore actual first frame and normal playback speed.");
            }
            else
                RecordVerification("restore_first_frame_paused", false, 0, 0,
                    "Cannot safely restore because the decoder or controller is unavailable: " + error);

            // Keep the original eleven engine cases distinct from helper-driven feature coverage.
            if (!verificationFailed) yield return VerifyUiFeatures();
            verificationReport.input_json_sha256_after = ReadVerificationInputHash();
            RecordUiFeature("input_json_unchanged", !string.IsNullOrEmpty(verificationReport.input_json_sha256_before) &&
                verificationReport.input_json_sha256_before == verificationReport.input_json_sha256_after,
                "file_integrity", "Compare the exact StreamingAssets JSON bytes before and after all engine/helper checks. No data or thresholds may be rewritten.");

            // All assertions have finished; capture normal enabled views rather than disabled QA controls.
            verificationChecksFinished = true;
            if (VerificationFrameValid())
            {
                yield return CaptureFeatureViews();
                yield return CaptureVerificationGameView();
            }
            verificationReport.status = verificationFailed ? "failed" : "passed";
            verificationReport.finished_utc = DateTime.UtcNow.ToString("o");
            PersistVerification();
            UnityEngine.Debug.Log("TrafficDemo runtime verification " + verificationReport.status + ": " +
                Path.Combine(verificationDirectory, "runtime_verification.json"));
        }

        private IEnumerator VerifyUiFeatures()
        {
            if (!VerificationFrameValid() || CurrentWindow.index != 0)
            {
                RecordUiFeature("feature_checks_start_at_first_window", false, "engine_precondition",
                    "Feature checks require the real paused first window after the original engine cases.");
                yield break;
            }

            bool selected = SelectRegion(3, true);
            bool selectionMatches = selected && selectedRegion == 3 && showInsights;
            bool rejectedInvalid = !SelectRegion(data.zones.Length, true) && selectedRegion == 3 && showInsights;
            RecordUiFeature("region_selection_opens_details", selectionMatches && rejectedInvalid, "presentation_helper",
                "Call the same SelectRegion helper as the interface. A valid selection opens its details; an invalid index must not replace the selection.", 3);

            var missingMotion = CurrentWindow.ForZone(data.zones[3].id);
            double unusedMotion;
            bool motionPlotted = TryTrendValue(CurrentWindow, 3, true, out unusedMotion);
            RecordUiFeature("unavailable_motion_is_not_a_zero_sample", missingMotion != null &&
                !missingMotion.motion_available && missingMotion.motion_median_px_s < 0 && !motionPlotted,
                "actual_chart_value_helper", "The first-window Junction center has unavailable motion. The actual plotting helper must return false, so its out value is not a plotted zero or a speed.", 3);

            bool upperSelected = SelectRegion(1, true);
            var upper = CurrentWindow.ForZone(data.zones[1].id);
            double count;
            bool countAvailable = TryTrendValue(CurrentWindow, 1, false, out count);
            string explanation = upper == null ? "" : PersistenceExplanation(upper);
            bool firstHighNotSustained = upperSelected && selectedRegion == 1 && showInsights && upper != null &&
                countAvailable && Math.Abs(count - upper.count_mean) < 1e-9 && count > data.zones[1].density_high_lower &&
                upper.density_level == "high" && Math.Abs(upper.high_count_duration_s - 10) < 1e-9 &&
                upper.traffic_state == "no_persistent_signal" && StateShort(upper.traffic_state) == "No sustained signal" &&
                !string.IsNullOrEmpty(explanation) && explanation.Contains("10") && explanation.Contains("30");
            RecordUiFeature("first_window_high_count_is_not_sustained", firstHighNotSustained, "actual_presentation_helpers",
                "Use the actual count-series and persistence-explanation helpers: Upper road is high count in window 0, but 10 seconds is below 30 seconds. State must remain No sustained signal. Explanation: " + explanation, 1);

            bool targetsValid = data.milestones != null && data.milestones.Length == 9;
            if (targetsValid)
                for (int i = 0; i < data.milestones.Length; i++)
                {
                    double target;
                    int sourceWindow = data.WindowAt(data.milestones[i].time_s);
                    if (!TryExampleTarget(i, out target) || sourceWindow < 0 || !TrafficDemoData.Finite(target) ||
                        target < data.windows[sourceWindow].start_s || target >= data.windows[sourceWindow].end_s ||
                        target > data.source.last_frame_time_s) targetsValid = false;
                }
            RecordUiFeature("existing_example_targets_stay_in_their_windows", targetsValid, "navigation_helper",
                "Validate the nine existing demonstration milestones as navigation examples, not newly detected traffic events. Each actual helper target must stay in its milestone's source window.");

            if (!verificationFailed)
            {
                int[] examples = { 1, 8 };
                int[] expectedRegions = { 2, 0 };
                for (int i = 0; i < examples.Length; i++)
                {
                    int example = examples[i];
                    double target;
                    bool hasTarget = TryExampleTarget(example, out target);
                    int expectedWindow = hasTarget ? data.WindowAt(target) : -1;
                    expectedWindow = expectedWindow < 0 ? -1 : data.windows[expectedWindow].index;
                    long decodedBefore = verificationDecodedFrames;
                    showExamples = true;
                    bool opened = hasTarget && OpenExample(example);
                    if (opened) yield return WaitForVerificationReady(35);
                    bool passed = opened && VerificationFrameValid() && !video.isPlaying && !showExamples &&
                        verificationDecodedFrames > decodedBefore && Math.Abs(video.time - target) <= 0.12 &&
                        CurrentWindow.index == expectedWindow && selectedRegion == expectedRegions[i];
                    RecordUiFeature("example_" + example + "_seeks_real_video", passed, "navigation_helper_and_real_video",
                        "Call OpenExample, then require a newly decoded real frame, completed seek, correct reported-time window and intended region. This does not simulate a mouse click.",
                        expectedRegions[i], target, expectedWindow);
                    if (!passed) break;
                }
            }

            if (CanControl && video.canSetTime)
            {
                SeekTo(0, false);
                yield return WaitForVerificationReady(35);
            }
            showInsights = showExamples = false;
            insightsMotionTab = false;
            selectedRegion = -1;
            RecordUiFeature("feature_checks_restore_first_frame_overview", VerificationFrameValid() &&
                video.frame == 0 && video.time <= 0.1 && !video.isPlaying && selectedRegion == -1 &&
                !showInsights && !showExamples, "real_video_and_presentation_state",
                "After helper-driven navigation, restore the real first frame paused in the overview.", -1, 0, 0);
        }

        private string ReadVerificationInputHash()
        {
            try { return HashFile(verificationReport.input_json_path, System.Threading.CancellationToken.None); }
            catch (Exception e)
            {
                verificationReport.input_json_integrity_error = e.Message;
                return "";
            }
        }

        private void RecordUiFeature(string name, bool passed, string scope, string evidence,
            int expectedRegion = -1, double expectedTime = -1, int expectedWindow = -1)
        {
            verificationFailed |= !passed;
            var current = CurrentWindow;
            var value = current != null && data != null && selectedRegion >= 0 && selectedRegion < data.zones.Length
                ? current.ForZone(data.zones[selectedRegion].id) : null;
            verificationUiChecks.Add(new ReplayUiFeatureCheck
            {
                name = name, passed = passed, scope = scope, evidence = evidence,
                measured_utc = DateTime.UtcNow.ToString("o"),
                expected_region_index = expectedRegion, selected_region_index = selectedRegion,
                expected_time_s = expectedTime, reported_time_s = video == null ? -1 : video.time,
                expected_window_index = expectedWindow, current_window_index = current == null ? -1 : current.index,
                details_open = showInsights, examples_open = showExamples,
                zone_id = value == null ? "" : value.zone_id,
                traffic_state = value == null ? "" : value.traffic_state,
                density_level = value == null ? "" : value.density_level,
                motion_available = value != null && value.motion_available,
                frame_ready_events = verificationDecodedFrames, controller_error = error
            });
            PersistVerification();
            UnityEngine.Debug.Log("TrafficDemo feature " + name + "=" + (passed ? "PASS" : "FAIL") + "; scope=" + scope);
        }

        private IEnumerator CaptureFeatureViews()
        {
            showSettings = showExamples = showCalculationDetails = false;
            if (SelectRegion(1, true))
            {
                insightsMotionTab = false;
                yield return CaptureVerificationView("details_count", false);
                showCalculationDetails = true;
                calculationScroll = Vector2.zero;
                yield return CaptureVerificationView("calculation_details", false);
                showCalculationDetails = false;
            }
            if (SelectRegion(3, true))
            {
                insightsMotionTab = true;
                yield return CaptureVerificationView("details_motion_unavailable", false);
            }
            showInsights = false;
            showExamples = true;
            yield return CaptureVerificationView("examples", false);
            showExamples = false;
            if (SelectRegion(1, true))
            {
                insightsMotionTab = false;
                showSourceHeader = true;
                yield return CaptureVerificationView("source_header_selected_region", false);
            }
            showSourceHeader = false;
            showExamples = showInsights = false;
            insightsMotionTab = false;
            selectedRegion = -1;
        }

        private IEnumerator VerifyPlaybackAndSeeks()
        {
            long decodedBefore = verificationDecodedFrames;
            double before = video.time;
            video.Play();
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed.TotalSeconds < 1.5 && string.IsNullOrEmpty(error)) yield return null;
            double wall = elapsed.Elapsed.TotalSeconds;
            double advanced = video.time - before;
            RecordVerification("play_1x", VerificationFrameValid() && video.isPlaying &&
                verificationDecodedFrames > decodedBefore && advanced > 0.2 && advanced / wall < 1.7,
                -1, -1, "Measured advancement from real VideoPlayer.time over elapsed wall time.", advanced / wall);
            if (verificationFailed) yield break;

            video.Pause();
            elapsed.Restart();
            while (elapsed.Elapsed.TotalSeconds < 0.25 && string.IsNullOrEmpty(error)) yield return null;
            before = video.time;
            long frameBefore = video.frame;
            elapsed.Restart();
            while (elapsed.Elapsed.TotalSeconds < 0.75 && string.IsNullOrEmpty(error)) yield return null;
            RecordVerification("pause_holds_decoded_frame", VerificationFrameValid() && !video.isPlaying &&
                video.frame == frameBefore && Math.Abs(video.time - before) < 1e-6, before, -1,
                "After allowing a pending decode to settle, actual time and frame must remain fixed.");
            if (verificationFailed) yield break;

            if (!video.canSetPlaybackSpeed)
            {
                RecordVerification("play_2x", false, -1, -1, "This decoder reports canSetPlaybackSpeed=false.");
                yield break;
            }
            speed = 2;
            video.playbackSpeed = 2;
            before = video.time;
            decodedBefore = verificationDecodedFrames;
            video.Play();
            elapsed.Restart();
            while (elapsed.Elapsed.TotalSeconds < 2 && string.IsNullOrEmpty(error)) yield return null;
            wall = elapsed.Elapsed.TotalSeconds;
            double measuredRate = (video.time - before) / wall;
            RecordVerification("play_2x", VerificationFrameValid() && video.isPlaying &&
                verificationDecodedFrames > decodedBefore && Math.Abs(video.playbackSpeed - 2) < 0.01 &&
                measuredRate >= 1.4 && measuredRate <= 2.6, -1, -1,
                "Requires actual decoded time to advance at 1.4–2.6 seconds per elapsed second; setting the property alone is insufficient.", measuredRate);
            video.Pause();
            speed = 1;
            video.playbackSpeed = 1;
            if (verificationFailed) yield break;

            double[] targets = { 20.2, 930.2, 1800.2, 3599.5, data.source.last_frame_time_s };
            for (int i = 0; i < targets.Length; i++)
            {
                double target = targets[i];
                int expected = data.WindowAt(target);
                expected = expected < 0 ? -1 : data.windows[expected].index;
                decodedBefore = verificationDecodedFrames;
                SeekTo(target, false);
                yield return WaitForVerificationReady(35);
                bool last = i == targets.Length - 1;
                bool passed = VerificationFrameValid() && !video.isPlaying && verificationDecodedFrames > decodedBefore &&
                    Math.Abs(video.time - target) <= (last ? 0.1 : 0.12) &&
                    CurrentWindow != null && CurrentWindow.index == expected;
                string name = last ? "seek_end_segment" : "seek_" + target.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture);
                string texturePath = CaptureDecodedTexture(name);
                if (last)
                    verificationDiagnostics.Add(new ReplayTimingDiagnostic
                    {
                        name = "source_last_pts_vs_unity_navigation_time",
                        requested_source_pts_s = target,
                        reported_unity_time_s = video == null ? -1 : video.time,
                        signed_error_s = video == null ? 0 : video.time - target,
                        strict_tolerance_s = 0.0001,
                        strict_time_match = video != null && Math.Abs(video.time - target) <= 0.0001,
                        source_frame_identity_verified = false,
                        decoded_texture_path = texturePath,
                        interpretation = "Timing observation only, not a product pass/fail. Unity time/frame can be quantized navigation values. Inspect the burned-in source header to establish frame identity. End seeks do not claim the exact last source frame."
                    });
                RecordVerification(name, passed, target, expected,
                    last ? "End-segment navigation requires completed seek, a real decoded frame, <=0.1s reported-time error and the expected data window. Exact last-source-frame identity is not claimed; the strict timestamp comparison is recorded separately." :
                    "The controller must receive seekCompleted and a real target frameReady; the shown window must match the Unity-reported video timestamp.",
                    -1, texturePath);
                if (verificationFailed) yield break;
            }

            // Reach EOF by playback, not by assuming an end seek decoded the final source frame.
            SeekTo(3599.5, false);
            yield return WaitForVerificationReady(35);
            if (!VerificationFrameValid() || Math.Abs(video.time - 3599.5) > 0.12)
            {
                RecordVerification("natural_eof", false, -1, 359,
                    "Could not prepare the near-end playback position for a real EOF event.");
                yield break;
            }
            decodedBefore = verificationDecodedFrames;
            video.Play();
            elapsed.Restart();
            while (!ended && elapsed.Elapsed.TotalSeconds < 5 && string.IsNullOrEmpty(error)) yield return null;
            bool eofPassed = ended && VerificationFrameValid() && !video.isPlaying &&
                verificationDecodedFrames > decodedBefore && CurrentWindow.index == 359;
            string eofTexture = CaptureDecodedTexture("natural_eof");
            RecordVerification("natural_eof", eofPassed, -1, 359,
                "Requires the actual loopPointReached callback within 5 seconds, decoded frames, automatic pause and window 359 selected from Unity-reported time. Review the captured source header separately for exact final-frame identity.",
                -1, eofTexture);
        }

        private IEnumerator WaitForVerificationReady(double timeoutSeconds)
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed.TotalSeconds < timeoutSeconds)
            {
                if (!string.IsNullOrEmpty(error)) yield break;
                if (CanControl && video.texture != null && verificationDecodedFrames > 0) yield break;
                yield return null;
            }
        }

        private bool VerificationFrameValid()
        {
            if (!CanShowState || video.texture == null || verificationDecodedFrames <= 0 || video.frame < 0 ||
                !TrafficDemoData.Finite(video.time) || data == null || CurrentWindow == null) return false;
            int index = data.WindowAt(video.time);
            return index >= 0 && CurrentWindow.index == data.windows[index].index;
        }

        private void RecordVerification(string name, bool passed, double expectedTime, int expectedWindow,
            string evidence, double measuredRate = -1, string texturePath = "")
        {
            verificationFailed |= !passed;
            verificationSteps.Add(new ReplayVerificationStep
            {
                name = name, passed = passed, measured_utc = DateTime.UtcNow.ToString("o"),
                expected_time_s = expectedTime, expected_window_index = expectedWindow,
                actual_time_s = video == null ? -1 : video.time,
                actual_frame_index = video == null ? -1 : video.frame,
                last_frame_ready_index = verificationLastFrameIndex,
                frame_ready_events = verificationDecodedFrames,
                current_window_index = CurrentWindow == null ? -1 : CurrentWindow.index,
                playing = video != null && video.isPlaying,
                end_event_seen = ended,
                texture_width = video == null || video.texture == null ? 0 : video.texture.width,
                texture_height = video == null || video.texture == null ? 0 : video.texture.height,
                decoded_texture_path = texturePath,
                requested_time_error_s = video == null || expectedTime < 0 ? 0 : video.time - expectedTime,
                player_reported_frame_rate = video == null ? 0 : video.frameRate,
                player_reported_frame_count = video == null ? 0 : video.frameCount,
                player_reported_length_s = video == null ? 0 : video.length,
                measured_playback_rate = measuredRate,
                controller_error = error, controller_operation = operation, evidence = evidence
            });
            PersistVerification();
            UnityEngine.Debug.Log("TrafficDemo verify " + name + "=" + (passed ? "PASS" : "FAIL") +
                "; actualTime=" + (video == null ? "unavailable" : video.time.ToString("F6")) +
                "; window=" + (CurrentWindow == null ? -1 : CurrentWindow.index));
        }

        private string CaptureDecodedTexture(string name)
        {
            if (video == null || video.texture == null) return "";
            RenderTexture before = RenderTexture.active;
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
                string file = Path.Combine(verificationDirectory, "decoded_" + name + "_" + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss_fff") + ".png");
                File.WriteAllBytes(file, pixels.EncodeToPNG());
                return file;
            }
            catch (Exception e)
            {
                UnityEngine.Debug.LogWarning("TrafficDemo decoded texture capture failed: " + e.Message);
                return "";
            }
            finally
            {
                RenderTexture.active = before;
                if (target != null) RenderTexture.ReleaseTemporary(target);
                if (pixels != null) Destroy(pixels);
            }
        }

        private IEnumerator CaptureVerificationGameView()
        {
            yield return CaptureVerificationView("overview", true);
        }

        private IEnumerator CaptureVerificationView(string kind, bool primary)
        {
            // Let the paused first frame and its IMGUI state repaint before requesting capture.
            yield return null;
            yield return null;
            string prefix = primary ? "runtime_game_" : "view_" + kind + "_";
            string file = Path.Combine(verificationDirectory, prefix + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss_fff") + ".png");
            bool requested = false;
            string captureError = "";
            try { ScreenCapture.CaptureScreenshot(file); requested = true; }
            catch (Exception e) { captureError = e.Message; }
            if (requested)
            {
                var elapsed = Stopwatch.StartNew();
                while (elapsed.Elapsed.TotalSeconds < 10 && (!File.Exists(file) || new FileInfo(file).Length < 64)) yield return null;
            }
            bool exists = File.Exists(file) && new FileInfo(file).Length >= 64;
            string status = exists ? "captured_not_visually_reviewed" : "unavailable_use_manual_capture";
            if (primary)
            {
                verificationReport.screenshot_status = status;
                verificationReport.screenshot_path = exists ? file : "";
                verificationReport.screenshot_error = captureError;
            }
            else
            {
                verificationViewCaptures.Add(new ReplayViewCapture
                {
                    kind = kind, status = status, path = exists ? file : "", error = captureError,
                    selected_region_index = selectedRegion, details_open = showInsights, examples_open = showExamples,
                    source_header_visible = showSourceHeader,
                    trend_mode = showInsights ? (showCalculationDetails ? "calculation" : insightsMotionTab ? "motion" : "count") : "",
                    current_window_index = CurrentWindow == null ? -1 : CurrentWindow.index,
                    reported_time_s = video == null ? -1 : video.time,
                    physical_ui_clicks_tested = false
                });
            }
            PersistVerification();
        }

        private void PersistVerification()
        {
            if (verificationReport == null) return;
            verificationReport.steps = verificationSteps.ToArray();
            verificationReport.diagnostics = verificationDiagnostics.ToArray();
            verificationReport.ui_feature_checks = verificationUiChecks.ToArray();
            verificationReport.view_captures = verificationViewCaptures.ToArray();
            verificationReport.seek_stability_checks = seekStabilityChecks.ToArray();
            verificationReport.seek_events = seekEvents.ToArray();
            verificationReport.source_id = data == null ? "" : data.source.id;
            verificationReport.verified_video_path = verifiedPath;
            verificationReport.ui_font_name = uiFontName;
            File.WriteAllText(Path.Combine(verificationDirectory, "runtime_verification.json"), JsonUtility.ToJson(verificationReport, true));
        }

        private void OnDisable()
        {
            if (verificationReport == null || verificationReport.status != "running") return;
            verificationReport.status = "interrupted";
            verificationReport.finished_utc = DateTime.UtcNow.ToString("o");
            PersistVerification();
        }
    }

    [Serializable] internal sealed class ReplayVerificationReport
    {
        public string status, started_utc, finished_utc, editor_version, verification_kind;
        public string source_id, verified_video_path, screenshot_status, screenshot_path, screenshot_error;
        public string ui_font_name;
        public string input_json_path, input_json_sha256_before, input_json_sha256_after, input_json_integrity_error;
        public bool synthetic_playback_clock, ui_button_clicks_tested;
        public ReplayVerificationStep[] steps;
        public ReplayTimingDiagnostic[] diagnostics;
        public ReplayUiFeatureCheck[] ui_feature_checks;
        public ReplayViewCapture[] view_captures;
        public ReplaySeekCheck[] seek_stability_checks;
        public ReplaySeekEvent[] seek_events;
    }
    [Serializable] internal sealed class ReplayVerificationStep
    {
        public string name, measured_utc, controller_error, controller_operation, evidence, decoded_texture_path;
        public bool passed, playing, end_event_seen;
        public double expected_time_s, actual_time_s, measured_playback_rate, requested_time_error_s, player_reported_frame_rate, player_reported_length_s;
        public ulong player_reported_frame_count;
        public int expected_window_index, current_window_index, texture_width, texture_height;
        public long actual_frame_index, last_frame_ready_index, frame_ready_events;
    }
    [Serializable] internal sealed class ReplayTimingDiagnostic
    {
        public string name, interpretation, decoded_texture_path;
        public double requested_source_pts_s, reported_unity_time_s, signed_error_s, strict_tolerance_s;
        public bool strict_time_match, source_frame_identity_verified;
    }
    [Serializable] internal sealed class ReplayUiFeatureCheck
    {
        public string name, scope, evidence, measured_utc, zone_id, traffic_state, density_level, controller_error;
        public bool passed, details_open, examples_open, motion_available;
        public int expected_region_index, selected_region_index, expected_window_index, current_window_index;
        public double expected_time_s, reported_time_s;
        public long frame_ready_events;
    }
    [Serializable] internal sealed class ReplayViewCapture
    {
        public string kind, status, path, error, trend_mode;
        public bool details_open, examples_open, source_header_visible, physical_ui_clicks_tested;
        public int selected_region_index, current_window_index;
        public double reported_time_s;
    }
}
#endif
