#if UNITY_EDITOR
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TrafficDemo.Editor
{
    [InitializeOnLoad]
    public sealed class TrafficDemoSetup : EditorWindow
    {
        private const string ScenePath = "Assets/TrafficDemo/Scenes/TrafficReplay.unity";
        static TrafficDemoSetup()
        {
            EditorApplication.delayCall += () =>
            {
                if (!Application.isBatchMode && !EditorApplication.isPlayingOrWillChangePlaymode && !SessionState.GetBool("TrafficDemoWelcomeSeen", false))
                { SessionState.SetBool("TrafficDemoWelcomeSeen", true); ShowWelcome(); }
            };
        }

        [MenuItem("Traffic Demo/Open demo welcome")]
        public static void ShowWelcome()
        {
            var window = GetWindow<TrafficDemoSetup>("Traffic Offline Replay");
            window.minSize = new Vector2(570, 450);
            window.Show();
        }

        private void OnGUI()
        {
            GUILayout.Space(18);
            GUILayout.Label("Five intersections · Offline traffic observations", EditorStyles.boldLabel);
            GUILayout.Space(10);
            EditorGUILayout.HelpBox("Development demo: video synchronized with precomputed window summaries. State estimates are not independently validated; count levels are calibrated within this hour.", MessageType.Info);
            GUILayout.Space(12);
            GUILayout.Label("1. Create or open the scene. The camera, player and dashboard are configured automatically.", EditorStyles.wordWrappedLabel);
            GUILayout.Label("2. Run the demo. The MP4 is verified before the first decoded frame is paused.", EditorStyles.wordWrappedLabel);
            GUILayout.Label("3. Select a region for State details and trends, or use Examples to inspect saved windows. If the video has moved, update its location in Methods & Data.", EditorStyles.wordWrappedLabel);
            GUILayout.Space(18);
            GUI.enabled = !EditorApplication.isPlayingOrWillChangePlaymode;
            if (GUILayout.Button("Create / Open Replay Scene", GUILayout.Height(40))) OpenScene();
            if (GUILayout.Button("Open and Play", GUILayout.Height(40)))
            {
                if (OpenScene()) { EditorApplication.ExecuteMenuItem("Window/General/Game"); EditorApplication.isPlaying = true; }
            }
            GUI.enabled = true;
            GUILayout.Space(16);
            bool exists = File.Exists(Path.Combine(Application.streamingAssetsPath, "traffic_demo.json"));
            EditorGUILayout.HelpBox(exists ? "traffic_demo.json found. Its structure, video hash and timeline are checked when the replay loads." :
                "Assets/StreamingAssets/traffic_demo.json was not found. Add the offline data first; the full video can remain outside this project.", exists ? MessageType.Info : MessageType.Warning);
            GUILayout.Space(12);
            if (GUILayout.Button("Open README_Unity.md"))
                EditorUtility.RevealInFinder(Path.GetFullPath(Path.Combine(Application.dataPath, "../README_Unity.md")));
        }

        [MenuItem("Traffic Demo/Create or Open Replay Scene")]
        public static void GenerateOrOpenScene() { OpenScene(); }

        // Suitable for -executeMethod. Does not quit the Editor; callers choose -quit.
        public static void BuildAndValidateScene()
        {
            string catalogPath = Path.Combine(Application.streamingAssetsPath, "five_sources/catalog.json");
            if (File.Exists(catalogPath))
            {
                var catalog = TrafficDemoCatalog.Load(catalogPath);
                foreach (var entry in catalog.entries)
                    catalog.LoadData(entry);
            }
            else TrafficDemoData.Load(Path.Combine(Application.streamingAssetsPath, "traffic_demo.json"));
            if (!OpenScene()) throw new System.InvalidOperationException("Scene creation was cancelled.");
            Debug.Log("TrafficDemo: JSON structure validated and replay scene created/opened. Playback and video SHA are checked at runtime.");
        }

        [MenuItem("Traffic Demo/Open and Play")]
        public static void LaunchDemo() { Launch(false); }

        [MenuItem("Traffic Demo/Run Real Player Verification")]
        public static void LaunchAndVerifyDemo() { Launch(true); }

        [MenuItem("Traffic Demo/Launch Presentation and Verify")]
        public static void LaunchPresentationAndVerify()
        {
            if (Application.isBatchMode)
                throw new System.InvalidOperationException("Presentation verification requires a visible Editor, without -batchmode or -quit.");
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new System.InvalidOperationException("Stop the current Play session before launching presentation verification.");
            // Explicit opt-in: a named fixed Game-view size avoids low-resolution aspect emulation.
            // This adds/updates an Editor resolution entry, without changing the system display.
            EditorApplication.ExecuteMenuItem("Window/General/Game");
            PlayModeWindow.SetViewType(PlayModeWindow.PlayModeViewTypes.GameView);
            PlayModeWindow.SetCustomRenderingResolution(2400, 1334, "Traffic presentation");
            PlayModeWindow.GetRenderingResolution(out uint width, out uint height);
            Debug.Log("TrafficDemo presentation rendering resolution: " + width + " x " + height);
            LaunchAndVerifyDemo();
        }

        private static void Launch(bool verify)
        {
            if (Application.isBatchMode)
                throw new System.InvalidOperationException("LaunchDemo requires a visible Editor, without -batchmode or -quit.");
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new System.InvalidOperationException("Stop the current Play session before launching the demo.");
            SessionState.SetBool("TrafficDemoWelcomeSeen", true);
            foreach (var welcome in Resources.FindObjectsOfTypeAll<TrafficDemoSetup>()) welcome.Close();
            BuildAndValidateScene();
            SessionState.SetBool("TrafficDemoVerifyRequested", verify);
            EditorApplication.ExecuteMenuItem("Window/General/Game");
            var gameView = EditorWindow.focusedWindow;
            if (gameView != null && gameView.GetType().Name == "GameView") gameView.maximized = true;
            EditorApplication.isPlaying = true;
        }

        public static bool OpenScene()
        {
            if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return false;
            if (File.Exists(ScenePath)) { EditorSceneManager.OpenScene(ScenePath); return true; }
            Directory.CreateDirectory("Assets/TrafficDemo/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var cameraObject = new GameObject("Display Camera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.04f, 0.06f, 0.10f);
            camera.cullingMask = 0;
            new GameObject("Traffic Replay").AddComponent<TrafficReplayController>();
            EditorSceneManager.SaveScene(scene, ScenePath);
            var current = EditorBuildSettings.scenes;
            bool registered = false;
            foreach (var item in current) if (item.path == ScenePath) registered = true;
            if (!registered)
            {
                var scenes = new EditorBuildSettingsScene[current.Length + 1];
                current.CopyTo(scenes, 0);
                scenes[current.Length] = new EditorBuildSettingsScene(ScenePath, true);
                EditorBuildSettings.scenes = scenes;
            }
            AssetDatabase.Refresh();
            return true;
        }
    }
}
#endif
