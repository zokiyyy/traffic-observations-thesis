using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Video;

namespace TrafficDemo
{
    [DisallowMultipleComponent]
    public sealed partial class TrafficReplayController : MonoBehaviour
    {
        private TrafficDemoData data;
        private TrafficDetectionOverlay detectionOverlay;
        private TrafficDemoCatalog catalog;
        private int sourceIndex;
        private VideoPlayer video;
        private string pathText = "", notice = "Loading offline data...", error = "", verifiedPath = "";
        private bool ready, initialFrame, seeking, seekEventSeen, seekFrameSeen, resumeAfterSeek, ended;
        private bool dragging, wasPlayingBeforeDrag, showSettings;
        private double seekTarget, dragTarget;
        private float speed = 1;
        private int loadGeneration;
        private CancellationTokenSource hashCancellation;
        private readonly Stopwatch operationClock = new Stopwatch();
        private string operation = "";
        private long seekReadyFrame = -1;
        private int seekRecoveryCount;
        private bool seekDecodeNudged, recoveringSeek;
        private double recoveryTarget;
        private bool recoveryResume;
        private long verifiedVideoSize, verifiedVideoWriteTicks;
        private double lastCompletedSeekTime = -1;

        private void Start()
        {
            Application.runInBackground = true;
            // A fixed render cap avoids macOS variable-refresh/vsync clock pacing
            // under the denser five-region dashboard. It never selects data windows.
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 60;
            LoadData();
            BeginMultiVerificationIfRequested();
#if UNITY_EDITOR
            if (catalog == null) BeginEditorVerificationIfRequested();
#endif
        }

        private void LoadData()
        {
            ResetPlayer();
            data = null;
            detectionOverlay = null;
            error = "";
#if UNITY_WEBGL && !UNITY_EDITOR
            StartCoroutine(LoadWebData());
#else
            try
            {
                string catalogPath = Path.Combine(Application.streamingAssetsPath, "five_sources/catalog.json");
                if (File.Exists(catalogPath))
                {
                    catalog = TrafficDemoCatalog.Load(catalogPath);
                    sourceIndex = Math.Max(0, Math.Min(sourceIndex, catalog.entries.Length - 1));
                    RebuildRecordingGroups();
                    data = catalog.LoadData(catalog.entries[sourceIndex]);
                }
                else { catalog = null; recordingGroups.Clear(); data = TrafficDemoData.Load(Path.Combine(Application.streamingAssetsPath, "traffic_demo.json")); }
                detectionOverlay = TrafficDetectionOverlay.Load(data.source);
                pathText = data.source.video_path ?? "";
                OpenVideo();
            }
            catch (Exception e) { Fail("Data unavailable: " + e.Message); showSettings = true; }
#endif
        }

        private void SwitchSource(int index)
        {
            if (catalog == null || index < 0 || index >= catalog.entries.Length) return;
            // Cancel hashing/seek work and detach the previous decoder before selecting new data.
            if (sourceIndex >= 0 && sourceIndex < catalog.entries.Length)
                rememberedRecording[catalog.entries[sourceIndex].label] = sourceIndex;
            sourceIndex = index;
            rememberedRecording[catalog.entries[index].label] = index;
            selectedRegion = -1;
            showInsights = showExamples = showSettings = showRecordingPicker = showEvaluation = false;
            speed = 1;
            LoadData();
        }

        private void ResetPlayer()
        {
            loadGeneration++;
            if (hashCancellation != null) { hashCancellation.Cancel(); hashCancellation.Dispose(); hashCancellation = null; }
            StopAllCoroutines();
            operationClock.Reset();
            operation = "";
            ready = initialFrame = seeking = dragging = ended = false;
            recoveringSeek = false;
            seekRecoveryCount = 0;
            verifiedPath = "";
            ReleaseDecoder();
        }

        private void ReleaseDecoder()
        {
            if (video != null)
            {
                video.prepareCompleted -= OnPrepared;
                video.errorReceived -= OnVideoError;
                video.seekCompleted -= OnSeekCompleted;
                video.frameReady -= OnFrameReady;
                video.loopPointReached -= OnEnded;
                video.Stop();
                Destroy(video);
                video = null;
            }
        }

