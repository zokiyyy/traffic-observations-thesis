#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
namespace TrafficDemo.Editor
{
    public static class TrafficDemoMultiBuild
    {
        public static void BuildMacTenSources()
        {
            TrafficDemoSetup.BuildAndValidateScene();
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string output = Path.Combine(root, "Builds/TenRecordings_2026-09-28_v1/TrafficObservationsTen.app");
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            PlayerSettings.productName = "Traffic Observations 10";
            PlayerSettings.SetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Standalone, "research.traffic.tenrecordings");
            PlayerSettings.bundleVersion = "2026.09.28.3";
            PlayerSettings.defaultScreenWidth = 1800;
            PlayerSettings.defaultScreenHeight = 1000;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.resizableWindow = true;
            PlayerSettings.runInBackground = true;
            PlayerSettings.SetScriptingBackend(UnityEditor.Build.NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
            EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Player;
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                scenes = new[] { "Assets/TrafficDemo/Scenes/TrafficReplay.unity" },
                locationPathName = output, target = BuildTarget.StandaloneOSX, options = BuildOptions.None
            });
            string evidence = Path.Combine(root, "Verification/ten_sources/build_result.txt");
            Directory.CreateDirectory(Path.GetDirectoryName(evidence));
            File.WriteAllText(evidence, "UTC: " + DateTime.UtcNow.ToString("o") + "\nResult: " + report.summary.result +
                "\nErrors: " + report.summary.totalErrors + "\nWarnings: " + report.summary.totalWarnings + "\nOutput: " + output + "\n");
            if (report.summary.result != BuildResult.Succeeded) throw new InvalidOperationException("Mac build failed: " + report.summary.result);
        }
    }
}
#endif
