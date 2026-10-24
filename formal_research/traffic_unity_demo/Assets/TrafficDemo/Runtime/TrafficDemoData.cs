using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using UnityEngine;

namespace TrafficDemo
{
    // Public fields match the offline JSON contract; JsonUtility does not read properties.
    [Serializable] public sealed class TrafficDemoData
    {
        public string schema_version;
        public DemoSource source;
        public string[] notes;
        public DemoZone[] zones;
        public DemoWindow[] windows;
        public DemoMilestone[] milestones;

        public static TrafficDemoData Load(string path)
        {
            if (!File.Exists(path))
                throw new InvalidDataException("The offline source summary is missing: " + path);
            var data = JsonUtility.FromJson<TrafficDemoData>(File.ReadAllText(path));
            if (data == null) throw new InvalidDataException("Cannot read the offline summary JSON.");
            data.Validate();
            return data;
        }

        public void Validate()
        {
            if (schema_version != "newport_state_demo_v1" && schema_version != "five_intersection_demo_v1" &&
                schema_version != "ten_source_demo_v1")
                throw new InvalidDataException("Unsupported offline data schema: " + schema_version);
            NormalizeContract();
            if (source == null || string.IsNullOrWhiteSpace(source.id) || !Finite(source.duration_s) || source.duration_s <= 0)
                throw new InvalidDataException("Invalid source identifier or duration.");
            string split = (source.split ?? "").ToLowerInvariant();
            if (split != "validation" && split != "train")
                throw new InvalidDataException("This development demo accepts Train / Validation sources only.");
            if (source.width <= 0 || source.height <= 0 || source.frame_count <= 0)
                throw new InvalidDataException("Invalid source dimensions or frame count.");
            if (source.video_width <= 0 || source.video_height <= 0 || source.content_x < 0 || source.content_y < 0 ||
                source.content_width <= 0 || source.content_height <= 0 ||
                (long)source.content_x + source.content_width > source.video_width ||
                (long)source.content_y + source.content_height > source.video_height ||
                source.content_width != source.width || source.content_height != source.height)
                throw new InvalidDataException("Invalid exported dimensions or source content rectangle. Implicit scaling of the source area is not supported.");
            if (!Finite(source.last_frame_time_s) || source.last_frame_time_s < 0 ||
                source.last_frame_time_s >= source.duration_s || (source.frame_count > 1 && source.last_frame_time_s == 0))
                throw new InvalidDataException("A valid source last-frame PTS, last_frame_time_s, is required.");
            if (string.IsNullOrWhiteSpace(source.video_sha256) ||
                !Regex.IsMatch(source.video_sha256, "^[0-9a-fA-F]{64}$"))
                throw new InvalidDataException("A complete video SHA256 is required to verify that video and data correspond.");
            if (!string.IsNullOrWhiteSpace(source.detection_boxes_path) &&
                (string.IsNullOrWhiteSpace(source.detection_boxes_sha256) ||
                 !Regex.IsMatch(source.detection_boxes_sha256, "^[0-9a-fA-F]{64}$")))
                throw new InvalidDataException("Saved detection boxes require a verified cache SHA256.");
            if (schema_version == "five_intersection_demo_v1" || schema_version == "ten_source_demo_v1")
            {
                if (source.frame_pts_s == null || source.frame_pts_s.LongLength != source.frame_count)
                    throw new InvalidDataException("The verified source presentation-timestamp table is missing.");
                double previousPts = -1;
                foreach (double pts in source.frame_pts_s)
                {
                    if (!Finite(pts) || pts < 0 || pts <= previousPts || pts >= source.duration_s)
                        throw new InvalidDataException("Source PTS must be finite, increasing, and within the video.");
                    previousPts = pts;
                }
                if (source.frame_pts_s[0] != 0 || Math.Abs(previousPts - source.last_frame_time_s) > 1e-7)
                    throw new InvalidDataException("Source PTS range does not match the video metadata.");
            }
            if (zones == null || zones.Length == 0)
                throw new InvalidDataException("The demo requires at least one defined image region.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var zone in zones)
            {
                if (zone == null || string.IsNullOrWhiteSpace(zone.id) || !ids.Add(zone.id))
                    throw new InvalidDataException("Zone identifiers are missing or duplicated.");
                if (zone.count_thresholds_available && (!Finite(zone.density_low_upper) || !Finite(zone.density_high_lower) ||
                    zone.density_low_upper < 0 || zone.density_high_lower < zone.density_low_upper))
                    throw new InvalidDataException("Invalid relative-count thresholds.");
                if (zone.motion_calibration_available && (!Finite(zone.motion_low_upper) || zone.motion_low_upper < 0))
                    throw new InvalidDataException("Invalid motion threshold.");
                if (zone.count_calibration_available && (!zone.count_thresholds_available || zone.density_high_lower <= zone.density_low_upper))
                    throw new InvalidDataException("Count calibration cannot be available with indistinguishable thresholds.");
                if (zone.polygon == null || zone.polygon.Length < 3)
                    throw new InvalidDataException("A zone is missing its normalized polygon.");
                foreach (var point in zone.polygon)
                    if (point == null || !Finite(point.x) || !Finite(point.y) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1)
                        throw new InvalidDataException("Polygon coordinates must be finite values between 0 and 1.");
            }
            if (windows == null || windows.Length == 0)
                throw new InvalidDataException("The offline summary contains no time windows.");
            double previousEnd = 0;
            for (int i = 0; i < windows.Length; i++)
            {
                var window = windows[i];
                if (window == null || !Finite(window.start_s) || !Finite(window.end_s) || window.start_s < 0 ||
                    window.end_s <= window.start_s || window.start_s < previousEnd - 1e-7 ||
                    window.end_s > source.duration_s + 1e-5)
                    throw new InvalidDataException("Windows must be ordered, non-overlapping, and within the source duration.");
                previousEnd = window.end_s;
                if (!Finite(window.held_duration_s) || window.held_duration_s < 0 ||
                    window.held_duration_s > window.end_s - window.start_s + 1e-6)
                    throw new InvalidDataException("Invalid held-frame or window evidence flags.");
                if (window.zones == null || window.zones.Length != zones.Length)
                    throw new InvalidDataException("A window is missing zone data.");
                var observed = new HashSet<string>(StringComparer.Ordinal);
                foreach (var value in window.zones)
                {
                    if (value == null || !ids.Contains(value.zone_id ?? "") || !observed.Add(value.zone_id))
                        throw new InvalidDataException("Window zone identifiers do not match the zone definitions.");
                    if ((value.count_available && (!Finite(value.count_mean) || value.count_mean < 0)) || !Finite(value.motion_coverage) ||
                        value.motion_coverage < 0 || value.motion_coverage > 1 ||
                        !Finite(value.high_count_duration_s) || value.high_count_duration_s < 0 ||
                        !Finite(value.joint_duration_s) || value.joint_duration_s < 0)
                        throw new InvalidDataException("Invalid window count, coverage or persistence duration.");
                    if (value.motion_available && (!Finite(value.motion_median_px_s) || value.motion_median_px_s < 0))
                        throw new InvalidDataException("Motion evidence is marked available but its value is invalid.");
                    if (!KnownDensity(value.relative_count_level) || !KnownState(value.state_hint))
                        throw new InvalidDataException("Unsupported count level or state type; check the data version.");
                    if (schema_version == "ten_source_demo_v1" &&
                        (value.evidence_state != "unknown" && value.evidence_state != "no_persistent_hint" &&
                         value.evidence_state != "high_count_hint" && value.evidence_state != "joint_hint"))
                        throw new InvalidDataException("Unsupported conservative evidence state.");
                    var definition = Array.Find(zones, z => z.id == value.zone_id);
                    if (!definition.count_calibration_available && value.relative_count_level != "unknown" && value.relative_count_level != "empty")
                        throw new InvalidDataException("Coincident quantile thresholds require an unknown count level.");
                    if (!window.evidence_eligible && (value.relative_count_level != "unknown" || value.high_count_duration_s != 0 || value.joint_duration_s != 0))
                        throw new InvalidDataException("Invalid windows must not be classified or extend persistence.");
                }
            }
            if (milestones != null)
                foreach (var mark in milestones)
                    if (mark == null || !Finite(mark.time_s) || mark.time_s < 0 || mark.time_s > source.duration_s)
                        throw new InvalidDataException("A demonstration milestone is outside the video range.");
        }

        // JsonUtility has no nullable numeric support. The offline adapter leaves JSON null
        // intact AND writes explicit availability flags; convert unavailable fields to NaN
        // here so a forgotten UI guard cannot silently turn missing evidence into zero.
        void NormalizeContract()
        {
            bool legacy = schema_version == "newport_state_demo_v1";
            if (zones != null) foreach (var zone in zones)
            {
                if (zone == null) continue;
                if (legacy)
                {
                    zone.count_thresholds_available = Finite(zone.density_low_upper) && Finite(zone.density_high_lower);
                    zone.count_calibration_available = zone.count_thresholds_available && zone.density_low_upper < zone.density_high_lower;
                    zone.motion_calibration_available = Finite(zone.motion_low_upper) && zone.motion_low_upper >= 0;
                }
                if (!zone.count_thresholds_available) zone.density_low_upper = zone.density_high_lower = double.NaN;
                if (!zone.motion_calibration_available) zone.motion_low_upper = double.NaN;
            }
            if (windows != null) foreach (var window in windows)
            {
                if (window == null) continue;
                if (legacy) window.full_valid_window = window.complete;
                if (window.zones == null) continue;
                foreach (var value in window.zones)
                {
                    if (value == null) continue;
                    if (legacy)
                    {
                        value.relative_count_level = value.density_level;
                        value.relative_count_label = value.density_label;
                        value.state_hint = value.traffic_state;
                        value.state_label = value.traffic_state_label;
                        value.count_available = Finite(value.count_mean) && value.count_mean >= 0;
                        if (value.missing_duration_s > 0) window.full_valid_window = false;
                    }
                    else
                    {
                        value.density_level = value.relative_count_level;
                        value.density_label = value.relative_count_label;
                        value.traffic_state = value.state_hint;
                        value.traffic_state_label = value.state_label;
                    }
                    if (!value.count_available) value.count_mean = double.NaN;
                    if (!value.motion_available) value.motion_median_px_s = double.NaN;
                }
            }
        }

        // Half-open windows [start,end): gaps return -1; never borrow a stale window.
        public int WindowAt(double seconds)
        {
            if (!Finite(seconds) || seconds < 0 || seconds >= source.duration_s) return -1;
            int low = 0, high = windows.Length - 1;
            while (low <= high)
            {
                int mid = low + (high - low) / 2;
                if (seconds < windows[mid].start_s) high = mid - 1;
                else if (seconds >= windows[mid].end_s) low = mid + 1;
                else return mid;
            }
            return -1;
        }

        // A window's offline result only exists after its right edge. Never skip an
        // invalid predecessor or keep an old signal during a hold/incomplete window.
        public int ConfirmedWindowAt(double seconds)
        {
            int current = WindowAt(seconds);
            if (current <= 0 || !windows[current].evidence_eligible || !windows[current].complete) return -1;
            var previous = windows[current - 1];
            return previous.evidence_eligible && previous.complete && previous.end_s <= seconds &&
                Math.Abs(previous.end_s - windows[current].start_s) < 1e-7 ? current - 1 : -1;
        }

        // This maps a reported source time onto the verified presentation timeline.
        // It does not claim that Unity's approximate player clock identifies a decoded
        // frame exactly; the controller applies its own boundary uncertainty guard.
        public int SourceFrameIndexAt(double seconds)
        {
            if (!Finite(seconds) || seconds < 0 || seconds >= source.duration_s || source.frame_pts_s == null || source.frame_pts_s.Length == 0)
                return -1;
            int low = 0, high = source.frame_pts_s.Length - 1;
            while (low <= high)
            {
                int mid = low + (high - low) / 2;
                if (source.frame_pts_s[mid] <= seconds) low = mid + 1;
                else high = mid - 1;
            }
            return high;
        }

        public double SourcePtsAt(double seconds)
        {
            int index = SourceFrameIndexAt(seconds);
            return index < 0 ? double.NaN : source.frame_pts_s[index];
        }

        public static bool Finite(double value) { return !double.IsNaN(value) && !double.IsInfinity(value); }
        public static bool KnownDensity(string s)
        { return s == "empty" || s == "low" || s == "medium" || s == "high" || s == "unknown"; }
        public static bool KnownState(string s)
        {
            return s == "no_observed_vehicles" || s == "no_persistent_signal" || s == "high_count_persistent" ||
                s == "high_count_low_motion_persistent" || s == "motion_insufficient" || s == "insufficient_window" ||
                s == "calibration_insufficient" || s == "night_no_observations" || s == "held_image_evidence_insufficient";
        }

        // The frozen state_hint is retained for audit. Supplementary cards and charts
        // use the stricter evidence_state, so a count-only hint never masks unknown.
        public static string DisplayState(DemoZoneWindow value)
        {
            if (value == null) return null;
            switch (value.evidence_state)
            {
                case "unknown": return "evidence_unknown";
                case "no_persistent_hint": return "no_persistent_signal";
                case "high_count_hint": return "high_count_persistent";
                case "joint_hint": return "high_count_low_motion_persistent";
                default: return value.state_hint;
            }
        }
    }

    [Serializable] public sealed class DemoSource
    {
        public string id, split, video_path, video_sha256, limitation, state_path, rules_path, report_path, state_sha256, rules_sha256;
        public string detection_boxes_path, detection_boxes_sha256;
        public double duration_s, last_frame_time_s;
        public double[] frame_pts_s;
        public bool night_case;
        public long frame_count;
        public int width, height, video_width, video_height, content_x, content_y, content_width, content_height;
    }
    [Serializable] public sealed class DemoPoint { public float x, y; }
    [Serializable] public sealed class DemoZone
    {
        public string id, label, color_hex;
        public DemoPoint[] polygon;
        public double density_low_upper, density_high_lower, motion_low_upper;
        public int density_calibration_windows, motion_calibration_windows;
        public bool count_thresholds_available, count_calibration_available, motion_calibration_available;
    }
    [Serializable] public sealed class DemoWindow
    {
        public int index;
        public double start_s, end_s;
        public bool complete, full_valid_window;
        public bool evidence_eligible { get { return complete && full_valid_window && held_duration_s <= 0; } }
        public double held_duration_s;
        public string quality;
        public DemoZoneWindow[] zones;
        public DemoZoneWindow ForZone(string id)
        {
            foreach (var zone in zones) if (zone.zone_id == id) return zone;
            return null;
        }
    }
    [Serializable] public sealed class DemoZoneWindow
    {
        public string zone_id, density_level, density_label, traffic_state, traffic_state_label, reason;
        public string relative_count_level, relative_count_label, state_hint, state_label, night_caution;
        public string evidence_state;
        public string[] evidence_unknown_reasons;
        public bool high_count_persistent_count_only, joint_hint;
        public double count_mean, motion_median_px_s, motion_coverage, high_count_duration_s, joint_duration_s;
        public double motion_vehicle_seconds, observed_vehicle_seconds, valid_duration_s, missing_duration_s;
        public int motion_track_count;
        public bool motion_available, count_available, motion_low_relative_available, motion_low_relative;
    }
    [Serializable] public sealed class DemoMilestone { public double time_s; public string label; }
}