        private void OpenVideo(bool forceVerification = false)
        {
            if (data == null) { LoadData(); return; }
            ResetPlayer();
            error = "";
#if UNITY_WEBGL && !UNITY_EDITOR
            OpenWebVideo();
#else
            try
            {
                string path = ResolveVideoPath(pathText);
                FileInfo cachedInfo;
                if (!forceVerification && TrafficVideoVerificationCache.TryGet(path, data.source.video_sha256, out cachedInfo))
                {
                    UnityEngine.Debug.Log("Traffic video verification cache hit: " + data.source.id);
                    verifiedPath = path;
                    verifiedVideoSize = cachedInfo.Length;
                    verifiedVideoWriteTicks = cachedInfo.LastWriteTimeUtc.Ticks;
                    notice = "Previously verified video unchanged. Preparing the decoder...";
                    PrepareVerifiedDecoder();
                    return;
                }
                UnityEngine.Debug.Log("Traffic video full SHA256 check: " + data.source.id);
                hashCancellation = new CancellationTokenSource();
                notice = "Verifying the video SHA256... The file is read without copying it.";
                operation = "hash";
                operationClock.Restart();
                StartCoroutine(VerifyAndPrepare(path, loadGeneration, hashCancellation.Token));
            }
            catch (Exception e) { Fail("Video path unavailable: " + e.Message); showSettings = true; }
#endif
        }

        public static string ResolveVideoPath(string input)
        {
            if (string.IsNullOrWhiteSpace(input)) throw new IOException("Enter the path to a local MP4 file.");
            input = input.Trim();
            if (input.StartsWith("file:", StringComparison.OrdinalIgnoreCase))
            {
                var uri = new Uri(input, UriKind.Absolute);
                if (!uri.IsFile || (!string.IsNullOrEmpty(uri.Host) && uri.Host != "localhost"))
                    throw new IOException("Only local files are supported; remote addresses are not accepted.");
                input = uri.LocalPath;
            }
            else if (input.Contains("://")) throw new IOException("This offline demo does not request network videos.");
            if (input.StartsWith("\\\\", StringComparison.Ordinal) || input.StartsWith("//", StringComparison.Ordinal))
                throw new IOException("Select a local file rather than a network share.");
            string full;
            if (Path.IsPathRooted(input)) full = Path.GetFullPath(input);
            else
            {
                string root = Path.GetFullPath(Application.streamingAssetsPath).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                full = Path.GetFullPath(Path.Combine(root, input));
                var comparison = Application.platform == RuntimePlatform.WindowsPlayer || Application.platform == RuntimePlatform.WindowsEditor
                    ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                if (!full.StartsWith(root, comparison)) throw new IOException("Relative paths must remain within StreamingAssets. Use an absolute path for an external video.");
            }
            if (!string.Equals(Path.GetExtension(full), ".mp4", StringComparison.OrdinalIgnoreCase))
                throw new IOException("Select the exported MP4 file.");
            if (!File.Exists(full)) throw new FileNotFoundException("File not found. If the video was moved, update its path here.", full);
            return full;
        }

        private IEnumerator VerifyAndPrepare(string path, int generation, CancellationToken token)
        {
            var before = new FileInfo(path);
            Task<string> job = Task.Run(() => HashFile(path, token), token);
            while (!job.IsCompleted) yield return null;
            if (generation != loadGeneration || token.IsCancellationRequested) yield break;
            if (job.IsFaulted) { Fail("Cannot read the video: " + job.Exception.GetBaseException().Message); yield break; }
            if (job.IsCanceled) yield break;
            if (!string.Equals(job.Result, data.source.video_sha256, StringComparison.OrdinalIgnoreCase))
            {
                Fail("Video SHA256 does not match the data. Playback stopped. Select the complete export used by this offline summary.");
                showSettings = true;
                yield break;
            }
            var verifiedInfo = new FileInfo(path);
            if (!TrafficVideoVerificationCache.SameFile(before, verifiedInfo))
            {
                Fail("The video changed during verification. Select the file again to retry.");
                yield break;
            }
            verifiedPath = path;
            verifiedVideoSize = verifiedInfo.Length;
            verifiedVideoWriteTicks = verifiedInfo.LastWriteTimeUtc.Ticks;
            UnityEngine.Debug.Log("Traffic video SHA256 verified in " + operationClock.Elapsed.TotalSeconds.ToString("F2") + " s: " + data.source.id);
            TrafficVideoVerificationCache.Store(path, job.Result, verifiedInfo);
            PrepareVerifiedDecoder();
        }

