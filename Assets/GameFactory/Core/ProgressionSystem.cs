using System;
using UnityEngine;

namespace GameFactory.Core
{
    public readonly struct StageDefinition
    {
        public readonly int Number;
        public readonly string Name;
        public readonly int TargetDistance;
        public readonly float SpeedMultiplier;

        public StageDefinition(int number, string name, int targetDistance, float speedMultiplier)
        {
            Number = number;
            Name = name;
            TargetDistance = targetDistance;
            SpeedMultiplier = speedMultiplier;
        }
    }

    public readonly struct RunProgressResult
    {
        public readonly int EarnedXp;
        public readonly int Level;
        public readonly bool LeveledUp;
        public readonly int StageStars;
        public readonly bool StageCleared;

        public RunProgressResult(int earnedXp, int level, bool leveledUp, int stageStars, bool stageCleared)
        {
            EarnedXp = earnedXp;
            Level = level;
            LeveledUp = leveledUp;
            StageStars = stageStars;
            StageCleared = stageCleared;
        }
    }

    /// <summary>Persistent stages, player level, experience, and daily login rewards.</summary>
    public static class ProgressionSystem
    {
        public const int StageCount = 10;

        private const string LevelKey = "progress.level";
        private const string XpKey = "progress.xp";
        private const string UnlockedStageKey = "progress.stage_unlocked";
        private const string SelectedStageKey = "progress.stage_selected";
        private const string DailyDateKey = "reward.daily_date";
        private const string DailyStreakKey = "reward.daily_streak";

        private static readonly string[] StageNames =
        {
            "첫 출근", "컨베이어 벨트", "사탕 보일러", "톱니 통로", "야간 교대",
            "초고속 포장", "비밀 창고", "폭주 라인", "공장 옥상", "황금 출하"
        };

        public static StageDefinition GetStage(int number)
        {
            int stage = Mathf.Clamp(number, 1, StageCount);
            return new StageDefinition(stage, StageNames[stage - 1], 150 + (stage - 1) * 75,
                1f + (stage - 1) * 0.035f);
        }

        public static int GetLevel(string gameId) => Mathf.Max(1, SaveSystem.GetInt(gameId, LevelKey, 1));
        public static int GetXp(string gameId) => Mathf.Max(0, SaveSystem.GetInt(gameId, XpKey));
        public static int XpToNextLevel(int level) => 80 + Mathf.Max(0, level - 1) * 35;

        public static int GetUnlockedStage(string gameId) =>
            Mathf.Clamp(SaveSystem.GetInt(gameId, UnlockedStageKey, 1), 1, StageCount);

        public static int GetSelectedStage(string gameId) =>
            Mathf.Clamp(SaveSystem.GetInt(gameId, SelectedStageKey, 1), 1, GetUnlockedStage(gameId));

        public static void SelectStage(string gameId, int stage)
        {
            SaveSystem.SaveInt(gameId, SelectedStageKey, Mathf.Clamp(stage, 1, GetUnlockedStage(gameId)));
        }

        public static int GetStageBest(string gameId, int stage) =>
            Mathf.Max(0, SaveSystem.GetInt(gameId, $"progress.stage.{stage}.best"));

        public static int GetStageStars(string gameId, int stage)
        {
            StageDefinition definition = GetStage(stage);
            int best = GetStageBest(gameId, stage);
            if (best >= definition.TargetDistance) return 3;
            if (best >= Mathf.CeilToInt(definition.TargetDistance * 0.75f)) return 2;
            if (best >= Mathf.CeilToInt(definition.TargetDistance * 0.5f)) return 1;
            return 0;
        }

        public static RunProgressResult CompleteRun(string gameId, int stage, int distance, int coins)
        {
            StageDefinition definition = GetStage(stage);
            int previousBest = GetStageBest(gameId, stage);
            if (distance > previousBest)
                SaveSystem.SaveInt(gameId, $"progress.stage.{stage}.best", distance);

            bool cleared = distance >= definition.TargetDistance;
            if (cleared && stage < StageCount && GetUnlockedStage(gameId) <= stage)
                SaveSystem.SaveInt(gameId, UnlockedStageKey, stage + 1);

            int beforeLevel = GetLevel(gameId);
            int level = beforeLevel;
            int xp = GetXp(gameId) + Mathf.Max(10, distance / 6 + coins * 2);
            int earnedXp = xp - GetXp(gameId);
            while (xp >= XpToNextLevel(level))
            {
                xp -= XpToNextLevel(level);
                level++;
            }

            SaveSystem.SaveInt(gameId, LevelKey, level);
            SaveSystem.SaveInt(gameId, XpKey, xp);
            return new RunProgressResult(earnedXp, level, level > beforeLevel,
                GetStageStars(gameId, stage), cleared);
        }

        /// <summary>Claims once per UTC day. Returns awarded coins, or zero when already claimed.</summary>
        public static int TryClaimDailyReward(string gameId, DateTime utcNow)
        {
            string today = utcNow.ToString("yyyyMMdd");
            if (SaveSystem.GetString(gameId, DailyDateKey) == today) return 0;

            string yesterday = utcNow.AddDays(-1).ToString("yyyyMMdd");
            int streak = SaveSystem.GetString(gameId, DailyDateKey) == yesterday
                ? SaveSystem.GetInt(gameId, DailyStreakKey) + 1
                : 1;
            streak = Mathf.Clamp(streak, 1, 7);
            int reward = 20 + streak * 10;

            SaveSystem.SaveString(gameId, DailyDateKey, today);
            SaveSystem.SaveInt(gameId, DailyStreakKey, streak);
            SaveSystem.AddInt(gameId, ShopKeys.Currency, reward);
            return reward;
        }

        public static bool TutorialComplete(string gameId) =>
            SaveSystem.GetInt(gameId, "tutorial.complete") != 0;

        public static void CompleteTutorial(string gameId) =>
            SaveSystem.SaveInt(gameId, "tutorial.complete", 1);
    }
}
