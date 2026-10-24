using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        private const float CanvasWidth = 1800, CanvasHeight = 1064;
        private const float RuleNoticeHeight = 64;
        private readonly Color background = Hex("F4F6F9"), ink = Hex("18263A"), muted = Hex("718096"), teal = Hex("187C80"), line = Hex("E3E8EF");
        private GUIStyle titleStyle, headingStyle, bodyStyle, smallStyle, tinyStyle, metricStyle, buttonStyle, selectedButtonStyle, inputStyle, roundStyle, sliderStyle, thumbStyle;
        private GUIStyle strongBodyStyle, activeSmallStyle, smallInkStyle, exampleTimeStyle, logoStyle, centerBodyStyle, rightSmallStyle, chipStyle, tickLeftStyle, tickCenterStyle, tickRightStyle;
        private GUIStyle compactMetricStyle, compactStateStyle, sourceButtonStyle, sourceSelectedButtonStyle;
        private GUIStyle detectionLabelStyle;
        private Font uiFont;
        private string uiFontName = "Unity built-in IMGUI";
        private readonly List<Texture2D> uiTextures = new List<Texture2D>();
        private Vector2 notesScroll, uiPointer;
        private Vector2 recordingPickerScroll;
        private bool showEvaluation;
        private bool showSourceHeader;
        private bool showRecordingPicker;
        private sealed class RecordingGroup
        {
            internal string camera;
            internal readonly List<int> sources = new List<int>();
        }
        private readonly List<RecordingGroup> recordingGroups = new List<RecordingGroup>();
        private readonly Dictionary<string, int> rememberedRecording = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Rect videoCard = new Rect(32, 106, 1118, 648);
        private readonly Rect regions = new Rect(1174, 106, 594, 648);

        private void EnsureUi()
        {
            if (bodyStyle != null) return;
            uiFont = GUI.skin.font;
            titleStyle = TextStyle(31, ink, true);
            headingStyle = TextStyle(21, ink, true);
            bodyStyle = TextStyle(18, ink);
            smallStyle = TextStyle(15, muted);
            tinyStyle = TextStyle(13, muted);
            metricStyle = TextStyle(43, ink, true);
            strongBodyStyle = TextStyle(18, ink, true);
            compactMetricStyle = TextStyle(34, ink, true);
            compactStateStyle = TextStyle(16, ink, true);
            detectionLabelStyle = TextStyle(10, Color.white, true);
            detectionLabelStyle.wordWrap = false;
            activeSmallStyle = TextStyle(15, teal, true);
            smallInkStyle = TextStyle(15, ink);
            exampleTimeStyle = TextStyle(17, teal, true);
            logoStyle = TextStyle(22, Color.white, true); logoStyle.alignment = TextAnchor.MiddleCenter;
            centerBodyStyle = new GUIStyle(bodyStyle) { alignment = TextAnchor.MiddleCenter };
            rightSmallStyle = new GUIStyle(smallStyle) { alignment = TextAnchor.MiddleRight };
            chipStyle = new GUIStyle(smallStyle) { alignment = TextAnchor.MiddleCenter, fontStyle = FontStyle.Bold, wordWrap = false };
            tickLeftStyle = new GUIStyle(tinyStyle) { alignment = TextAnchor.MiddleLeft };
            tickCenterStyle = new GUIStyle(tinyStyle) { alignment = TextAnchor.MiddleCenter };
            tickRightStyle = new GUIStyle(tinyStyle) { alignment = TextAnchor.MiddleRight };
            roundStyle = new GUIStyle { border = new RectOffset(12, 12, 12, 12) };
            roundStyle.normal.background = RoundedTexture(Color.white);
            buttonStyle = new GUIStyle(GUI.skin.button) {
                font = uiFont, fontSize = 16, alignment = TextAnchor.MiddleCenter,
                border = new RectOffset(12, 12, 12, 12), padding = new RectOffset(10, 10, 2, 2)
            };
            buttonStyle.normal.background = RoundedTexture(Hex("EEF2F6"));
            buttonStyle.hover.background = RoundedTexture(Hex("E1E9F0"));
            buttonStyle.active.background = RoundedTexture(Hex("D1DFE7"));
            buttonStyle.normal.textColor = buttonStyle.hover.textColor = buttonStyle.active.textColor = ink;
            selectedButtonStyle = new GUIStyle(buttonStyle);
            selectedButtonStyle.normal.background = RoundedTexture(teal);
            selectedButtonStyle.hover.background = RoundedTexture(Hex("116B70"));
            selectedButtonStyle.active.background = RoundedTexture(Hex("0B5C62"));
            selectedButtonStyle.normal.textColor = selectedButtonStyle.hover.textColor = selectedButtonStyle.active.textColor = Color.white;
            sourceButtonStyle = new GUIStyle(buttonStyle) { fontSize = 15 };
            sourceSelectedButtonStyle = new GUIStyle(selectedButtonStyle) { fontSize = 15 };
            inputStyle = new GUIStyle(GUI.skin.textField) { font = uiFont, fontSize = 17, padding = new RectOffset(12, 12, 12, 10) };
            inputStyle.normal.background = RoundedTexture(Hex("EEF2F6"));
            inputStyle.normal.textColor = ink;
            inputStyle.focused.textColor = ink;
            sliderStyle = new GUIStyle(GUI.skin.horizontalSlider) { fixedHeight = 6, margin = new RectOffset(0, 0, 9, 0), border = new RectOffset(3, 3, 3, 3) };
            sliderStyle.normal.background = RoundedTexture(Hex("DDE5EC"));
            thumbStyle = new GUIStyle(GUI.skin.horizontalSliderThumb) { fixedWidth = 14, fixedHeight = 20, border = new RectOffset(6, 6, 6, 6) };
            thumbStyle.normal.background = RoundedTexture(teal);
            thumbStyle.hover.background = thumbStyle.active.background = RoundedTexture(Hex("116B70"));
        }

        private GUIStyle TextStyle(int size, Color color, bool bold = false)
        {
            var style = new GUIStyle(GUI.skin.label) { font = uiFont, fontSize = size, wordWrap = true,
                fontStyle = bold ? FontStyle.Bold : FontStyle.Normal, padding = new RectOffset(0, 0, 0, 0) };
            style.normal.textColor = color;
            style.hover.textColor = style.active.textColor = style.focused.textColor = color;
            style.onNormal.textColor = style.onHover.textColor = style.onActive.textColor = style.onFocused.textColor = color;
            return style;
        }
        private Texture2D RoundedTexture(Color color)
        {
            const int size = 32;
            const float radius = 12;
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false);
            var pixels = new Color[size * size];
            for (int y = 0; y < size; y++) for (int x = 0; x < size; x++)
            {
                float dx = Mathf.Max(radius - (x + .5f), x + .5f - (size - radius), 0);
                float dy = Mathf.Max(radius - (y + .5f), y + .5f - (size - radius), 0);
                Color c = color;
                c.a *= Mathf.Clamp01(radius + .5f - Mathf.Sqrt(dx * dx + dy * dy));
                pixels[y * size + x] = c;
            }
            texture.SetPixels(pixels); texture.Apply(); texture.wrapMode = TextureWrapMode.Clamp;
            texture.filterMode = FilterMode.Bilinear; uiTextures.Add(texture); return texture;
        }
        private static Color Hex(string value)
        {
            Color color;
            return ColorUtility.TryParseHtmlString(value.StartsWith("#") ? value : "#" + value, out color) ? color : Color.gray;
        }
        private static void Fill(Rect rect, Color color)
        {
            Color before = GUI.color; GUI.color = color;
            GUI.DrawTexture(rect, Texture2D.whiteTexture); GUI.color = before;
        }
        private void Round(Rect rect, Color color)
        {
            if (Event.current.type != EventType.Repaint) return;
            Color before = GUI.color; GUI.color = color;
            roundStyle.Draw(rect, false, false, false, false); GUI.color = before;
        }
        private void Card(Rect rect)
        {
            Round(new Rect(rect.x, rect.y + 3, rect.width, rect.height), Hex("E8EDF3"));
            Round(rect, Color.white);
        }
        private void Label(Rect rect, string text, GUIStyle style, string tooltip = "")
        { GUI.Label(rect, new GUIContent(text ?? ""), style); }
        private void Chip(Rect rect, string text, Color fill, Color textColor)
        {
            Round(rect, fill);
            chipStyle.normal.textColor = chipStyle.hover.textColor = chipStyle.active.textColor = chipStyle.focused.textColor = textColor;
            Label(rect, text, chipStyle);
        }

        private void OnGUI()
        {
            EnsureUi();
            // The opt-in engine suite owns the player until completion. Ignore desktop
            // input during it, while keeping screenshots styled like the normal UI.
            if (multiReport != null && multiReport.status == "running" && (Event.current.isMouse || Event.current.isKey))
                Event.current.Use();
            bool originalEnabled = GUI.enabled;
#if UNITY_EDITOR
            // Isolate the opt-in engine checks from live mouse input. Normal replay remains interactive.
            if (verificationReport != null && verificationReport.status == "running" && !verificationChecksFinished) GUI.enabled = false;
#endif
            Fill(new Rect(0, 0, Screen.width, Screen.height), background);
            Matrix4x4 oldMatrix = GUI.matrix;
            float scale = Mathf.Min(Screen.width / CanvasWidth, Screen.height / CanvasHeight);
            Vector2 offset = new Vector2((Screen.width - CanvasWidth * scale) / 2, (Screen.height - CanvasHeight * scale) / 2);
            uiPointer = (Event.current.mousePosition - offset) / scale;
            GUI.matrix = Matrix4x4.TRS(offset, Quaternion.identity, new Vector3(scale, scale, 1));
            bool contentEnabled = GUI.enabled;
            if (showExamples || showRecordingPicker) GUI.enabled = false;
            DrawHeader();
            if (showSettings) { DrawSettings(); GUI.matrix = oldMatrix; GUI.enabled = originalEnabled; return; }
            if (showExamples || showRecordingPicker) GUI.enabled = false;
            DrawRecordingRuleNotice();
            // Reserve a full row for the rule explanation without compressing cards or details.
            Matrix4x4 headerMatrix = GUI.matrix;
            GUI.matrix = headerMatrix * Matrix4x4.Translate(new Vector3(0, RuleNoticeHeight, 0));
            uiPointer.y -= RuleNoticeHeight;
            DrawVideoPanel();
            if (showEvaluation) DrawEvaluationPanel();
            else if (showInsights) DrawInsightsPanel(CurrentWindow); else DrawCards(CurrentWindow);
            DrawTimeline();
            Label(new Rect(36, 974, 1330, 21), "Model estimates · Relative to each region & recording · No validated congestion ranking", smallStyle);
            string totals = recordingGroups.Count + " CAMERAS / " + (catalog == null ? 1 : catalog.entries.Length) + " RECORDINGS";
            Label(new Rect(1480, 974, 288, 21), totals, tinyStyle);
            GUI.matrix = headerMatrix;
            uiPointer.y += RuleNoticeHeight;
            GUI.enabled = contentEnabled;
            if (showExamples) DrawExamplesPanel();
            else if (showRecordingPicker) DrawRecordingPicker();
            GUI.matrix = oldMatrix;
            GUI.enabled = originalEnabled;
        }

        private void DrawRecordingRuleNotice()
        {
            string text = "Select a recording to see its state-display rule.";
            if (data != null && data.windows != null && data.windows.Length > 0 &&
                data.windows[0].zones != null && data.windows[0].zones.Length > 0)
            {
                // Use the same saved output layer as DisplayState, rather than camera name or selection order.
                bool stricter = !string.IsNullOrEmpty(data.windows[0].zones[0].evidence_state);
                text = stricter
                    ? "Stricter rule: if motion evidence is insufficient, the main state is Unknown; any sustained high-count hint is noted separately."
                    : "Original rule: a sustained high-count hint can appear even when motion evidence is insufficient.";
            }
            Round(new Rect(32, 106, 1736, 52), Hex("E5F2F1"));
            Label(new Rect(48, 116, 1704, 34), text, smallInkStyle);
        }

        private string SourceLabel
        {
            get
            {
                if (catalog != null && catalog.entries != null && sourceIndex >= 0 && sourceIndex < catalog.entries.Length)
                    return catalog.entries[sourceIndex].label;
                return data == null ? "No source loaded" : data.source.id;
            }
        }

        private void DrawHeader()
        {
            Round(new Rect(32, 24, 48, 48), teal);
            Label(new Rect(32, 24, 48, 48), "T", logoStyle);
            Label(new Rect(96, 13, 920, 41), "Traffic Observation", titleStyle);
            Chip(new Rect(1118, 28, 110, 35), "OFFLINE", Hex("E5F2F1"), teal);
            if (GUI.Button(new Rect(1240, 25, 158, 44), "Evaluation", showEvaluation ? selectedButtonStyle : buttonStyle))
            { showExamples = showRecordingPicker = showSettings = false; showEvaluation = !showEvaluation; }
            if (GUI.Button(new Rect(1408, 25, 140, 44), "Examples", buttonStyle))
            { showEvaluation = showSettings = showRecordingPicker = false; showExamples = true; }
            if (GUI.Button(new Rect(1558, 25, 210, 44), "Methods & Data", buttonStyle))
            { showEvaluation = showExamples = showRecordingPicker = false; showSettings = !showSettings; }
            if (catalog != null && catalog.entries != null && catalog.entries.Length > 0 && recordingGroups.Count > 0)
            {
                RecordingGroup active = ActiveRecordingGroup();
                if (active != null)
                {
                    string chosen = RecordingTime(catalog.entries[sourceIndex].id, false);
                    string button = "Video time: " + chosen + "  (" + active.sources.Count + " videos)";
                    if (GUI.Button(new Rect(642, 22, 418, 36), new GUIContent(button, "Choose another video recorded at this camera"), sourceSelectedButtonStyle))
                    { showEvaluation = showSettings = false; recordingPickerScroll = Vector2.zero; showRecordingPicker = true; }
                }
                int tabs = recordingGroups.Count;
                float width = Mathf.Min(192, 1008f / tabs);
                for (int i = 0; i < tabs; i++)
                {
                    RecordingGroup group = recordingGroups[i];
                    int index;
                    if (!rememberedRecording.TryGetValue(group.camera, out index) || !group.sources.Contains(index))
                        index = group.sources[0];
                    if (GUI.Button(new Rect(96 + i * width, 66, width - 8, 32),
                        new GUIContent(group.camera, group.sources.Count + " recording(s) at this camera"),
                        active == group ? sourceSelectedButtonStyle : sourceButtonStyle) && index != sourceIndex)
                    {
                        // Switching clears and rebuilds recordingGroups in the browser.
                        // Leave this loop before it reads the old count with new contents.
                        SwitchSource(index);
                        break;
                    }
                }
            }
            string recorded = data == null || data.source == null ? "No recording selected" :
                "Recorded " + RecordingTime(data.source.id, true);
            string split = data == null || data.source == null ? "" : data.source.split;
            string duration = data != null && split == "Train" ? "  /  " + data.source.duration_s.ToString("0.###", CultureInfo.InvariantCulture) + " s" : "";
            Label(new Rect(1140, 75, 626, 20), recorded + "  /  " + split + duration + "  /  10-second windows", rightSmallStyle);
        }

        private void RebuildRecordingGroups()
        {
            recordingGroups.Clear();
            if (catalog == null || catalog.entries == null) return;
            for (int i = 0; i < catalog.entries.Length; i++)
            {
                var pair = catalog.entries[i];
                RecordingGroup group = recordingGroups.Find(g => g.camera == pair.label);
                if (group == null) { group = new RecordingGroup { camera = pair.label }; recordingGroups.Add(group); }
                group.sources.Add(i);
            }
            foreach (var group in recordingGroups)
                group.sources.Sort((a, b) => string.CompareOrdinal(RecordingTime(catalog.entries[a].id, true),
                    RecordingTime(catalog.entries[b].id, true)));
            if (sourceIndex >= 0 && sourceIndex < catalog.entries.Length)
                rememberedRecording[catalog.entries[sourceIndex].label] = sourceIndex;
        }

        private RecordingGroup ActiveRecordingGroup()
        {
            if (catalog == null || sourceIndex < 0 || sourceIndex >= catalog.entries.Length) return null;
            return recordingGroups.Find(g => g.camera == catalog.entries[sourceIndex].label);
        }

        private void DrawRecordingPicker()
        {
            RecordingGroup group = ActiveRecordingGroup();
            if (group == null) { showRecordingPicker = false; return; }
            Fill(new Rect(0, 0, CanvasWidth, CanvasHeight), new Color(.09f, .15f, .23f, .22f));
            float cardHeight = Mathf.Min(740, Mathf.Max(330, 173 + group.sources.Count * 62));
            Card(new Rect(430, 116, 940, cardHeight));
            Label(new Rect(462, 145, 700, 40), group.camera + " · " + group.sources.Count + " videos", titleStyle);
            if (GUI.Button(new Rect(1220, 143, 118, 38), "Close", buttonStyle)) showRecordingPicker = false;
            Label(new Rect(463, 198, 850, 46), "Separate videos from this camera at different times. Teal = current video; click another row to switch.", bodyStyle);
            Rect viewport = new Rect(461, 257, 878, cardHeight - 173);
            float contentHeight = Mathf.Max(viewport.height, group.sources.Count * 62 + 4);
            recordingPickerScroll = GUI.BeginScrollView(viewport, recordingPickerScroll,
                new Rect(0, 0, viewport.width - 18, contentHeight));
            int chosenIndex = -1;
            for (int i = 0; i < group.sources.Count; i++)
            {
                int index = group.sources[i];
                string time = RecordingTime(catalog.entries[index].id, true);
                string text = time + (index == sourceIndex ? "   ·   CURRENT VIDEO" : "   ·   SELECT THIS VIDEO");
                if (GUI.Button(new Rect(0, i * 62, viewport.width - 32, 53),
                    new GUIContent(text, catalog.entries[index].id),
                    index == sourceIndex ? sourceSelectedButtonStyle : sourceButtonStyle))
                    chosenIndex = index;
            }
            GUI.EndScrollView();
            if (chosenIndex >= 0)
            {
                showRecordingPicker = false;
                if (chosenIndex != sourceIndex) SwitchSource(chosenIndex);
            }
        }

        private static string RecordingTime(string sourceId, bool full)
        {
            if (string.IsNullOrEmpty(sourceId)) return "Unknown recording time";
            int marker = sourceId.LastIndexOf("__", StringComparison.Ordinal);
            if (marker < 0) return "Unknown recording time";
            int start = marker + 2;
            if (sourceId.Length < start + 19) return "Unknown recording time";
            DateTime timestamp;
            if (!DateTime.TryParseExact(sourceId.Substring(start, 19), "yyyy-MM-dd_HH-mm-ss",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out timestamp)) return "Unknown recording time";
            return timestamp.ToString(full ? "yyyy-MM-dd HH:mm:ss" : "MM-dd HH:mm", CultureInfo.InvariantCulture);
        }

        private void DrawVideoPanel()
        {
            Card(videoCard);
            Label(new Rect(54, 124, 430, 27), "Camera view", headingStyle);
            if (data != null && selectedRegion >= 0 && selectedRegion < data.zones.Length)
                Chip(new Rect(220, 120, 290, 31), "Selected: " + data.zones[selectedRegion].label, Hex("E5F2F1"), teal);
            bool old = GUI.enabled;
            bool hasHeader = data != null && data.source.content_y > 0;
            GUI.enabled = old && hasHeader;
            if (GUI.Button(new Rect(858, 119, 172, 32), hasHeader ? (showSourceHeader ? "Road view" : "Source header") : "Full frame", buttonStyle))
                showSourceHeader = !showSourceHeader;
            GUI.enabled = old && CanControl && !dragging && video.canSetTime;
            if (GUI.Button(new Rect(1040, 119, 88, 32), "End", buttonStyle)) SeekTo(data.source.last_frame_time_s, false);
            GUI.enabled = old;
            Rect area = new Rect(48, 163, 1086, 482);
            Round(area, Hex("EDF1F5"));
            bool show = video != null && ready && !initialFrame && !seeking && !dragging && video.texture != null;
            if (show)
            {
                // Road view hides only the baked metadata header in the display. The media is unchanged.
                bool crop = !showSourceHeader && data != null && data.source.content_width > 0 && data.source.content_height > 0;
                float aspect = crop ? data.source.content_width / (float)data.source.content_height : video.texture.width / (float)Math.Max(1, video.texture.height);
                float w = Mathf.Min(area.width, area.height * aspect), h = w / aspect;
                Rect target = new Rect(area.x + (area.width - w) / 2, area.y + (area.height - h) / 2, w, h);
                if (crop)
                {
                    float vw = video.texture.width, vh = video.texture.height;
                    Rect uv = new Rect(data.source.content_x / vw, 1f - (data.source.content_y + data.source.content_height) / vh,
                        data.source.content_width / vw, data.source.content_height / vh);
                    GUI.DrawTextureWithTexCoords(target, video.texture, uv, false);
                }
                else GUI.DrawTexture(target, video.texture, ScaleMode.StretchToFill, false);
                DrawRegionOverlay(target, crop);
                DrawDetectionOverlay(target, crop);
            }
            else
            {
                Label(new Rect(128, 350, 926, 112), dragging ? "Seeking...\nRelease to load the selected moment." : notice, centerBodyStyle);
            }
            bool limited = data != null && !string.IsNullOrWhiteSpace(data.source.limitation);
            Round(new Rect(54, 653, 1074, 32), limited ? Hex("FFF4DF") : Hex("F0F5F7"));
            string caution = limited ? data.source.limitation : "Offline observations: each 10-second summary is confirmed only after its window ends.";
            Label(new Rect(65, 657, 1051, 25), caution, tinyStyle);
            DrawControls();
        }

        private void DrawControls()
        {
            bool old = GUI.enabled;
            GUI.enabled = old && CanControl && !dragging;
            if (GUI.Button(new Rect(54, 697, 102, 38), ended ? "Replay" : video != null && video.isPlaying ? "Pause" : "Play", selectedButtonStyle))
            {
                if (ended) SeekTo(0, true);
                else if (video.isPlaying) video.Pause(); else video.Play();
            }
            if (GUI.Button(new Rect(166, 697, 57, 38), "-10s", buttonStyle)) SeekTo(Math.Max(0, video.time - 10), video.isPlaying);
            if (GUI.Button(new Rect(229, 697, 57, 38), "+10s", buttonStyle)) SeekTo(video.time + 10, video.isPlaying);
            GUI.enabled = old && CanControl && video.canSetTime;
            double shown = dragging ? dragTarget : PresentedTime;
            double duration = data == null ? 1 : data.source.duration_s;
            float selected = GUI.HorizontalSlider(new Rect(302, 693, 510, 26), (float)Math.Max(0, shown), 0, (float)duration, sliderStyle, thumbStyle);
            if (GUI.enabled && Math.Abs(selected - Math.Max(0, shown)) > 0.01)
                UpdateScrub(selected);
            if (dragging && (Event.current.rawType == EventType.MouseUp || Event.current.type == EventType.KeyUp))
                CommitScrub();
            Label(new Rect(304, 723, 510, 19), Clock(shown) + " / " + (data == null ? "--:--" : Clock(data.source.duration_s)), tinyStyle);
            float[] speeds = { .5f, 1, 2, 4 };
            for (int i = 0; i < speeds.Length; i++)
            {
                GUI.enabled = old && CanControl && !dragging && video.canSetPlaybackSpeed;
                if (GUI.Button(new Rect(848 + 70 * i, 697, 64, 38), speeds[i].ToString("0.#") + "x", Mathf.Approximately(speed, speeds[i]) ? selectedButtonStyle : buttonStyle))
                { speed = speeds[i]; video.playbackSpeed = speed; }
            }
            GUI.enabled = old;
        }

        private string WindowSummaryLabel(DemoWindow confirmed)
        {
            if (!CanShowState) return "Waiting for the selected source and decoded frame";
            if (confirmed != null)
                return "Last completed [" + Clock(confirmed.start_s) + ", " + Clock(confirmed.end_s) + ") · Confirmed at " + Clock(confirmed.end_s);
            var observing = CurrentObservingWindow;
            if (observing == null) return "No completed summary available at this source time";
            if (observing.held_duration_s > 0) return "Insufficient evidence · Long held frame in this window";
            if (!observing.complete || !observing.evidence_eligible) return "Insufficient evidence · Incomplete or invalid window";
            return "First summary after " + Clock(observing.end_s) + " · Press Play or +10s";
        }

        private string ObservingWindowLabel()
        {
            if (!CanShowState) return "Values are hidden while loading, switching or seeking.";
            var observing = CurrentObservingWindow;
            if (observing == null) return "Unknown values remain unavailable; they are never filled with zero.";
            string range = "Observing [" + Clock(observing.start_s) + ", " + Clock(observing.end_s) + ")";
            if (observing.held_duration_s > 0) return range + " · Held-frame evidence is excluded";
            if (!observing.complete || !observing.evidence_eligible) return range + " · No state confirmation for this window";
            return range + " · Next summary after " + Clock(observing.end_s);
        }

        private void DrawCards(DemoWindow window)
        {
            Label(new Rect(regions.x, regions.y, 594, 28), "Road regions", headingStyle);
            Label(new Rect(regions.x, regions.y + 31, 594, 23), WindowSummaryLabel(window), smallInkStyle);
            Label(new Rect(regions.x, regions.y + 54, 594, 21), ObservingWindowLabel(), tinyStyle);
            int count = data == null || data.zones == null ? 0 : data.zones.Length;
            if (count == 0) return;
            float gap = count > 4 ? 8 : 10;
            float rowHeight = (regions.height - 82 - gap * (count - 1)) / count;
            for (int i = 0; i < count; i++)
            {
                Rect rect = new Rect(regions.x, regions.y + 82 + i * (rowHeight + gap), regions.width, rowHeight);
                if (selectedRegion == i) Round(new Rect(rect.x - 3, rect.y - 3, rect.width + 6, rect.height + 6), teal);
                Card(rect);
                var zone = data.zones[i];
                var value = window == null ? null : window.ForZone(zone.id);
                Fill(new Rect(rect.x + 18, rect.y + 18, 9, 9), Hex(zone.color_hex ?? "718096"));
                Label(new Rect(rect.x + 37, rect.y + 8, 290, 29), zone.label ?? zone.id, headingStyle);
                if (GUI.Button(new Rect(rect.x + 342, rect.y + 10, 94, 27), "Details", sourceButtonStyle)) SelectRegion(i);
                if (value == null)
                {
                    Label(new Rect(rect.x + 19, rect.y + 35, 170, 43), "--", compactMetricStyle);
                    Label(new Rect(rect.x + 220, rect.y + 43, 350, 38),
                        CanShowState && window == null && CurrentObservingWindow != null &&
                        CurrentObservingWindow.index == 0 && CurrentObservingWindow.evidence_eligible ?
                            "First result after 00:10" : CanShowState ?
                            "Awaiting sufficient completed evidence" : "Waiting for the selected video", compactStateStyle);
                    Label(new Rect(rect.x + 20, rect.yMax - 24, 554, 18), "Count and motion unavailable · No state assigned", tinyStyle);
                }
                else
                {
                    bool high = value.relative_count_level == "high";
                    Chip(new Rect(rect.x + 452, rect.y + 10, 122, 27), DensityShort(value.relative_count_level),
                        high ? Hex("FFF2D8") : Hex("EFF3F7"), high ? Hex("926019") : Hex("536779"));
                    float metricTop = rect.y + (count > 4 ? 36 : 43);
                    Label(new Rect(rect.x + 19, metricTop, 137, 43), value.count_available ? InsightNumber(value.count_mean) : "--", compactMetricStyle);
                    Label(new Rect(rect.x + 135, metricTop + 7, 104, 29), "mean\nvehicles", tinyStyle);
                    string displayState = TrafficDemoData.DisplayState(value);
                    Fill(new Rect(rect.x + 254, metricTop + 6, 4, 33), StateColor(displayState));
                    Label(new Rect(rect.x + 273, metricTop + 1, 298, 38), StateShort(displayState), compactStateStyle,
                        PlainStateExplanation(value));
                    Fill(new Rect(rect.x + 20, rect.yMax - 30, 554, 1), line);
                    string motion = value.motion_available ? InsightNumber(value.motion_median_px_s) + " px/s" : "Unavailable";
                    string coverage = TrafficDemoData.Finite(value.motion_coverage) ? InsightNumber(value.motion_coverage * 100, "0") + "%" : "Unavailable";
                    string detail = value.high_count_persistent_count_only && value.evidence_state == "unknown" ?
                        "Count-only sustained hint  ·  Image motion " + motion :
                        "Image motion  " + motion + "    ·    Motion coverage  " + coverage;
                    Label(new Rect(rect.x + 20, rect.yMax - 24, 554, 21), detail, tinyStyle,
                        "Image displacement, not road speed. Coverage is usable tracked vehicle-time, not accuracy.");
                }
                if (GUI.enabled && Event.current.type == EventType.MouseDown && Event.current.button == 0 && rect.Contains(uiPointer))
                { SelectRegion(i); Event.current.Use(); }
            }
        }

        private void DrawTimeline()
        {
            Card(new Rect(32, 778, 1736, 184));
            Label(new Rect(54, 790, 210, 30), "State timeline", headingStyle);
            Label(new Rect(265, 796, 420, 22), "Offline overview · Click to replay after window end", tinyStyle);
            const float x = 246, width = 1498, top = 848, plotHeight = 88;
            Label(new Rect(54, 826, 178, 20), "ROAD REGION [CATEGORY]", tinyStyle);
            Label(new Rect(830, 943, 500, 18), "SOURCE VIDEO TIME [MINUTES]", tinyStyle);
            if (data == null || data.zones == null || data.zones.Length == 0) return;
            for (int tick = 0; tick <= 6; tick++)
            {
                float tickX = x + tick / 6f * width;
                Fill(new Rect(tickX, top - 2, 1, plotHeight + 2), Hex("E2E8EF"));
                var tickStyle = tick == 0 ? tickLeftStyle : tick == 6 ? tickRightStyle : tickCenterStyle;
                Label(new Rect(tickX - (tick == 0 ? 0 : tick == 6 ? 43 : 22), 824, 44, 20),
                    InsightNumber(data.source.duration_s * tick / 360, "0.#"), tickStyle);
            }
            float rowGap = plotHeight / data.zones.Length;
            float barHeight = Mathf.Min(15, rowGap - 4);
            for (int z = 0; z < data.zones.Length; z++)
            {
                float y = top + z * rowGap;
                if (GUI.Button(new Rect(54, y - 2, 176, 21), new GUIContent(data.zones[z].label ?? data.zones[z].id, "Open this region's evidence and trend"),
                    selectedRegion == z ? activeSmallStyle : smallStyle)) SelectRegion(z);
                if (selectedRegion == z) Fill(new Rect(x - 8, y, 4, barHeight), teal);
                foreach (var window in data.windows)
                {
                    var zone = window.ForZone(data.zones[z].id);
                    Rect rect = new Rect(x + (float)(window.start_s / data.source.duration_s) * width, y,
                        (float)((window.end_s - window.start_s) / data.source.duration_s) * width + .25f, barHeight);
                    Fill(rect, StateColor(TrafficDemoData.DisplayState(zone)));
                    double target = WindowTarget(window);
                    bool frame = TrafficDemoData.Finite(target) && target >= window.end_s && target <= data.source.last_frame_time_s;
                    string tip = data.zones[z].label + "  |  [" + Clock(window.start_s) + ", " + Clock(window.end_s) + ")\n" +
                        (zone == null ? "Unknown" : StateShort(TrafficDemoData.DisplayState(zone)));
                    if (zone != null && zone.high_count_persistent_count_only && zone.evidence_state == "unknown")
                        tip += "\nCount-only sustained hint; main state remains unknown.";
                    tip += frame ? "\nReplay starts after this window ends." : "\nInsufficient window or no later source frame for confirmation.";
                    bool enabled = GUI.enabled; GUI.enabled = enabled && CanControl && !dragging && video.canSetTime && frame;
                    if (GUI.Button(rect, new GUIContent("", tip), GUIStyle.none)) { SelectRegion(z); SeekTo(target, video.isPlaying); }
                    GUI.enabled = enabled;
                }
            }
            double time = dragging ? dragTarget : PresentedTime;
            if (time >= 0)
            {
                float cursor = x + (float)Math.Min(1, time / data.source.duration_s) * width;
                Fill(new Rect(cursor - 1, top - 4, 2, plotHeight + 4), teal);
                Fill(new Rect(cursor - 4, top - 7, 8, 8), teal);
            }
            string[] states = { "no_observed_vehicles", "no_persistent_signal", "high_count_persistent", "high_count_low_motion_persistent", "motion_insufficient", "calibration_insufficient", "insufficient_window" };
            string[] labels = { "None", "No signal", "High count", "High + low motion", "Motion N/A", "Calibration N/A", "Unknown / held / tail" };
            float legendX = 706;
            for (int i = 0; i < states.Length; i++)
            {
                float labelWidth = tinyStyle.CalcSize(new GUIContent(labels[i])).x + 2;
                Fill(new Rect(legendX, 802, 9, 9), StateColor(states[i]));
                Label(new Rect(legendX + 14, 795, labelWidth, 26), labels[i], tinyStyle);
                legendX += labelWidth + 31;
            }
        }

        private static Color StateColor(string state)
        {
            switch (state)
            {
                case "no_observed_vehicles": return Hex("E6EAF0");
                case "no_persistent_signal": return Hex("BFCFDC");
                case "high_count_persistent": return Hex("E5B35B");
                case "high_count_low_motion_persistent": return Hex("DF8F78");
                case "motion_insufficient": return Hex("B5A7D2");
                case "calibration_insufficient": return Hex("A1A8B4");
                case "night_no_observations": return Hex("EBEDF2");
                case "held_image_evidence_insufficient": return Hex("EBEDF2");
                case "evidence_unknown": return Hex("A1A8B4");
                default: return Hex("EBEDF2");
            }
        }
        private static string DensityShort(string level)
        {
            switch (level)
            {
                case "empty": return "None observed";
                case "low": return "Low count";
                case "medium": return "Mid count";
                case "high": return "High count";
                default: return "Unknown";
            }
        }
        private static string StateShort(string state)
        {
            switch (state)
            {
                case "no_observed_vehicles": return "No vehicles observed";
                case "no_persistent_signal": return "No sustained signal";
                case "high_count_persistent": return "Persistent high count";
                case "high_count_low_motion_persistent": return "High count + relatively\nlow image motion";
                case "motion_insufficient": return "Insufficient motion\nevidence";
                case "calibration_insufficient": return "Insufficient calibration";
                case "night_no_observations": return "No night detections\nRoad condition unknown";
                case "held_image_evidence_insufficient": return "Held image\nInsufficient evidence";
                case "insufficient_window": return "Insufficient window\nUngraded";
                case "evidence_unknown": return "Unknown · evidence\ninsufficient";
                default: return "Insufficient evidence\nUnknown";
            }
        }

        private void DrawSettings()
        {
            Card(new Rect(250, 116, 1300, 835));
            Label(new Rect(286, 145, 1120, 44), "Methods & Data", titleStyle);
            if (GUI.Button(new Rect(1320, 144, 195, 42), "Back to replay", buttonStyle)) showSettings = false;
#if UNITY_WEBGL && !UNITY_EDITOR
            Label(new Rect(286, 212, 1190, 62), "Ten recordings from five cameras: nine Validation and one short Train recording (NE12th, 408.577 s).\nEach region uses its own recording baseline; these relative estimates do not rank true congestion across sources.", bodyStyle);
            Label(new Rect(286, 294, 800, 25), "HOSTED VIDEO", tinyStyle);
            Label(new Rect(286, 325, 1228, 49), pathText, smallStyle);
            if (GUI.Button(new Rect(286, 391, 220, 42), "Reload video", selectedButtonStyle)) OpenVideo();
#else
            Label(new Rect(286, 212, 1190, 62), "Ten recordings from five cameras: nine Validation and one short Train recording (NE12th, 408.577 s).\nEach region uses its own recording baseline; these relative estimates do not rank true congestion across sources.", bodyStyle);
            Label(new Rect(286, 294, 800, 25), "LOCAL VIDEO", tinyStyle);
            pathText = GUI.TextField(new Rect(286, 325, 1228, 49), pathText, inputStyle);
            if (GUI.Button(new Rect(286, 391, 220, 42), "Verify and load", selectedButtonStyle)) OpenVideo(true);
#endif
            if (GUI.Button(new Rect(520, 391, 175, 42), "Reload data", buttonStyle)) LoadData();
#if UNITY_EDITOR
            if (GUI.Button(new Rect(709, 391, 205, 42), "Choose video...", buttonStyle))
            {
                string selected = UnityEditor.EditorUtility.OpenFilePanel("Select the corresponding complete video export", "", "mp4");
                if (!string.IsNullOrEmpty(selected)) pathText = selected;
            }
#endif
            Label(new Rect(286, 454, 1228, 49), notice, smallStyle);
            string source = data == null ? "Data not loaded" : SourceLabel + "  |  " + data.source.id + "  |  " + data.source.split;
            Label(new Rect(286, 519, 1228, 35), source, smallStyle);
            Fill(new Rect(286, 569, 1228, 1), line);
            string notes = data != null && data.notes != null ? string.Join("\n\n", data.notes) : "Methods will appear here after the data loads.";
            if (data != null && !string.IsNullOrWhiteSpace(data.source.limitation)) notes = data.source.limitation + "\n\n" + notes;
            notes += "\n\nTIMING AND MISSING EVIDENCE\nWindows are half-open [start, end) in actual source presentation timestamps. A saved state becomes eligible only after the window ends; the current collecting window is labelled separately. Values are hidden during switching, seeking and unavailable playback. Long held frames and incomplete windows do not confirm a state. Missing values stay unavailable, never zero.";
            notes += "\n\nRoad view hides the exported metadata header for presentation only. Source header restores the full annotated image. The underlying video, timestamps and analysis data are unchanged.";
            notes += "\n\nState colours: blue-grey = no sustained signal; amber = persistent high count; salmon = high count and relatively low image motion; purple = insufficient motion evidence; grey = no observation, calibration unavailable, or incomplete window (open region Details for evidence).";
            notes += "\n\nSelect a road-region card for State details: the last completed window, local thresholds, evidence requirements and durations. Count / Image motion tabs show the saved recording, with unavailable observations left as gaps. The selected video's bright outline identifies the region. Road view crops the 150-pixel metadata header; Source header restores it, and region outlines use the corresponding image coordinates.";
            notes += "\n\nExamples opens existing demonstration windows after they end and pauses playback for inspection. Timeline colours and trend lines are static offline context across the recording; they are not live alerts. The foreground cards use only completed eligible evidence.";
            if (data != null) notes += "\n\nEnd navigates to the final source position. An incomplete tail remains insufficient evidence and is not promoted to a completed result. Video frame indices are matched to actual stored PTS; no frame-number / 30 conversion is used.";
            float height = bodyStyle.CalcHeight(new GUIContent(notes), 1168);
            notesScroll = GUI.BeginScrollView(new Rect(286, 594, 1228, 322), notesScroll, new Rect(0, 0, 1190, Math.Max(320, height)));
            Label(new Rect(0, 0, 1168, Math.Max(320, height)), notes, bodyStyle);
            GUI.EndScrollView();
        }

        private void ReleaseUi()
        {
            foreach (var texture in uiTextures) if (texture != null) Destroy(texture);
        }
    }
}