        private void PrepareVerifiedDecoder()
        {
            video = gameObject.AddComponent<VideoPlayer>();
            video.playOnAwake = false;
            video.source = VideoSource.Url;
            video.renderMode = VideoRenderMode.APIOnly;
            video.audioOutputMode = VideoAudioOutputMode.None;
            // This silent offline player follows unscaled time, not the audio DSP clock
            // or gameplay scaling. State selection still uses decoded video time/PTS.
            video.timeUpdateMode = VideoTimeUpdateMode.UnscaledGameTime;
            if (video.canSetSkipOnDrop) video.skipOnDrop = true;
            video.isLooping = false;
            video.waitForFirstFrame = true;
            video.sendFrameReadyEvents = true;
            video.prepareCompleted += OnPrepared;
            video.errorReceived += OnVideoError;
            video.seekCompleted += OnSeekCompleted;
            video.frameReady += OnFrameReady;
            video.loopPointReached += OnEnded;
#if UNITY_WEBGL && !UNITY_EDITOR
            video.url = verifiedPath;
            notice = recoveringSeek ? "Recovering the selected video moment..." : "Loading the hosted video...";
#else
            video.url = new Uri(verifiedPath).AbsoluteUri;
            notice = recoveringSeek ? "Recovering the selected video moment..." : "Video content verified. Preparing the decoder...";
#endif
            operation = "prepare";
            operationClock.Restart();
            video.Prepare();
        }

