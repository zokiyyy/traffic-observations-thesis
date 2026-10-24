#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace TrafficDemo.Editor
{
    public static class TrafficDemoEvaluationBuild
    {
        public static void BuildWebEvaluation() { Build(true); }
        public static void BuildMacEvaluation() { Build(false); }

        private static void Build(bool web)
        {
            if (Resources.Load<TextAsset>("TrafficEvaluation") == null)
                throw new InvalidOperationException("The saved evaluation snapshot is missing.");
            foreach (string id in new[] { "F1018", "F0178", "F0794" })
            {
                string path = "Assets/TrafficDemo/Resources/TrafficEvaluationCases/" + id + ".jpg";
                var importer = AssetImporter.GetAtPath(path) as TextureImporter;
                if (importer == null) throw new InvalidOperationException("Missing evaluation image: " + id);
                importer.textureType = TextureImporterType.Default;
                importer.mipmapEnabled = false;
                importer.npotScale = TextureImporterNPOTScale.None;
                importer.maxTextureSize = 4096;
                importer.textureCompression = TextureImporterCompression.Uncompressed;
                importer.alphaSource = TextureImporterAlphaSource.None;
                importer.filterMode = FilterMode.Bilinear;
                importer.wrapMode = TextureWrapMode.Clamp;
                importer.SaveAndReimport();
                if (Resources.Load<Texture2D>("TrafficEvaluationCases/" + id) == null)
                    throw new InvalidOperationException("Evaluation image could not be imported: " + id);
            }
            TrafficDemoSetup.BuildAndValidateScene();
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string folder = Path.Combine(root, "Builds/Evaluation_2026-10-09_v4");
            string output = web ? Path.Combine(folder, "Web") : Path.Combine(folder, "TrafficObservationsEvaluation.app");
            Directory.CreateDirectory(folder);
            PlayerSettings.productName = "Traffic Observations 10";
            PlayerSettings.bundleVersion = "2026.10.09.4";
            PlayerSettings.defaultScreenWidth = 1800;
            PlayerSettings.defaultScreenHeight = 1000;
            PlayerSettings.runInBackground = true;
            if (web) PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
            else
            {
                PlayerSettings.SetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Standalone, "research.traffic.tenrecordings.evaluation");
                PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
                PlayerSettings.resizableWindow = true;
                PlayerSettings.SetScriptingBackend(UnityEditor.Build.NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
            }
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                scenes = new[] { "Assets/TrafficDemo/Scenes/TrafficReplay.unity" },
                locationPathName = output,
                target = web ? BuildTarget.WebGL : BuildTarget.StandaloneOSX,
                options = BuildOptions.None
            });
            File.WriteAllText(Path.Combine(folder, "build_result.txt"),
                "UTC: " + DateTime.UtcNow.ToString("o") + "\nResult: " + report.summary.result +
                "\nErrors: " + report.summary.totalErrors + "\nWarnings: " + report.summary.totalWarnings + "\nOutput: " + output + "\n");
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("Evaluation build failed: " + report.summary.result);
        }
    }
}
#endif
