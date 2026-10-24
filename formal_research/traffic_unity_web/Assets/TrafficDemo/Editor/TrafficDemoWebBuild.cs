#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace TrafficDemo.Editor
{
    public static class TrafficDemoWebBuild
    {
        public static void BuildTenRecordingWeb()
        {
            Build(false);
        }

        public static void BuildTenRecordingWebDebug()
        {
            Build(true);
        }

        private static void Build(bool development)
        {
            TrafficDemoSetup.BuildAndValidateScene();
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string output = Path.Combine(root, development ? "Builds/TrafficWebTenDebug" : "Builds/TrafficWebTen");
            Directory.CreateDirectory(output);
            PlayerSettings.productName = "Traffic Observations 10";
            PlayerSettings.defaultScreenWidth = 1800;
            PlayerSettings.defaultScreenHeight = 1000;
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                scenes = new[] { "Assets/TrafficDemo/Scenes/TrafficReplay.unity" },
                locationPathName = output,
                target = BuildTarget.WebGL,
                options = development ? BuildOptions.Development : BuildOptions.None
            });
            string record = Path.Combine(root, development ? "Builds/web_debug_build_result.txt" : "Builds/web_build_result.txt");
            File.WriteAllText(record, "UTC: " + DateTime.UtcNow.ToString("o") +
                "\nResult: " + report.summary.result +
                "\nErrors: " + report.summary.totalErrors +
                "\nWarnings: " + report.summary.totalWarnings +
                "\nOutput: " + output + "\n");
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("Web build failed: " + report.summary.result);
        }
    }
}
#endif
