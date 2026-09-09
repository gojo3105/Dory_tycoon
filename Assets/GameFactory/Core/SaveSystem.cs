using UnityEngine;

namespace GameFactory.Core
{
    /// <summary>
    /// Thin PlayerPrefs wrapper, namespaced per game id so multiple generated
    /// games can share one device without colliding on save keys.
    /// </summary>
    public static class SaveSystem
    {
        public static int GetBestScore(string gameId)
        {
            return PlayerPrefs.GetInt(BestScoreKey(gameId), 0);
        }

        /// <summary>Stores score as the new best if it beats the current one. Returns the resulting best.</summary>
        public static int SaveBestScore(string gameId, int score)
        {
            int best = GetBestScore(gameId);
            if (score > best)
            {
                best = score;
                PlayerPrefs.SetInt(BestScoreKey(gameId), best);
                PlayerPrefs.Save();
            }

            return best;
        }

        public static void SaveInt(string gameId, string key, int value)
        {
            PlayerPrefs.SetInt(Key(gameId, key), value);
            PlayerPrefs.Save();
        }

        public static int GetInt(string gameId, string key, int defaultValue = 0)
        {
            return PlayerPrefs.GetInt(Key(gameId, key), defaultValue);
        }

        public static int AddInt(string gameId, string key, int amount)
        {
            int value = GetInt(gameId, key) + amount;
            SaveInt(gameId, key, value);
            return value;
        }

        public static void SaveString(string gameId, string key, string value)
        {
            PlayerPrefs.SetString(Key(gameId, key), value ?? string.Empty);
            PlayerPrefs.Save();
        }

        public static string GetString(string gameId, string key, string defaultValue = "")
        {
            return PlayerPrefs.GetString(Key(gameId, key), defaultValue);
        }

        private static string BestScoreKey(string gameId) => Key(gameId, "best_score");

        private static string Key(string gameId, string key) => $"{gameId}.{key}";
    }
}
