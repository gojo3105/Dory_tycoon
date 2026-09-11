using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace GameFactory.Editor
{
    public static class BuildDashboard
    {
        [MenuItem("Game Factory/Build/AI Dashboard (APK)")]
        private static void BuildMenuItem()
        {
            bool success = BuildAndReport();
            EditorUtility.DisplayDialog("AI Dashboard Build",
                success ? "APK build succeeded. See Builds/dashboard/." : "APK build failed. See Logs/dashboard-build.log.",
                "OK");
        }

        public static void BuildFromCommandLine()
        {
            bool success = BuildAndReport();
            CommandLineExit.Exit(success ? 0 : 1, "dashboard-build");
        }

        public static bool BuildAndReport()
        {
            try
            {
                string output = Build();
                Debug.Log($"[Dashboard] Build succeeded: {output}");
                return true;
            }
            catch (Exception exception)
            {
                Debug.LogError($"[Dashboard] Build failed: {exception}");
                return false;
            }
        }

        public static string Build()
        {
            string scenePath = DashboardSceneGenerator.ScenePath;
            string absoluteScenePath = Path.GetFullPath(scenePath);
            if (!File.Exists(absoluteScenePath))
            {
                DashboardSceneGenerator.Generate();
            }

            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);
            PlayerSettings.productName = "Dory AI Dashboard";
            PlayerSettings.applicationIdentifier = "com.gamefactory.dorydashboard";
            PlayerSettings.defaultInterfaceOrientation = UIOrientation.Portrait;
            PlayerSettings.Android.forceInternetPermission = true;
            PlayerSettings.Android.bundleVersionCode = 3;

            string outputFolder = Path.Combine("Builds", "dashboard", "APK");
            Directory.CreateDirectory(outputFolder);
            string outputPath = Path.Combine(outputFolder, "DoryAIDashboard-v3.apk");
            BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { scenePath },
                locationPathName = outputPath,
                target = BuildTarget.Android,
                options = BuildOptions.None
            });

            if (report.summary.result != BuildResult.Succeeded)
            {
                throw new InvalidOperationException(
                    $"Dashboard APK build failed: {report.summary.result}, {report.summary.totalErrors} error(s).");
            }

            return outputPath;
        }
    }
}