        private static string HashFile(string path, CancellationToken token)
        {
            using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, FileOptions.SequentialScan))
            using (var hash = SHA256.Create())
            {
                byte[] buffer = new byte[1024 * 1024];
                int read;
                while ((read = file.Read(buffer, 0, buffer.Length)) > 0)
                {
                    token.ThrowIfCancellationRequested();
                    hash.TransformBlock(buffer, 0, read, buffer, 0);
                }
                hash.TransformFinalBlock(new byte[0], 0, 0);
                return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private void OnPrepared(VideoPlayer player)
        {
            if (player != video || !string.IsNullOrEmpty(error)) return;
            if (!TrafficDemoData.Finite(player.length) || player.length <= 0 || Math.Abs(player.length - data.source.duration_s) > 0.5)
            { Fail("Video duration does not match the data timeline. No time stretching or frame-rate conversion is applied; check the export."); return; }
            if ((data.source.video_width > 0 && player.width != (uint)data.source.video_width) ||
                (data.source.video_height > 0 && player.height != (uint)data.source.video_height))
            { Fail("The decoded video dimensions do not match the data. Check the export."); return; }
            ready = true;
            initialFrame = true;
            operation = "first_frame";
            operationClock.Restart();
            notice = "Decoding the first frame...";
            if (player.canSetPlaybackSpeed) player.playbackSpeed = 1;
            // Decode a real first frame, then pause. A prepared decoder need not yet have a texture.
            player.Play();
        }

        private void OnFrameReady(VideoPlayer player, long frameIndex)
        {
            if (player != video || !string.IsNullOrEmpty(error)) return;
            if (seeking && player == multiVerificationSuppressedSeekPlayer) return;
#if UNITY_EDITOR
            verificationDecodedFrames++;
            verificationLastFrameIndex = frameIndex;
#endif
            if (initialFrame)
            {
                initialFrame = false;
                player.Pause();
                operation = "";
                notice = "Ready · Press Play";
                if (recoveringSeek)
                {
                    recoveringSeek = false;
                    BeginSeek(recoveryTarget, recoveryResume);
                    return;
                }
                if (player.canSetPlaybackSpeed) player.playbackSpeed = speed;
            }
            // Unity's VFR navigation timestamp can be quantized; the requested source PTS
            // is not proof of exact container-frame identity. Inspect actual texture for that.
            if (seeking)
            {
#if UNITY_EDITOR
                if (player == verificationSuppressedSeekPlayer) { TraceSeek("test_suppressed_frame_ready"); return; }
#endif
                seekReadyFrame = frameIndex;
                TraceSeek("frame_ready");
                TryFinishSeek();
            }
        }

        private void OnSeekCompleted(VideoPlayer player)
        {
            if (player != video || !seeking) return;
            if (player == multiVerificationSuppressedSeekPlayer) return;
#if UNITY_EDITOR
            if (player == verificationSuppressedSeekPlayer) { TraceSeek("test_suppressed_seek_completed"); return; }
#endif
            seekEventSeen = true;
            TraceSeek("seek_completed");
            TryFinishSeek();
            // A paused seek within the current frame can complete without a new
            // frameReady event on macOS. Update gives that decoder a bounded
            // playback pulse if necessary; Pause alone did not produce a frame
            // in the reproduced case on this Mac.
        }

        private void TryFinishSeek()
        {
            if (!seeking || video == null || !seekEventSeen) return;
            // Frame callbacks can precede the settled presentation time. Recheck
            // in Update as well; never use the requested time to select data.
            seekFrameSeen = seekReadyFrame >= 0 && video.frame == seekReadyFrame && video.texture != null &&
                TrafficDemoData.Finite(video.time) && Math.Abs(video.time - seekTarget) <= 0.1;
            if (!seekFrameSeen) return;
            lastCompletedSeekTime = video.time;
            TraceSeek("accepted");
            seeking = false;
            operation = "";
            notice = "Seek completed";
            if (video.canSetPlaybackSpeed) video.playbackSpeed = speed;
            if (resumeAfterSeek) video.Play(); else video.Pause();
        }

        private void SeekTo(double seconds, bool resume)
        {
            if (!CanControl || !video.canSetTime) return;
            if (!TrafficDemoData.Finite(seconds)) return;
            seekRecoveryCount = 0;
            BeginSeek(seconds, resume);
        }

        private void BeginSeek(double seconds, bool resume)
        {
            double lastTime = data.source.last_frame_time_s;
            if (seconds >= lastTime && video.length + 1e-7 < lastTime)
            { notice = "The player-reported range does not cover the requested end position. Check the export timeline."; return; }
            seekTarget = SourcePresentationTime(Math.Max(0, Math.Min(seconds, Math.Min(lastTime, video.length))));
            if (Math.Abs(video.time - seekTarget) < 1e-5)
            {
                lastCompletedSeekTime = video.time;
                if (video.canSetPlaybackSpeed) video.playbackSpeed = speed;
                if (resume) video.Play(); else video.Pause(); return;
            }
            video.Pause();
            if (video.canSetPlaybackSpeed) video.playbackSpeed = 1;
            ended = false;
            resumeAfterSeek = resume;
            seeking = true;
            seekFrameSeen = seekEventSeen = false;
            seekReadyFrame = -1;
            seekDecodeNudged = false;
            operation = "seek";
            operationClock.Restart();
            notice = "Seeking " + Clock(seekTarget) + " · Window state is hidden until decoding completes";
            TraceSeek("requested");
            video.time = seekTarget;
        }

        private void UpdateScrub(double target)
        {
            if (!CanControl || !video.canSetTime || !TrafficDemoData.Finite(target)) return;
            if (!dragging) { wasPlayingBeforeDrag = video.isPlaying; video.Pause(); dragging = true; }
            dragTarget = Math.Max(0, Math.Min(target, data.source.duration_s));
        }

        private void CommitScrub()
        {
            if (!dragging) return;
            dragging = false;
            SeekTo(dragTarget, wasPlayingBeforeDrag);
        }

        private void OnApplicationFocus(bool focused)
        {
            if (focused || !dragging) return;
            // Losing the mouse-up outside this app must not leave a permanent
            // dragging state. Cancel the uncommitted preview at its old position.
            dragging = false;
            if (CanControl && wasPlayingBeforeDrag) video.Play();
        }

        private void RecoverSeek()
        {
            if (seekRecoveryCount >= 1)
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                Fail("Video navigation could not recover. Open Methods & Data and reload the hosted video.");
#else
                Fail("Video navigation could not recover. Open Methods & Data and use Verify and load.");
#endif
                return;
            }
            try
            {
#if !UNITY_WEBGL || UNITY_EDITOR
                var info = new FileInfo(verifiedPath);
                if (!info.Exists || info.Length != verifiedVideoSize || info.LastWriteTimeUtc.Ticks != verifiedVideoWriteTicks)
                { Fail("The video file changed or is unavailable. Use Verify and load to check it again."); return; }
#endif
                TraceSeek("reprepare_once");
                seekRecoveryCount++;
                recoveryTarget = seekTarget;
                recoveryResume = resumeAfterSeek;
                recoveringSeek = true;
                ready = initialFrame = seeking = dragging = ended = false;
                // Isolate old asynchronous callbacks; do not stop the verification
                // coroutine, rehash the same unchanged file, or queue more seeks.
                ReleaseDecoder();
                PrepareVerifiedDecoder();
            }
            catch (Exception e) { Fail("Video navigation recovery failed: " + e.Message); }
        }

