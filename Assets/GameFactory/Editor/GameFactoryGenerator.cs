using System;
using System.Collections.Generic;
using System.IO;
using GameFactory.Core.Spec;
using UnityEditor;
using UnityEngine;

namespace GameFactory.Editor
{
    /// <summary>
    /// Top-level entry point for turning one GameSpec JSON file into a
    /// playable, saved Unity scene. Dispatches to the genre-specific
    /// generator (only Runner exists so far), then writes the Resources copy
    /// of the spec and a small metadata manifest under GeneratedGames/.
    ///
    /// CLI usage (see docs/AUTOMATION.md):
    ///   Unity -batchmode -projectPath . -executeMethod
    ///     GameFactory.Editor.GameFactoryGenerator.GenerateFromCommandLine
    ///     -gameSpec GameSpecs/game01.json -quit
    /// </summary>
    public static class GameFactoryGenerator
    {
        [MenuItem("Game Factory/Generate/Factory Runner Sample")]
        private static void GenerateFactoryRunnerSampleMenuItem()
        {
            try
            {
                string scenePath = Generate("GameSpecs/game01.json");
                EditorUtility.DisplayDialog("Game Factory", $"Generated:\n{scenePath}", "OK");
            }
            catch (Exception e)
            {
                Debug.LogError(e);
                EditorUtility.DisplayDialog("Game Factory", $"Generation failed:\n{e.Message}", "OK");
            }
        }

        /// <summary>
        /// CLI entry point driven by -executeMethod. Always exits Unity itself
        /// (success or failure) via CommandLineExit so the CI wrapper script
        /// polling for Logs/generate.exitcode can tell the run is truly done -
        /// see scripts/ci/wait-for-unity.ps1 for why this can't just rely on
        /// -quit/the calling process's own exit code.
        /// </summary>
        public static void GenerateFromCommandLine()
        {
            string path = GetCommandLineArg("-gameSpec");
            if (string.IsNullOrEmpty(path))
            {
                Debug.LogError("[GameFactoryGenerator] Missing -gameSpec <path> command line argument.");
                CommandLineExit.Exit(1, "generate");
                return;
            }

            try
            {
                Generate(path);
                CommandLineExit.Exit(0, "generate");
            }
            catch (Exception e)
            {
                Debug.LogError($"[GameFactoryGenerator] Generation failed: {e}");
                CommandLineExit.Exit(1, "generate");
            }
        }

        public static string Generate(string gameSpecPath)
        {
            string absoluteSpecPath = Path.IsPathRooted(gameSpecPath)
                ? gameSpecPath
                : EditorPaths.ToAbsolutePath(gameSpecPath);

            GameSpec spec = GameSpecParser.LoadFromFile(absoluteSpecPath);

            ValidationResult validation = GameSpecValidator.Validate(spec);
            if (!validation.IsValid)
            {
                throw new GameSpecException(
                    $"GameSpec '{gameSpecPath}' failed validation:\n- " + string.Join("\n- ", validation.Errors));
            }

            if (!Enum.TryParse(spec.game.genre, true, out GameGenre genre))
            {
                throw new GameSpecException($"Unsupported genre '{spec.game.genre}' in '{gameSpecPath}'.");
            }

            if (genre != GameGenre.Runner)
            {
                throw new GameSpecException(
                    $"Genre '{genre}' does not have a scene generator yet (only Runner is implemented).");
            }

            string gameFolder = $"Assets/GeneratedGames/{spec.game.id}";
            RunnerPrefabSet prefabs = PrefabGenerator.GenerateRunnerPrefabs(spec, $"{gameFolder}/Prefabs");
            string scenePath = SceneGenerator.GenerateRunnerScene(spec, prefabs, $"{gameFolder}/Scenes");

            CopySpecToResources(absoluteSpecPath, spec.game.id);
            WriteGeneratedGameManifest(spec, scenePath, CollectPrefabPaths(prefabs));

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();

            Debug.Log($"[GameFactoryGenerator] Generated '{spec.game.id}' -> {scenePath}");
            return scenePath;
        }

        private static string[] CollectPrefabPaths(RunnerPrefabSet prefabs)
        {
            var paths = new List<string>
            {
                AssetDatabase.GetAssetPath(prefabs.Player),
                AssetDatabase.GetAssetPath(prefabs.GroundTile),
                AssetDatabase.GetAssetPath(prefabs.Obstacle),
                AssetDatabase.GetAssetPath(prefabs.Coin),
            };

            if (prefabs.ObstacleOverhead != null)
            {
                paths.Add(AssetDatabase.GetAssetPath(prefabs.ObstacleOverhead));
            }

            if (prefabs.GravityZone != null)
            {
                paths.Add(AssetDatabase.GetAssetPath(prefabs.GravityZone));
            }

            return paths.ToArray();
        }

        private static void CopySpecToResources(string absoluteSourcePath, string gameId)
        {
            const string resourcesFolder = "Assets/Resources/GameSpecs";
            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(resourcesFolder));

            string destAssetPath = $"{resourcesFolder}/{gameId}.json";
            File.Copy(absoluteSourcePath, EditorPaths.ToAbsolutePath(destAssetPath), overwrite: true);
            AssetDatabase.ImportAsset(destAssetPath);
        }

        private static void WriteGeneratedGameManifest(GameSpec spec, string scenePath, string[] prefabPaths)
        {
            GeneratedGameManifest manifest = GeneratedGameManifest.Load(spec.game.id) ?? new GeneratedGameManifest();
            manifest.gameId = spec.game.id;
            manifest.title = spec.game.title;
            manifest.genre = spec.game.genre;
            manifest.generatedAtUtc = DateTime.UtcNow.ToString("o");
            manifest.scenePath = scenePath;
            manifest.prefabPaths = prefabPaths;
            manifest.Save(spec.game.id);
        }

        private static string GetCommandLineArg(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name) return args[i + 1];
            }

            return null;
        }
    }
}
