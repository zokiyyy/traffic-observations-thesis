using System;
using System.Collections.Generic;
using UnityEngine;

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        private int selectedRegion = -1;
        private bool showInsights, showExamples;
        private readonly List<TrafficDetectionBox> visibleDetections = new List<TrafficDetectionBox>();

        private bool SelectRegion(int index, bool openDetails = true)
        {
            if (data == null || index < 0 || index >= data.zones.Length) return false;
            selectedRegion = index;
            showInsights = openDetails;
            return true;
        }

        private bool TryExampleTarget(int index, out double target)
        {
            target = -1;
            if (data == null || data.milestones == null || index < 0 || index >= data.milestones.Length) return false;
            var mark = data.milestones[index];
            if (data.schema_version == "five_intersection_demo_v1" || data.schema_version == "ten_source_demo_v1")
            {
                // The adapter stores confirmation times, not observation-window starts.
                target = mark.time_s == 0 ? 0 : Math.Min(mark.time_s + .2, data.source.last_frame_time_s);
                return TrafficDemoData.Finite(target) && target >= 0;
            }
            int wi = data.WindowAt(mark.time_s);
            if (wi < 0 || !data.windows[wi].evidence_eligible) return false;
            var window = data.windows[wi];
            target = WindowTarget(window);
            return target >= 0;
        }

        private double WindowTarget(DemoWindow window)
        {
            if (data == null || window == null || !window.evidence_eligible) return -1;
            double target = window.end_s + .2;
            if (target > data.source.last_frame_time_s) return -1;
            int confirmed = data.ConfirmedWindowAt(target);
            return confirmed >= 0 && data.windows[confirmed] == window ? target : -1;
        }

        private bool OpenExample(int index)
        {
            double target;
            if (!CanControl || dragging || !video.canSetTime || !TryExampleTarget(index, out target)) return false;
            string label = data.milestones[index].label ?? "";
            int region = Array.FindIndex(data.zones, z => label.StartsWith(z.label + ":", StringComparison.Ordinal));
            if (region >= 0) SelectRegion(region);
            else { selectedRegion = -1; showInsights = false; }
            showExamples = false;
            SeekTo(target, false);
            return true;
        }

        private void DrawExamplesPanel()
        {
            Fill(new Rect(0, 0, CanvasWidth, CanvasHeight), new Color(.09f, .15f, .23f, .22f));
            Card(new Rect(300, 128, 1200, 726));
            Label(new Rect(332, 153, 800, 40), "Example windows", titleStyle);
            if (GUI.Button(new Rect(1344, 151, 122, 38), "Close", buttonStyle)) showExamples = false;
            Label(new Rect(334, 206, 1112, 43), "Jump just after a saved window ends to inspect its confirmed summary. These are offline examples, not live alerts.", bodyStyle);
            if (data == null || data.milestones == null) return;
            bool enabled = GUI.enabled;
            for (int i = 0; i < data.milestones.Length; i++)
            {
                Rect row = new Rect(334, 268 + i * 53, 1132, 47);
                Round(row, i % 2 == 0 ? Hex("F1F5F8") : Hex("F8FAFC"));
                Label(new Rect(row.x + 15, row.y + 11, 80, 25), Clock(data.milestones[i].time_s), exampleTimeStyle);
                Label(new Rect(row.x + 108, row.y + 5, 888, 40), data.milestones[i].label, smallInkStyle);
                double target;
                GUI.enabled = enabled && CanControl && !dragging && video.canSetTime && TryExampleTarget(i, out target);
                if (GUI.Button(new Rect(row.xMax - 105, row.y + 5, 93, 36), "Open", buttonStyle)) OpenExample(i);
                GUI.enabled = enabled;
            }
            Label(new Rect(334, 770, 1132, 50), "Each result needs the complete 10-second interval. Playback pauses after its end; examples without a safe confirmation frame are unavailable.", smallStyle);
        }

        private Vector2 RegionPointToCanvas(DemoPoint point, Rect target, bool cropped)
        {
            if (cropped) return new Vector2(target.x + point.x * target.width, target.y + point.y * target.height);
            return new Vector2(target.x + (data.source.content_x + point.x * data.source.content_width) / data.source.video_width * target.width,
                target.y + (data.source.content_y + point.y * data.source.content_height) / data.source.video_height * target.height);
        }

        private void DrawRegionOverlay(Rect target, bool cropped)
        {
            if (data == null) return;
            // The supplementary full original MP4 has no baked detector/ROI graphics.
            // Show every saved ROI outline; keep the selected outline prominent.
            if (data.schema_version == "ten_source_demo_v1")
                for (int z = 0; z < data.zones.Length; z++)
                    DrawZoneOutline(data.zones[z], target, cropped, z == selectedRegion);
            else if (selectedRegion >= 0 && selectedRegion < data.zones.Length)
                DrawZoneOutline(data.zones[selectedRegion], target, cropped, true);
        }

        private void DrawDetectionOverlay(Rect target, bool cropped)
        {
            if (Event.current.type != EventType.Repaint || detectionOverlay == null || data == null ||
                video == null || !CanShowState || !TrafficDemoData.Finite(video.time)) return;
            int frame = data.SourceFrameIndexAt(video.time);
            if (frame < 0 || frame >= detectionOverlay.FrameCount ||
                video.time - data.source.frame_pts_s[frame] > .08) return;
            detectionOverlay.Collect(frame, visibleDetections);
            foreach (var box in visibleDetections)
            {
                float x1 = DetectionCoordinate(box.x1, true, target, cropped);
                float y1 = DetectionCoordinate(box.y1, false, target, cropped);
                float x2 = DetectionCoordinate(box.x2, true, target, cropped);
                float y2 = DetectionCoordinate(box.y2, false, target, cropped);
                Rect bounded = Rect.MinMaxRect(Mathf.Clamp(x1, target.x, target.xMax),
                    Mathf.Clamp(y1, target.y, target.yMax), Mathf.Clamp(x2, target.x, target.xMax),
                    Mathf.Clamp(y2, target.y, target.yMax));
                if (bounded.width < 2 || bounded.height < 2) continue;
                Color color = box.zoneIndex < data.zones.Length ? Hex(data.zones[box.zoneIndex].color_hex) : Hex("A0AAB6");
                const float stroke = 2;
                Fill(new Rect(bounded.x, bounded.y, bounded.width, stroke), color);
                Fill(new Rect(bounded.x, bounded.yMax - stroke, bounded.width, stroke), color);
                Fill(new Rect(bounded.x, bounded.y, stroke, bounded.height), color);
                Fill(new Rect(bounded.xMax - stroke, bounded.y, stroke, bounded.height), color);
                // Tiny distant vehicles keep a visible box without stacking labels.
                if (bounded.width < 48 || bounded.height < 36) continue;
                string caption = "#" + box.trackId + " " + TrafficDetectionOverlay.ClassName(box.classId);
                float tagWidth = Mathf.Min(112, Mathf.Max(55, 16 + caption.Length * 6));
                float tagX = Mathf.Min(bounded.x, target.xMax - tagWidth);
                float tagY = bounded.y > target.y + 17 ? bounded.y - 17 : bounded.y + 2;
                Fill(new Rect(tagX, tagY, tagWidth, 16), color);
                Label(new Rect(tagX + 3, tagY + 1, tagWidth - 6, 14), caption, detectionLabelStyle);
            }
        }

        private float DetectionCoordinate(ushort encoded, bool horizontal, Rect target, bool cropped)
        {
            float sourcePixels = encoded / 65535f * (horizontal ? data.source.video_width : data.source.video_height);
            float origin = cropped ? (horizontal ? data.source.content_x : data.source.content_y) : 0;
            float extent = cropped ? (horizontal ? data.source.content_width : data.source.content_height) :
                (horizontal ? data.source.video_width : data.source.video_height);
            return (horizontal ? target.x : target.y) + (sourcePixels - origin) / extent *
                (horizontal ? target.width : target.height);
        }

        private void DrawZoneOutline(DemoZone zone, Rect target, bool cropped, bool selected)
        {
            for (int i = 0; i < zone.polygon.Length; i++)
            {
                Vector2 a = RegionPointToCanvas(zone.polygon[i], target, cropped);
                Vector2 b = RegionPointToCanvas(zone.polygon[(i + 1) % zone.polygon.Length], target, cropped);
                if (selected) DrawRegionEdge(a, b, Color.white, 5);
                DrawRegionEdge(a, b, Hex(zone.color_hex), selected ? 3 : 2);
            }
        }

        private static void DrawRegionEdge(Vector2 a, Vector2 b, Color color, float width)
        {
            if (Event.current.type != EventType.Repaint) return;
            Matrix4x4 old = GUI.matrix;
            GUI.matrix = old * Matrix4x4.TRS(new Vector3(a.x, a.y, 0),
                Quaternion.Euler(0, 0, Mathf.Atan2(b.y - a.y, b.x - a.x) * Mathf.Rad2Deg), Vector3.one);
            Fill(new Rect(0, -width / 2, Vector2.Distance(a, b), width), color);
            GUI.matrix = old;
        }
    }
}