        private void OnEnded(VideoPlayer player)
        {
            if (player != video) return;
            ended = true;
            player.Pause();
            notice = "Replay ended · Incomplete final evidence is not classified";
        }
        private void OnVideoError(VideoPlayer player, string message)
        {
            if (player != video) return;
#if UNITY_WEBGL && !UNITY_EDITOR
            Fail("Video decoding failed: " + message + ". Check the hosted MP4 and browser codec support.");
#else
            Fail("Video decoding failed: " + message + ". Reload the video or check local codec support.");
#endif
        }
        private void Fail(string message)
        {
            TraceSeek("failure: " + message);
            error = message;
            notice = message;
            ready = initialFrame = seeking = dragging = false;
            recoveringSeek = false;
            operation = "";
            if (video != null) video.Pause();
        }
        private bool CanControl { get { return ready && !initialFrame && !seeking && video != null && string.IsNullOrEmpty(error); } }
        private bool CanShowState { get { return CanControl && !dragging; } }
        // Resolve the native presentation timestamp through the saved real MP4 PTS table.
        // VideoPlayer.frame is NOT an original VFR frame ordinal and is never divided by FPS.
        private double SourcePresentationTime(double nativeTime)
        {
            double bounded = Math.Max(0, Math.Min(nativeTime, data.source.last_frame_time_s));
            return data.source.frame_pts_s == null || data.source.frame_pts_s.Length == 0 ? bounded : data.SourcePtsAt(bounded);
        }
        private double PresentedTime { get { return CanShowState && TrafficDemoData.Finite(video.time) ? SourcePresentationTime(video.time) : -1; } }
        private DemoWindow CurrentObservingWindow
        {
            get
            {
                double time = PresentedTime;
                if (data == null || time < 0) return null;
                if (ended) return data.windows[data.windows.Length - 1];
                int index = data.WindowAt(time);
                return index < 0 ? null : data.windows[index];
            }
        }
        private DemoWindow CurrentWindow
        {
            get
            {
                double time = PresentedTime;
                if (data == null || time < 0 || ended) return null;
                // The Mac decoder's reported time is quantized. Suppress confirmation near
                // window edges instead of claiming exact frame identity from that clock.
                const double boundaryGuard = .1;
                if (data.WindowAt(Math.Max(0, time - boundaryGuard)) !=
                    data.WindowAt(Math.Min(data.source.duration_s - 1e-7, time + boundaryGuard))) return null;
                // An aggregate exists only after its half-open observation interval ended.
                // Missing/held/partial current intervals invalidate previous evidence too.
                int index = data.ConfirmedWindowAt(time);
                return index < 0 ? null : data.windows[index];
            }
        }
        private void Update()
        {
            // Watchdogs use elapsed real time only for errors, never for selecting traffic data.
            if (seeking) TryFinishSeek();
            if (operation == "seek" && operationClock.Elapsed.TotalSeconds > 8)
                RecoverSeek();
            else if (operation == "seek" && seekEventSeen && !seekDecodeNudged && operationClock.Elapsed.TotalSeconds > .25)
            {
                seekDecodeNudged = true;
                TraceSeek("decode_pulse");
                // Decode a real frame at 1x. The frame callback completes the
                // seek and restores the selected speed and pause/play intent.
                // Traffic state stays hidden until that actual frame arrives.
                video.Play();
            }
            else if ((operation == "prepare" || operation == "first_frame") && recoveringSeek && operationClock.Elapsed.TotalSeconds > 15)
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                Fail("Video navigation recovery timed out. Reload the hosted video in Methods & Data.");
#else
                Fail("Video navigation recovery timed out. Use Verify and load in Methods & Data.");
#endif
            }
            else if ((operation == "prepare" || operation == "first_frame") && operationClock.Elapsed.TotalSeconds > 60)
                Fail("Video preparation timed out. Check the format and file permissions, then reload.");
        }
        private static string Clock(double seconds)
        {
            if (!TrafficDemoData.Finite(seconds) || seconds < 0) return "--:--";
            var t = TimeSpan.FromSeconds(seconds);
            return ((int)t.TotalHours > 0 ? ((int)t.TotalHours).ToString("00") + ":" : "") + t.Minutes.ToString("00") + ":" + t.Seconds.ToString("00");
        }
        private void OnDestroy()
        {
            ResetPlayer();
            ReleaseUi();
        }
    }
}
