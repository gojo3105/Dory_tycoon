using System;
using UnityEngine;

namespace GameFactory.Core
{
    public enum MissionKind { Distance, Coins, Combo }

    public readonly struct MissionDefinition
    {
        public readonly string Id;
        public readonly string Title;
        public readonly MissionKind Kind;
        public readonly int Target;
        public readonly int Reward;

        public MissionDefinition(string id, string title, MissionKind kind, int target, int reward)
        {
            Id = id;
            Title = title;
            Kind = kind;
            Target = target;
            Reward = reward;
        }
    }

    /// <summary>Three rotating daily goals with persistent progress and explicit reward claims.</summary>
    public static class MissionSystem
    {
        public static readonly MissionDefinition[] DailyMissions =
        {
            new MissionDefinition("distance", "누적 500m 달리기", MissionKind.Distance, 500, 40),
            new MissionDefinition("coins", "젤리 40개 모으기", MissionKind.Coins, 40, 60),
            new MissionDefinition("combo", "15 콤보 달성하기", MissionKind.Combo, 15, 80)
        };

        public static void PrepareDay(string gameId, DateTime utcNow)
        {
            string day = utcNow.ToString("yyyyMMdd");
            if (SaveSystem.GetString(gameId, "missions.day") == day) return;

            SaveSystem.SaveString(gameId, "missions.day", day);
            foreach (MissionDefinition mission in DailyMissions)
            {
                SaveSystem.SaveInt(gameId, ProgressKey(mission.Id), 0);
                SaveSystem.SaveInt(gameId, ClaimedKey(mission.Id), 0);
            }
        }

        public static void RecordRun(string gameId, int distance, int coins, int maxCombo)
        {
            PrepareDay(gameId, DateTime.UtcNow);
            foreach (MissionDefinition mission in DailyMissions)
            {
                int current = GetProgress(gameId, mission);
                int next = mission.Kind switch
                {
                    MissionKind.Distance => current + Mathf.Max(0, distance),
                    MissionKind.Coins => current + Mathf.Max(0, coins),
                    MissionKind.Combo => Mathf.Max(current, maxCombo),
                    _ => current
                };
                SaveSystem.SaveInt(gameId, ProgressKey(mission.Id), Mathf.Min(mission.Target, next));
            }
        }

        public static int GetProgress(string gameId, MissionDefinition mission) =>
            Mathf.Clamp(SaveSystem.GetInt(gameId, ProgressKey(mission.Id)), 0, mission.Target);

        public static bool IsClaimed(string gameId, MissionDefinition mission) =>
            SaveSystem.GetInt(gameId, ClaimedKey(mission.Id)) != 0;

        public static bool CanClaim(string gameId, MissionDefinition mission) =>
            GetProgress(gameId, mission) >= mission.Target && !IsClaimed(gameId, mission);

        public static bool Claim(string gameId, MissionDefinition mission)
        {
            if (!CanClaim(gameId, mission)) return false;
            SaveSystem.SaveInt(gameId, ClaimedKey(mission.Id), 1);
            SaveSystem.AddInt(gameId, ShopKeys.Currency, mission.Reward);
            return true;
        }

        private static string ProgressKey(string id) => $"missions.{id}.progress";
        private static string ClaimedKey(string id) => $"missions.{id}.claimed";
    }
}
