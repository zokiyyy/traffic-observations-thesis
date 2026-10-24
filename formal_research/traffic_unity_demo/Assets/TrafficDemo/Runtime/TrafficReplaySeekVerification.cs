using System;
using UnityEngine;
#if UNITY_EDITOR
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
#endif

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        private void TraceSeek(string phase)
        {
#if UNITY_EDITOR
            if (seekEvents.Count >= 2000) return;
            seekEvents.Add(new ReplaySeekEvent {
                phase = phase, utc = DateTime.UtcNow.ToString("o"), target = seekTarget,
                actual = video == null ? -1 : video.time, frame = video == null ? -1 : video.frame,
                elapsed = operationClock.Elapsed.TotalSeconds, speed = speed,
                seek_event = seekEventSeen, frame_event = seekFrameSeen,
                playing = video != null && video.isPlaying
            });
#endif
        }

#if UNITY_EDITOR
        private readonly List<ReplaySeekCheck> seekStabilityChecks = new List<ReplaySeekCheck>();
        private readonly List<ReplaySeekEvent> seekEvents = new List<ReplaySeekEvent>();
        private UnityEngine.Video.VideoPlayer verificationSuppressedSeekPlayer;

        private IEnumerator VerifySeekStability()
        {
            speed = 1;
            video.playbackSpeed = 1;
            SeekTo(0, false);
            yield return WaitForVerificationReady(35);
            if (!VerificationFrameValid()) yield break;
            yield return CheckStableSeek("subframe_seek_from_paused_zero", .005, false);
            if (verificationFailed) yield break;
            yield return CheckStableSeek("subframe_seek_15ms", .015, false);
            if (verificationFailed) yield break;
            yield return CheckStableSeek("subframe_seek_25ms", .025, false);
            if (verificationFailed) yield break;
            float[] rates = { .5f, 1f, 2f, 4f };
            foreach (float rate in rates)
            {
                speed = rate;
                video.playbackSpeed = rate;
                yield return CheckStableSeek("paused_seek_" + rate + "x", 110.231, false);
                if (verificationFailed) yield break;
                video.Play();
                var settle = Stopwatch.StartNew();
                while (settle.Elapsed.TotalSeconds < .3) yield return null;
                yield return CheckStableSeek("playing_seek_" + rate + "x", 930.517, true);
                if (verificationFailed) yield break;
                video.Pause();
                yield return CheckStableSeek("backwards_seek_" + rate + "x", 20.203, false);
                if (verificationFailed) yield break;
                video.Play();
                yield return CheckStableSeek("slider_commit_" + rate + "x", 1800.217, true, true);
                if (verificationFailed) yield break;
            }
            speed = 1;
            video.playbackSpeed = 1;
            video.Pause();
            yield return CheckStableSeek("same_position_noop", video.time, false);
            if (verificationFailed) yield break;

            double beforeDrag = video.time;
            UpdateScrub(800.2);
            bool hidden = dragging && !CanShowState && !video.isPlaying;
            OnApplicationFocus(false);
            AddSeekCheck("drag_focus_loss_cancels_preview", hidden && !dragging && VerificationFrameValid() &&
                Math.Abs(video.time - beforeDrag) < .001, beforeDrag, 0,
                "Actual slider/focus handlers cancel the preview without changing the presented timestamp; no mouse event is synthesized.");
            if (verificationFailed) yield break;

            SeekTo(930.517, false);
            bool hiddenDuringSeek = !CanShowState && seeking;
            SeekTo(20.203, false);
            bool secondRejected = Math.Abs(seekTarget - 930.517) < 1e-7;
            yield return WaitForVerificationReady(35);
            AddSeekCheck("busy_seek_does_not_queue_a_second_target", hiddenDuringSeek && secondRejected &&
                VerificationFrameValid() && Math.Abs(video.time - 930.517) <= .1, 930.517, 0,
                "An immediate second request is rejected while busy; no mixed callback state or stale traffic window is displayed.");
            if (verificationFailed) yield break;

            verificationSuppressedSeekPlayer = video;
            yield return CheckStableSeek("recover_missing_callbacks_once", 110.231, false);
            bool recoveredOnce = seekRecoveryCount == 1 && video != verificationSuppressedSeekPlayer && VerificationFrameValid();
            verificationSuppressedSeekPlayer = null;
            AddSeekCheck("recovery_replaces_only_the_decoder", recoveredOnce, 110.231, 0,
                "Fault injection drops seek/frame callbacks from the first decoder only. The replacement decodes the real unchanged video and completes the same target.");
        }

        private IEnumerator CheckStableSeek(string name, double target, bool resume, bool scrub = false)
        {
            var elapsed = Stopwatch.StartNew();
            bool dragHidden = true;
            if (scrub)
            {
                UpdateScrub(target - 25);
                UpdateScrub(target);
                dragHidden = dragging && !CanShowState && !video.isPlaying;
                CommitScrub();
            }
            else SeekTo(target, resume);
            yield return WaitForVerificationReady(35);
            bool passed = dragHidden && VerificationFrameValid() && !seeking && Math.Abs(lastCompletedSeekTime - target) <= .1 &&
                video.isPlaying == resume && Math.Abs(video.playbackSpeed - speed) < .01 && CurrentWindow.index == data.WindowAt(video.time);
            AddSeekCheck(name, passed, target, elapsed.Elapsed.TotalSeconds,
                "Real decoded video and the production seek/slider controller; completed timestamp, resume mode and selected speed must match. No synthetic clock or physical mouse input.");
        }

        private void AddSeekCheck(string name, bool passed, double target, double elapsed, string evidence)
        {
            seekStabilityChecks.Add(new ReplaySeekCheck {
                name = name, passed = passed, target = target, actual = video == null ? -1 : video.time,
                completed_seek_time = lastCompletedSeekTime, recovery_count = seekRecoveryCount,
                elapsed = elapsed, selected_speed = speed,
                playing = video != null && video.isPlaying, current_window = CurrentWindow == null ? -1 : CurrentWindow.index,
                error = error, evidence = evidence
            });
            verificationFailed |= !passed;
            PersistVerification();
            UnityEngine.Debug.Log("TrafficDemo seek stability " + name + "=" + passed + "; error=" + error);
        }
#endif
    }

#if UNITY_EDITOR
    [Serializable] internal sealed class ReplaySeekCheck
    {
        public string name, error, evidence;
        public bool passed, playing;
        public double target, actual, elapsed, completed_seek_time;
        public float selected_speed;
        public int current_window, recovery_count;
    }
    [Serializable] internal sealed class ReplaySeekEvent
    {
        public string phase, utc;
        public double target, actual, elapsed;
        public long frame;
        public float speed;
        public bool seek_event, frame_event, playing;
    }
#endif
}
