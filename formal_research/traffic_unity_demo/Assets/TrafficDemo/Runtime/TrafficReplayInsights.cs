using System;
using System.Globalization;
using UnityEngine;

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        private bool insightsMotionTab, showCalculationDetails;
        private Vector2 calculationScroll;
        private GUIStyle insightSmall, insightTiny, insightNumber, insightState, insightReading;
        private GUIStyle insightButton, insightSelectedButton, insightRightTick, insightCenterTick;

        private void EnsureInsightStyles()
        {
            if (insightSmall != null) return;
            insightSmall = TextStyle(13, muted);
            insightTiny = TextStyle(11, muted);
            insightNumber = TextStyle(28, ink, true);
            insightState = TextStyle(18, ink, true);
            insightReading = TextStyle(14, ink);
            insightButton = new GUIStyle(buttonStyle) { fontSize = 14 };
            insightSelectedButton = new GUIStyle(selectedButtonStyle) { fontSize = 14 };
            insightRightTick = new GUIStyle(insightTiny) { alignment = TextAnchor.MiddleRight, wordWrap = false };
            insightCenterTick = new GUIStyle(insightTiny) { alignment = TextAnchor.MiddleCenter, wordWrap = false };
        }

        private static string InsightNumber(double number, string format = "0.0")
        {
            return TrafficDemoData.Finite(number) ? number.ToString(format, CultureInfo.InvariantCulture) : "--";
        }

        private static string RegionTabLabel(DemoZone zone)
        {
            string label = zone.label ?? zone.id;
            return label.Replace(" road", "").Replace("Road ", "").Replace("Junction center", "Center");
        }

        private void DrawInsightsPanel(DemoWindow currentWindow)
        {
            EnsureInsightStyles();
            Card(regions);
            float x = regions.x, y = regions.y;
            Label(new Rect(x + 20, y + 14, 380, 32), "State details", headingStyle);
            if (GUI.Button(new Rect(x + 444, y + 13, 130, 33), "All regions", insightButton))
            {
                showInsights = false;
                selectedRegion = -1;
                return;
            }
            if (data == null || data.zones == null || data.zones.Length == 0)
            {
                Label(new Rect(x + 24, y + 88, 546, 70), "Load the offline data to inspect a region.", bodyStyle);
                return;
            }
            selectedRegion = Mathf.Clamp(selectedRegion, 0, data.zones.Length - 1);
            float tabWidth = 560f / data.zones.Length;
            for (int i = 0; i < data.zones.Length; i++)
                if (GUI.Button(new Rect(x + 20 + i * tabWidth, y + 55, tabWidth - 6, 30),
                    new GUIContent(RegionTabLabel(data.zones[i]), data.zones[i].label),
                    i == selectedRegion ? insightSelectedButton : insightButton)) selectedRegion = i;

            DemoZone zone = data.zones[selectedRegion];
            // A confirmed, completed window is supplied by the source PTS gate.
            // No requested seek time or current collecting window can provide its values.
            DemoZoneWindow value = CanShowState && currentWindow != null ? currentWindow.ForZone(zone.id) : null;
            Label(new Rect(x + 20, y + 94, 554, 21), WindowSummaryLabel(currentWindow), insightSmall);
            Label(new Rect(x + 20, y + 117, 554, 20), ObservingWindowLabel(), insightTiny);

            if (value == null)
            {
                Label(new Rect(x + 20, y + 151, 554, 45), "No confirmed state available", insightState);
                Label(new Rect(x + 20, y + 205, 554, 77),
                    "A complete window is confirmed only after its end. Long held frames, incomplete windows and unavailable evidence remain ungraded. Values stay hidden while loading or seeking.", insightReading);
                Label(new Rect(x + 20, y + 303, 554, 25), "Completed-window count, motion and duration: --", insightSmall);
            }
            else
            {
                string displayState = TrafficDemoData.DisplayState(value);
                Fill(new Rect(x + 20, y + 145, 5, 36), StateColor(displayState));
                Label(new Rect(x + 36, y + 140, 530, 45), StateShort(displayState), insightState,
                    PlainStateExplanation(value));
                Label(new Rect(x + 20, y + 190, 554, 42), PlainStateExplanation(value), insightReading, PlainStateExplanation(value));
                Fill(new Rect(x + 20, y + 237, 554, 1), line);

                Label(new Rect(x + 20, y + 245, 270, 20), "Average vehicles (10 s)", insightReading);
                Label(new Rect(x + 309, y + 245, 265, 20), "Movement in image [px/s]", insightReading);
                Label(new Rect(x + 20, y + 267, 265, 34), value.count_available ? InsightNumber(value.count_mean) : "Unavailable", insightNumber);
                Label(new Rect(x + 309, y + 267, 265, 34), value.motion_available ?
                    InsightNumber(value.motion_median_px_s) : "Unavailable", insightNumber);
                Label(new Rect(x + 20, y + 304, 279, 20), CountReading(value), insightReading);
                Label(new Rect(x + 309, y + 304, 265, 20), MotionReading(value, zone), insightReading);
                Label(new Rect(x + 20, y + 327, 279, 19),
                    zone.count_calibration_available ? "High above " + InsightNumber(zone.density_high_lower, "0.00") + " vehicles" :
                    "Count-level calibration unavailable", insightSmall);
                Label(new Rect(x + 309, y + 327, 265, 19),
                    zone.motion_calibration_available ? "Low at or below " + InsightNumber(zone.motion_low_upper, "0.00") + " px/s" :
                    "Low-motion calibration unavailable", insightSmall);
                Label(new Rect(x + 20, y + 352, 554, 20), PersistenceExplanation(value), insightReading);
                Label(new Rect(x + 20, y + 375, 554, 20), PersistenceExplanation(value, true), insightReading,
                    "Continuous duration through the end of the displayed completed window.");
            }

            if (GUI.Button(new Rect(x + 20, y + 407, 130, 29), "Count trend",
                !showCalculationDetails && !insightsMotionTab ? insightSelectedButton : insightButton))
            { insightsMotionTab = false; showCalculationDetails = false; }
            if (GUI.Button(new Rect(x + 156, y + 407, 130, 29), "Motion trend",
                !showCalculationDetails && insightsMotionTab ? insightSelectedButton : insightButton))
            { insightsMotionTab = true; showCalculationDetails = false; }
            if (GUI.Button(new Rect(x + 306, y + 407, 268, 29), "How this is calculated",
                showCalculationDetails ? insightSelectedButton : insightButton))
            { showCalculationDetails = !showCalculationDetails; calculationScroll = Vector2.zero; }

            if (showCalculationDetails)
                DrawCalculationDetails(new Rect(x + 20, y + 447, 554, 174), zone, value);
            else
                DrawInsightTrend(new Rect(x + 20, y + 447, 554, 174),
                    selectedRegion, insightsMotionTab, value == null ? null : currentWindow);
            Label(new Rect(x + 20, y + 627, 554, 18), showCalculationDetails ?
                "Scroll for evidence and rules · Select a trend to return" :
                "Navy = saved observations · Teal = video time / confirmed sample · Gaps = unavailable", insightTiny);
        }

        private static string CountReading(DemoZoneWindow value)
        {
            switch (value.relative_count_level)
            {
                case "high": return "High for this region & hour";
                case "medium": return "Mid-range for this region & hour";
                case "low": return "Low for this region & hour";
                case "empty": return "No vehicles observed";
                default: return "Count level unavailable";
            }
        }

        private static string MotionReading(DemoZoneWindow value, DemoZone zone)
        {
            if (!value.motion_available) return "Not enough motion evidence";
            if (!zone.motion_calibration_available) return "Low-motion limit unavailable";
            return value.motion_median_px_s <= zone.motion_low_upper ?
                "At or below low-motion limit" : "Above low-motion limit";
        }

        private static string PlainStateExplanation(DemoZoneWindow value)
        {
            if (value.evidence_state == "unknown")
            {
                string reason = "Conservative main state unknown: " + EvidenceReasonText(value.evidence_unknown_reasons) + ".";
                return value.high_count_persistent_count_only ?
                    reason + " A sustained count-only hint is kept separately." : reason;
            }
            switch (TrafficDemoData.DisplayState(value))
            {
                case "high_count_low_motion_persistent":
                    return "High count and relatively low image motion lasted at least 30 s. This can also include signal waiting.";
                case "high_count_persistent":
                    return "High count has lasted at least 30 s. This alone does not establish congestion.";
                case "motion_insufficient":
                    return "There is not enough motion evidence for a combined count-and-motion assessment.";
                case "calibration_insufficient":
                    return "The baseline cannot reliably separate low, mid and high count levels.";
                case "no_observed_vehicles": return "No vehicles were observed in this window. This does not establish that the real road is empty.";
                case "night_no_observations": return "No vehicles detected at night. Glare and missed detections leave the road condition unknown.";
                case "held_image_evidence_insufficient": return "A long held image overlaps this window. Repeated pixels cannot confirm a traffic state.";
                case "insufficient_window": return "This is not a complete valid 10-second window, so no state is assigned.";
                case "no_persistent_signal":
                    return value.relative_count_level == "high" ?
                        "Count is high, but the 30-second persistence rule is not met. This does not establish free flow." :
                        "No 30-second high-count signal in this window. This does not establish free flow.";
                default: return "The available evidence does not support a state assessment.";
            }
        }

        private static string EvidenceReasonText(string[] reasons)
        {
            if (reasons == null || reasons.Length == 0) return "insufficient evidence";
            string[] readable = new string[reasons.Length];
            for (int i = 0; i < reasons.Length; i++)
                switch (reasons[i])
                {
                    case "motion_observation_insufficient": readable[i] = "current motion evidence is insufficient"; break;
                    case "motion_calibration_insufficient": readable[i] = "the motion baseline has too few valid windows"; break;
                    case "count_calibration_degenerate": readable[i] = "count thresholds cannot separate levels"; break;
                    case "held_image": readable[i] = "a held image overlaps this window"; break;
                    case "incomplete_tail": readable[i] = "the final window is incomplete"; break;
                    default: readable[i] = "evidence is unavailable"; break;
                }
            return string.Join("; ", readable);
        }

        private void DrawCalculationDetails(Rect bounds, DemoZone zone, DemoZoneWindow value)
        {
            string countLimits = zone.count_calibration_available ?
                "Low: up to " + InsightNumber(zone.density_low_upper, "0.00") + "; mid: above that to " +
                InsightNumber(zone.density_high_lower, "0.00") + "; high: above " + InsightNumber(zone.density_high_lower, "0.00") +
                " vehicles. Zero observations are separate. These are the region's P33 / P67 values from " + zone.density_calibration_windows + " complete windows." :
                "The count baseline cannot separate three levels. The level stays unknown.";
            string motionLimit = zone.motion_calibration_available ?
                "At or below " + InsightNumber(zone.motion_low_upper, "0.00") + " px/s is relatively low for this region and hour (P25). " :
                "The relative low-motion limit is unavailable. ";
            string evidence = value == null ? "Current evidence is hidden until a decoded source frame follows a complete eligible window." :
                "Usable motion evidence: " + InsightNumber(value.motion_coverage * 100, "0") + "% of observed vehicle-time; " +
                InsightNumber(value.motion_vehicle_seconds) + " vehicle-seconds. This is coverage, not accuracy. " +
                "One vehicle observed for 5 usable seconds contributes 5 vehicle-seconds.";
            string text = "COUNT LEVELS — THIS REGION, THIS HOUR\n" + countLimits +
                "\n\nHOW THE AVERAGE IS FOUND\nVehicle counts are weighted by their observed duration inside the 10-second window. This is not the number of different vehicles passing through." +
                "\n\nIMAGE MOTION — NOT ROAD SPEED\n" + motionLimit +
                "Pixels per second describe movement in the image, not km/h. Different regions have different perspectives." +
                "\n\nEVIDENCE QUALITY\n" + evidence +
                "\nA usable motion window needs a complete valid 10 s, at least 50% coverage and at least 5 vehicle-seconds. " +
                "The low-motion baseline needs at least 30 usable windows; this region has " + zone.motion_calibration_windows + "." +
                "\n\nWHY 30 SECONDS?\nThis prototype uses 30 s of continuous high count for a sustained count signal. " +
                "The combined signal needs high count AND relatively low motion together for 30 s. " +
                "Durations include this whole offline window and are shown only after it ends; 30 s is the trigger, not a maximum. These choices do not establish congestion accuracy." +
                "\n\nHELD FRAMES AND INCOMPLETE WINDOWS\nAny long held-frame overlap makes a state window insufficient. Such windows are excluded from the trend and calibration. Missing count or motion is a gap, not zero. Thresholds differ by source and cannot rank real congestion." +
                (value == null ? "" : "\n\nSAVED WINDOW EXPLANATION\n" + PlainStateExplanation(value) +
                    "\nFrozen state_hint: " + value.state_hint + ". A count-only hint never replaces conservative unknown.");
            float height = insightReading.CalcHeight(new GUIContent(text), bounds.width - 28);
            calculationScroll = GUI.BeginScrollView(bounds, calculationScroll,
                new Rect(0, 0, bounds.width - 22, height + 8));
            Label(new Rect(0, 0, bounds.width - 28, height), text, insightReading);
            GUI.EndScrollView();
        }

        // This is the sole value gate used by the rendered trend and its QA.
        // Returning false produces a gap; the caller must never plot the out
        // value when false. In particular, unavailable motion is not zero speed.
        private bool TryTrendValue(DemoWindow window, int regionIndex, bool motion, out double value)
        {
            value = double.NaN;
            if (data == null || data.zones == null || regionIndex < 0 || regionIndex >= data.zones.Length ||
                window == null || !window.complete || !window.evidence_eligible || window.zones == null ||
                !TrafficDemoData.Finite(window.start_s) || !TrafficDemoData.Finite(window.end_s) ||
                window.end_s <= window.start_s || Math.Abs(window.end_s - window.start_s - 10) > 1e-7) return false;
            DemoZoneWindow row = window.ForZone(data.zones[regionIndex].id);
            if (row == null || !TrafficDemoData.Finite(row.valid_duration_s) ||
                !TrafficDemoData.Finite(row.missing_duration_s) || row.missing_duration_s > 1e-7 ||
                row.valid_duration_s < window.end_s - window.start_s - 1e-7) return false;
            if (motion ? !row.motion_available : !row.count_available) return false;
            double candidate = motion ? row.motion_median_px_s : row.count_mean;
            if (!TrafficDemoData.Finite(candidate) || candidate < 0) return false;
            value = candidate;
            return true;
        }

        private string PersistenceExplanation(DemoZoneWindow value, bool joint = false)
        {
            if (value == null) return "Duration: no eligible completed window";
            if (value.relative_count_level == "unknown" || value.state_hint == "night_no_observations" ||
                value.state_hint == "calibration_insufficient" || value.state_hint == "held_image_evidence_insufficient" || value.state_hint == "insufficient_window")
                return (joint ? "High count + low motion: " : "High count duration: ") + "Unavailable (insufficient evidence)";
            return (joint ? "High count + low motion: " : "High count duration: ") +
                InsightNumber(joint ? value.joint_duration_s : value.high_count_duration_s, "0") + " s   ·   Required: 30 s";
        }

        private static bool InsightWindowsTouch(DemoWindow previous, DemoWindow current)
        {
            return previous != null && current != null &&
                Math.Abs(previous.end_s - current.start_s) <= 1e-7;
        }

        private static double InsightTickStep(double maximum, bool wholeNumbers)
        {
            double raw = Math.Max(maximum, 1e-6) / 4;
            double scale = Math.Pow(10, Math.Floor(Math.Log10(raw)));
            double fraction = raw / scale;
            double step = (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * scale;
            return wholeNumbers ? Math.Max(1, step) : step;
        }

        private void DrawInsightTrend(Rect bounds, int regionIndex, bool motion, DemoWindow currentWindow)
        {
            DemoZone zone = data.zones[regionIndex];
            Label(new Rect(bounds.x, bounds.y, 230, 18), motion ? "Image motion [px/s]" : "Average count [vehicles]", insightSmall);
            string limits = motion ? (zone.motion_calibration_available ?
                "Low-motion limit: " + InsightNumber(zone.motion_low_upper, "0.00") : "Limit unavailable") :
                (zone.count_calibration_available ?
                "Mid > " + InsightNumber(zone.density_low_upper, "0.00") + "  ·  High > " + InsightNumber(zone.density_high_lower, "0.00") : "Limits unavailable");
            Label(new Rect(bounds.x + 235, bounds.y + 1, 319, 17), "Dashed: " + limits, insightTiny);
            Rect plot = new Rect(bounds.x + 44, bounds.y + 24, bounds.width - 56, bounds.height - 65);
            Fill(plot, Hex("F7F9FC"));
            double duration = data.source.duration_s;
            double maxValue = 0;
            bool anyValue = false;
            foreach (DemoWindow window in data.windows)
            {
                double number;
                if (!TryTrendValue(window, regionIndex, motion, out number)) continue;
                anyValue = true;
                maxValue = Math.Max(maxValue, number);
            }
            double[] thresholds = motion ? (zone.motion_calibration_available ? new[] { zone.motion_low_upper } : new double[0]) :
                (zone.count_calibration_available ? new[] { zone.density_low_upper, zone.density_high_lower } : new double[0]);
            foreach (double threshold in thresholds)
                if (TrafficDemoData.Finite(threshold) && threshold >= 0) maxValue = Math.Max(maxValue, threshold);
            double step = InsightTickStep(Math.Max(maxValue * 1.06, 1), !motion);
            double upper = Math.Max(step, Math.Ceiling(Math.Max(maxValue * 1.06, 1) / step) * step);

            for (double tick = 0; tick <= upper + step * 1e-6; tick += step)
            {
                float py = plot.yMax - (float)(tick / upper) * plot.height;
                Fill(new Rect(plot.x, py, plot.width, 1), Hex("E3E8EF"));
                Label(new Rect(bounds.x, py - 8, 37, 16), InsightNumber(tick, step < 1 ? "0.##" : "0"), insightRightTick);
            }
            for (int tick = 0; tick <= 4; tick++)
            {
                float px = plot.x + tick / 4f * plot.width;
                Fill(new Rect(px, plot.y, 1, plot.height), Hex("E3E8EF"));
                Fill(new Rect(px, plot.yMax, 1, 4), Hex("91A3B3"));
                Label(new Rect(px - 18, plot.yMax + 5, 36, 16), InsightNumber(duration * tick / 240, "0.#"), insightCenterTick);
            }
            Label(new Rect(plot.x, bounds.yMax - 17, plot.width, 16), "Video time [min]", insightCenterTick);
            foreach (double threshold in thresholds)
            {
                if (!TrafficDemoData.Finite(threshold) || threshold < 0) continue;
                float py = plot.yMax - (float)(threshold / upper) * plot.height;
                for (float px = plot.x; px < plot.xMax; px += 10)
                    Fill(new Rect(px, py, Mathf.Min(6, plot.xMax - px), 1), Hex("91A3B3"));
            }

            bool havePrevious = false;
            Vector2 previousPoint = Vector2.zero;
            DemoWindow previousWindow = null;
            foreach (DemoWindow window in data.windows)
            {
                double number;
                if (!TryTrendValue(window, regionIndex, motion, out number))
                {
                    havePrevious = false;
                    previousWindow = null;
                    continue;
                }
                Vector2 point = new Vector2(plot.x + (float)((window.start_s + window.end_s) * .5 / duration) * plot.width,
                    plot.yMax - (float)(number / upper) * plot.height);
                if (havePrevious && InsightWindowsTouch(previousWindow, window))
                    DrawInsightLine(previousPoint, point, Hex("203F5B"), 1.7f);
                // A point also represents an isolated available window; no line
                // is drawn across an unavailable or missing interval.
                Fill(new Rect(point.x - 1, point.y - 1, 2, 2), Hex("203F5B"));
                previousPoint = point;
                previousWindow = window;
                havePrevious = true;
            }
            if (!anyValue)
                Label(new Rect(plot.x + 12, plot.y + plot.height / 2 - 22, plot.width - 24, 48),
                    "No available complete-window observations", insightSmall);
            if (PresentedTime >= 0)
            {
                float px = plot.x + (float)(Math.Min(duration, PresentedTime) / duration) * plot.width;
                Fill(new Rect(px, plot.y, 2, plot.height), teal);
                double number;
                if (currentWindow != null && TryTrendValue(currentWindow, regionIndex, motion, out number))
                {
                    float pointX = plot.x + (float)((currentWindow.start_s + currentWindow.end_s) * .5 / duration) * plot.width;
                    float pointY = plot.yMax - (float)(number / upper) * plot.height;
                    Fill(new Rect(pointX - 3, pointY - 3, 6, 6), teal);
                }
            }
        }

        private static void DrawInsightLine(Vector2 from, Vector2 to, Color color, float thickness)
        {
            if (Event.current.type != EventType.Repaint) return;
            Vector2 delta = to - from;
            if (delta.sqrMagnitude <= 0) return;
            Matrix4x4 before = GUI.matrix;
            GUI.matrix = before * Matrix4x4.TRS(new Vector3(from.x, from.y, 0),
                Quaternion.Euler(0, 0, Mathf.Atan2(delta.y, delta.x) * Mathf.Rad2Deg), Vector3.one);
            Fill(new Rect(0, -thickness / 2, delta.magnitude, thickness), color);
            GUI.matrix = before;
        }
    }
}
