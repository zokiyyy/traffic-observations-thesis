using System;
using System.Globalization;
using UnityEngine;

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        [Serializable]
        private sealed class EvaluationClass
        {
            public string name;
            public int objects;
            public double precision, recall, ap50, ap50_95;
        }

        [Serializable]
        private sealed class EvaluationTracking
        {
            public string label;
            public int ids, short_tracks;
        }

        [Serializable]
        private sealed class EvaluationPersistenceRow
        {
            public int seconds, high_windows, joint_windows;
        }

        [Serializable]
        private sealed class EvaluationPersistence
        {
            public string test_date;
            public int recordings, regions, window_seconds, region_windows, eligible_windows, excluded_windows, reference_seconds;
            public bool reference_matches_saved;
            public EvaluationPersistenceRow[] rows;
        }

        [Serializable]
        private sealed class EvaluationThresholdRow
        {
            public double confidence, precision, recall, f1, count_mae, class_mean_f1, night_f1;
            public int predictions, tp, fp, fn;
        }

        [Serializable]
        private sealed class EvaluationThresholdStudy
        {
            public string test_date, detector;
            public int images, objects, source_videos, empty_label_images, image_size;
            public double nms_iou, match_iou, car_label_share;
            public EvaluationThresholdRow[] rows;
        }

        [Serializable]
        private sealed class EvaluationDetectionCase
        {
            public string id, selector, title, resource, camera, lighting, caption;
            public bool cropped;
        }

        [Serializable]
        private sealed class EvaluationSnapshot
        {
            public string schema_version, test_date, detector, tracking_clip, tracking_detector;
            public int images, objects, source_videos, tracking_seconds, tracking_frames, short_track_max_frames;
            public double precision, recall, map50, map50_95;
            public EvaluationClass[] classes;
            public EvaluationTracking[] tracking;
            public EvaluationPersistence persistence;
            public EvaluationThresholdStudy threshold_study;
            public EvaluationDetectionCase[] detection_cases;
        }

        private EvaluationSnapshot evaluationSnapshot;
        private bool evaluationLoadAttempted;
        private int evaluationPage, selectedDetectionCase;
        private Texture2D[] evaluationCaseTextures;
        private readonly string[] evaluationTabs = { "Detection & tracking", "Detection thresholds", "Detection cases", "20 / 30 / 60 s rules" };

        private void LoadEvaluationSnapshot()
        {
            if (evaluationLoadAttempted) return;
            evaluationLoadAttempted = true;
            var asset = Resources.Load<TextAsset>("TrafficEvaluation");
            if (asset == null) return;
            try
            {
                var snapshot = JsonUtility.FromJson<EvaluationSnapshot>(asset.text);
                if (snapshot == null || snapshot.schema_version != "traffic_evaluation_v3" ||
                    snapshot.classes == null || snapshot.classes.Length != 4 ||
                    snapshot.tracking == null || snapshot.tracking.Length != 2 ||
                    snapshot.images <= 0 || snapshot.objects <= 0) return;
                evaluationSnapshot = snapshot;
            }
            catch (Exception e) { Debug.LogWarning("Saved evaluation unavailable: " + e.Message); }
        }

        private static string EvaluationPercent(double value)
        {
            return TrafficDemoData.Finite(value) ? (value * 100).ToString("0.0", CultureInfo.InvariantCulture) + "%" : "--";
        }

        private const float EvaluationWidth = 536;
        private readonly Vector2[] evaluationScroll = new Vector2[4];
        private readonly float[] evaluationHeights = { 1800, 1800, 1200, 1600 };
        private Vector2 evaluationImageScroll;
        private int evaluationImageZoom = 1;
        private GUIStyle evaluationTextStyle, evaluationSmallStyle;

        private void EvaluationText(ref float y, string text, GUIStyle style = null)
        {
            if (style == null) style = evaluationTextStyle;
            float height = style.CalcHeight(new GUIContent(text), EvaluationWidth);
            Label(new Rect(0, y, EvaluationWidth, height), text, style);
            y += height + 12;
        }

        private void EvaluationHeading(ref float y, string text)
        {
            if (y > 0) { y += 6; Fill(new Rect(0, y, EvaluationWidth, 1), line); y += 14; }
            EvaluationText(ref y, text, strongBodyStyle);
        }

        private void EvaluationMetricTile(float x, float y, string label, double value)
        {
            Round(new Rect(x, y, 260, 80), Hex("F0F6F7"));
            Label(new Rect(x + 12, y + 10, 236, 24), label, smallInkStyle);
            Label(new Rect(x + 12, y + 35, 236, 40), EvaluationPercent(value), compactMetricStyle);
        }

        private void DrawEvaluationPanel()
        {
            LoadEvaluationSnapshot();
            if (evaluationTextStyle == null)
            {
                evaluationTextStyle = TextStyle(16, ink);
                evaluationSmallStyle = TextStyle(14, muted);
            }
            Card(regions);
            float x = regions.x + 16, y = regions.y;
            Label(new Rect(x, y + 13, 320, 30), "Experiment Evaluation", headingStyle);
            if (GUI.Button(new Rect(regions.xMax - 184, y + 12, 168, 32), "Back to regions", sourceButtonStyle))
            { showEvaluation = false; showInsights = false; selectedRegion = -1; }
            Label(new Rect(x, y + 51, 562, 40),
                "Saved evaluation results — not live accuracy for the current recording.", smallInkStyle);
            for (int i = 0; i < evaluationTabs.Length; i++)
            {
                if (GUI.Button(new Rect(x + (i % 2) * 286, y + 98 + (i / 2) * 40, 276, 34),
                    evaluationTabs[i], evaluationPage == i ? sourceSelectedButtonStyle : sourceButtonStyle))
                    evaluationPage = i;
            }
            var viewport = new Rect(x, y + 184, 562, regions.height - 211);
            evaluationScroll[evaluationPage] = GUI.BeginScrollView(viewport, evaluationScroll[evaluationPage],
                new Rect(0, 0, EvaluationWidth, evaluationHeights[evaluationPage]), false, true);
            float top = 0;
            var saved = evaluationSnapshot;
            if (saved == null) EvaluationText(ref top, "Saved evaluation data is unavailable. Video replay remains available.");
            else if (evaluationPage == 1) DrawThresholdEvaluation(saved.threshold_study, ref top);
            else if (evaluationPage == 2) DrawDetectionCases(saved.detection_cases, ref top);
            else if (evaluationPage == 3) DrawPersistenceEvaluation(saved.persistence, ref top);
            else DrawDetectorTrackingEvaluation(saved, ref top);
            GUI.EndScrollView();
            evaluationHeights[evaluationPage] = Mathf.Max(viewport.height, top + 8);
            Label(new Rect(x, regions.yMax - 22, 562, 18), "Scroll inside this panel for results and interpretation.", tinyStyle);
        }

        private void DrawDetectorTrackingEvaluation(EvaluationSnapshot saved, ref float y)
        {
            EvaluationHeading(ref y, saved.detector + " · Independent Main Test");
            EvaluationText(ref y, saved.images + " images · " + saved.objects.ToString("N0", CultureInfo.InvariantCulture) +
                " annotated vehicles · " + saved.source_videos + " held-out videos\nTested " + saved.test_date, evaluationSmallStyle);
            EvaluationMetricTile(0, y, "Precision (macro)", saved.precision);
            EvaluationMetricTile(276, y, "Recall (macro)", saved.recall);
            EvaluationMetricTile(0, y + 90, "mAP@0.50", saved.map50);
            EvaluationMetricTile(276, y + 90, "mAP@0.50:0.95", saved.map50_95);
            y += 184;
            EvaluationText(ref y, "Class averages at the evaluator-selected operating point, not at the video's tracking threshold. This still-image test does not evaluate the replay video's complete pipeline.", evaluationSmallStyle);
            EvaluationHeading(ref y, "Results by vehicle class");
            foreach (var item in saved.classes)
            {
                Round(new Rect(0, y, EvaluationWidth, 95), Hex("F4F6F9"));
                Label(new Rect(12, y + 8, 512, 24), item.name + " · " + item.objects.ToString("N0", CultureInfo.InvariantCulture) + " labels", strongBodyStyle);
                Label(new Rect(12, y + 37, 512, 24), "Precision " + EvaluationPercent(item.precision) + "    Recall " + EvaluationPercent(item.recall), evaluationTextStyle);
                Label(new Rect(12, y + 65, 512, 24), "AP@0.50 " + EvaluationPercent(item.ap50) + "    AP@0.50:0.95 " + EvaluationPercent(item.ap50_95), evaluationTextStyle);
                y += 105;
            }
            EvaluationText(ref y, "Motorcycle and bus results have small sample counts. Interpret the overall score together with these class results.", evaluationSmallStyle);
            EvaluationHeading(ref y, "Tracking diagnostics · Early pilot");
            EvaluationText(ref y, saved.tracking_seconds + "-second " + saved.tracking_clip + " clip · " +
                saved.tracking_frames.ToString("N0", CultureInfo.InvariantCulture) + " frames\n" + saved.tracking_detector);
            foreach (var item in saved.tracking)
            {
                EvaluationText(ref y, item.label, strongBodyStyle);
                EvaluationText(ref y, item.ids + " track IDs · " + item.short_tracks + " short tracks (at most " + saved.short_track_max_frames + " frames)");
            }
            EvaluationText(ref y, "Fewer IDs and short tracks do not establish more accurate vehicle identities. These are counts of saved outputs.");
            EvaluationText(ref y, "Tracking accuracy: not independently evaluated", strongBodyStyle);
            EvaluationText(ref y, "Independent trajectory labels were unavailable, so IDF1 and MOTA are not reported. This early pilot is supplementary evidence, not an accuracy test of the ten-recording replay.", evaluationSmallStyle);
        }

        private static string ThresholdPercent(double value)
        {
            return TrafficDemoData.Finite(value) ? (value * 100).ToString("0.00", CultureInfo.InvariantCulture) + "%" : "--";
        }

        private void EvaluationTableRow(ref float y, string[] values, float[] widths, bool heading = false, bool highlight = false)
        {
            GUIStyle style = heading ? evaluationSmallStyle : evaluationTextStyle;
            float height = 32;
            for (int i = 0; i < values.Length; i++) height = Mathf.Max(height, style.CalcHeight(new GUIContent(values[i]), widths[i] - 12) + 16);
            Round(new Rect(0, y, EvaluationWidth, height), highlight ? Hex("EAF5F4") : Hex("F4F6F9"));
            float x = 0;
            for (int i = 0; i < values.Length; i++)
            {
                Label(new Rect(x + 6, y + 8, widths[i] - 12, height - 12), values[i], style);
                x += widths[i];
            }
            y += height + 6;
        }

        private void DrawThresholdEvaluation(EvaluationThresholdStudy saved, ref float y)
        {
            if (saved == null || saved.rows == null || saved.rows.Length != 3)
            { EvaluationText(ref y, "Saved detection threshold comparison is unavailable."); return; }
            EvaluationHeading(ref y, saved.detector + " · Validation thresholds");
            EvaluationText(ref y, saved.images + " images · " + saved.objects.ToString("N0", CultureInfo.InvariantCulture) +
                " labels · " + saved.source_videos + " videos\nSame weights and images at every threshold\nSaved " + saved.test_date, evaluationSmallStyle);
            float[] widths = { 94, 112, 110, 110, 110 };
            EvaluationTableRow(ref y, new[] { "Conf.", "Precision\n(micro)", "Recall\n(micro)", "F1\n(micro)", "Count\nMAE" }, widths, true);
            foreach (var row in saved.rows)
                EvaluationTableRow(ref y, new[] { row.confidence.ToString("0.00", CultureInfo.InvariantCulture), ThresholdPercent(row.precision),
                    ThresholdPercent(row.recall), ThresholdPercent(row.f1), row.count_mae.ToString("0.000", CultureInfo.InvariantCulture) }, widths, false, Math.Abs(row.confidence - .35) < .0001);
            EvaluationHeading(ref y, "Matches and unmatched boxes");
            float[] counts = { 92, 148, 148, 148 };
            EvaluationTableRow(ref y, new[] { "Conf.", "Correct\nTP", "Unmatched\npredictions FP", "Unmatched\nlabels FN" }, counts, true);
            foreach (var row in saved.rows)
                EvaluationTableRow(ref y, new[] { row.confidence.ToString("0.00", CultureInfo.InvariantCulture), row.tp.ToString("N0", CultureInfo.InvariantCulture),
                    row.fp.ToString("N0", CultureInfo.InvariantCulture), row.fn.ToString("N0", CultureInfo.InvariantCulture) }, counts, false, Math.Abs(row.confidence - .35) < .0001);
            var lower = saved.rows[1]; var higher = saved.rows[2];
            EvaluationHeading(ref y, "Tradeoffs and scope");
            EvaluationText(ref y, "0.25 to 0.35: " + (lower.fp - higher.fp) + " fewer unmatched predictions, but " + (lower.tp - higher.tp) + " fewer correct matches.");
            EvaluationText(ref y, "Four-class mean F1: " + ThresholdPercent(lower.class_mean_f1) + " to " + ThresholdPercent(higher.class_mean_f1) +
                "\nNight F1: " + ThresholdPercent(lower.night_f1) + " to " + ThresholdPercent(higher.night_f1));
            EvaluationText(ref y, "0.35 has the highest pooled F1 of these three thresholds. Cars make up " + EvaluationPercent(saved.car_label_share) +
                " of labels; this does not establish improvement for every class or lighting group.");
            EvaluationText(ref y, "Micro metrics pool TP / FP / FN. They use a different dataset and averaging method from Main Test class averages; this comparison is not mAP.");
            EvaluationText(ref y, "Box-count MAE compares full-image prediction and annotation counts. It does not measure road-region counts, vehicle flow or traffic-state accuracy.");
            EvaluationHeading(ref y, "Method");
            EvaluationText(ref y, "Image size " + saved.image_size + " · NMS IoU " + saved.nms_iou.ToString("0.00", CultureInfo.InvariantCulture) +
                " · " + saved.empty_label_images + " empty-label images retained.");
            EvaluationText(ref y, "Correct matches require the same vehicle class and box IoU >= " + saved.match_iou.ToString("0.00", CultureInfo.InvariantCulture) +
                ". Each prediction and annotation can match once; pairs are matched in descending IoU order.");
            EvaluationText(ref y, "Predictions cover the full image; labels cover roadway vehicles. An unmatched prediction may be a real vehicle outside the annotation scope.");
            EvaluationText(ref y, "Saved validation diagnostic. No tracking in this study. Viewing these results does not change any replay threshold.", evaluationSmallStyle);
        }

        private void DrawDetectionCases(EvaluationDetectionCase[] cases, ref float y)
        {
            if (cases == null || cases.Length != 3)
            { EvaluationText(ref y, "Saved detection case images are unavailable."); return; }
            if (evaluationCaseTextures == null) evaluationCaseTextures = new Texture2D[cases.Length];
            selectedDetectionCase = Mathf.Clamp(selectedDetectionCase, 0, cases.Length - 1);
            EvaluationHeading(ref y, "Detection cases · Validation examples");
            for (int i = 0; i < cases.Length; i++)
                if (GUI.Button(new Rect(i * 181, y, 174, 32), cases[i].id,
                    selectedDetectionCase == i ? sourceSelectedButtonStyle : sourceButtonStyle))
                { selectedDetectionCase = i; evaluationImageScroll = Vector2.zero; }
            y += 44;
            var selected = cases[selectedDetectionCase];
            EvaluationText(ref y, selected.title, strongBodyStyle);
            EvaluationText(ref y, selected.camera + " · " + selected.lighting + (selected.cropped ? "\nCropped detail; printed counts refer to the full image." : "\nFull-image comparison."), evaluationSmallStyle);
            Label(new Rect(0, y, 202, 28), "Image size", smallInkStyle);
            for (int zoom = 1; zoom <= 3; zoom++)
                if (GUI.Button(new Rect(224 + (zoom - 1) * 104, y, 96, 28), zoom == 1 ? "Fit" : zoom + "x",
                    evaluationImageZoom == zoom ? sourceSelectedButtonStyle : sourceButtonStyle))
                { evaluationImageZoom = zoom; evaluationImageScroll = Vector2.zero; }
            y += 38;
            if (evaluationCaseTextures[selectedDetectionCase] == null) evaluationCaseTextures[selectedDetectionCase] = Resources.Load<Texture2D>(selected.resource);
            var texture = evaluationCaseTextures[selectedDetectionCase];
            var imageArea = new Rect(0, y, EvaluationWidth, 260);
            Round(imageArea, Hex("F4F6F9"));
            if (texture != null)
            {
                if (evaluationImageZoom == 1) GUI.DrawTexture(imageArea, texture, ScaleMode.ScaleToFit);
                else
                {
                    float ratio = Mathf.Min((EvaluationWidth - 18) / texture.width, 242f / texture.height) * evaluationImageZoom;
                    Rect content = new Rect(0, 0, texture.width * ratio, texture.height * ratio);
                    evaluationImageScroll = GUI.BeginScrollView(imageArea, evaluationImageScroll, content);
                    GUI.DrawTexture(content, texture, ScaleMode.ScaleToFit);
                    GUI.EndScrollView();
                }
            }
            else Label(new Rect(12, y + 12, EvaluationWidth - 24, 90), "Saved image unavailable. Its source result is described below.", evaluationTextStyle);
            y += 272;
            EvaluationText(ref y, "Use 2x or 3x and the image scrollbars to inspect boxes.", evaluationSmallStyle);
            EvaluationText(ref y, selected.caption);
            EvaluationText(ref y, "Cyan: annotations · Green: correct class matches · Orange: unmatched predictions. Selected qualitative examples, not the current replay frame.", evaluationSmallStyle);
        }

        private static string PersistenceChange(int count, int reference)
        {
            if (reference <= 0) return "--";
            double change = (count - reference) * 100.0 / reference;
            return (change > 0 ? "+" : "") + change.ToString("0.0", CultureInfo.InvariantCulture) + "% vs 30 s";
        }

        private void DrawPersistenceEvaluation(EvaluationPersistence saved, ref float y)
        {
            if (saved == null || saved.rows == null || saved.rows.Length != 3 ||
                saved.rows[0].seconds != 20 || saved.rows[1].seconds != 30 || saved.rows[2].seconds != 60 || saved.reference_seconds != 30)
            { EvaluationText(ref y, "Saved persistence comparison is unavailable."); return; }
            EvaluationHeading(ref y, "Persistence · 20 / 30 / 60 seconds");
            EvaluationText(ref y, "Initial " + saved.recordings + " recordings · " + saved.regions + " regions · " + saved.window_seconds +
                "-second windows\nSaved " + saved.test_date, evaluationSmallStyle);
            EvaluationText(ref y, "Only the required continuous duration changes; count, motion and evidence rules stay fixed.");
            var reference = saved.rows[1];
            float[] widths = { 102, 211, 223 };
            EvaluationTableRow(ref y, new[] { "Duration", "High-count hint\nRegion-windows", "Of those: joint hint\nRegion-windows" }, widths, true);
            foreach (var row in saved.rows)
                EvaluationTableRow(ref y, new[] { row.seconds + " s", row.high_windows.ToString("N0", CultureInfo.InvariantCulture), row.joint_windows.ToString("N0", CultureInfo.InvariantCulture) }, widths, false, row.seconds == 30);
            EvaluationText(ref y, "30 s is the replay rule. Joint hints are a subset of high-count hints; do not add the columns.", evaluationSmallStyle);
            foreach (var row in saved.rows)
                if (row.seconds != 30) EvaluationText(ref y, row.seconds + " s — high-count " + PersistenceChange(row.high_windows, reference.high_windows) +
                    "; joint " + PersistenceChange(row.joint_windows, reference.joint_windows), evaluationSmallStyle);
            EvaluationHeading(ref y, "How to interpret the totals");
            EvaluationText(ref y, "One region-window is one road region in one 10-second interval. A continuing hint spans several windows. These totals are not vehicle counts, independent congestion events or elapsed video time.");
            EvaluationText(ref y, "20 s produces more hints, including shorter-lived changes. This does not establish fewer missed events.\n\n30 s lies between the shorter and longer requirements; hint counts do not establish an optimal duration.\n\n60 s produces fewer hints and filters out shorter changes. This does not establish greater accuracy.");
            EvaluationText(ref y, saved.region_windows.ToString("N0", CultureInfo.InvariantCulture) + " region-windows total\n" +
                saved.eligible_windows.ToString("N0", CultureInfo.InvariantCulture) + " complete and unheld · " + saved.excluded_windows + " excluded from persistence accumulation.");
            if (saved.reference_matches_saved) EvaluationText(ref y, "30-second recalculation matches the saved hints.", activeSmallStyle);
            EvaluationText(ref y, "NE12th night has no hints because count calibration is insufficient. Zero hints do not establish free flow.");
            EvaluationText(ref y, "Rule sensitivity, not traffic-state accuracy. Initial five recordings only; separate from the selected camera and ten-recording replay totals.", evaluationSmallStyle);
        }
    }
}
