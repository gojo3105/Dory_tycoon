using System.IO;
using GameFactory.Core;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace GameFactory.Editor
{
    public static class DashboardSceneGenerator
    {
        public const string ScenePath = "Assets/GeneratedGames/dashboard/Scenes/dashboard.unity";

        [MenuItem("Game Factory/Generate/AI Dashboard Scene")]
        public static void Generate()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath) ?? "Assets/GeneratedGames/dashboard/Scenes");
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            GameObject controller = new GameObject("Dashboard WebView");
            controller.AddComponent<DashboardWebViewController>();
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.Refresh();
            Debug.Log($"[Dashboard] Generated {ScenePath}");
        }
    }
}
