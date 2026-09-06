using System;

namespace GameFactory.Core.Spec
{
    /// <summary>
    /// Root data structure for a GameSpec JSON file. Every field is a plain
    /// value type so it round-trips cleanly through UnityEngine.JsonUtility
    /// (no dictionaries, no polymorphism, no nullable reference types).
    /// </summary>
    [Serializable]
    public class GameSpec
    {
        public GameInfo game = new GameInfo();
        public PlayerConfig player = new PlayerConfig();
        public MechanicsConfig mechanics = new MechanicsConfig();
        public LevelConfig level = new LevelConfig();
        public EnemyConfig enemy = new EnemyConfig();
        public SpecialConfig special = new SpecialConfig();
        public ThemeConfig theme = new ThemeConfig();
    }

    [Serializable]
    public class GameInfo
    {
        /// <summary>Lowercase, underscore-separated unique id, e.g. "game01".</summary>
        public string id = string.Empty;
        public string title = string.Empty;
        /// <summary>Must match a name in GameGenre.</summary>
        public string genre = string.Empty;
    }

    [Serializable]
    public class PlayerConfig
    {
        public float moveSpeed = 6f;
        public float jumpPower = 10f;

        /// <summary>
        /// Multiplier on Unity's -9.81 gravity for the player body.
        ///
        /// WHY THIS IS A SPEC FIELD. Nothing set it, so Unity's default of 1
        /// applied and a jumpPower of 10 produced a 2.04 SECOND hang time, a
        /// 5.1 unit arc height - the camera's half-height is 5, so the player
        /// left the screen - and 12.2 units of forward travel, against an
        /// obstacle spacing of 4 to 8. One jump sailed over two or three
        /// obstacles and often landed on top of the next one. A runner's whole
        /// feel lives in this number; it belongs in the spec next to jumpPower,
        /// not in Unity's defaults.
        /// </summary>
        public float gravityScale = 3.5f;
    }

    [Serializable]
    public class MechanicsConfig
    {
        public bool jump = true;
        public bool doubleJump;

        /// <summary>
        /// Ducking under overhead obstacles - the runner genre's second verb.
        ///
        /// WHY IT MATTERS. With jump alone every obstacle asks the same
        /// question, "press now?", and the level reads as one note repeated.
        /// A duck makes the level a conversation: things to go over, things to
        /// go under, and the player has to read which is coming. The overhead
        /// obstacles that require it are only spawned when this is true, so an
        /// older spec without the field still generates a playable game.
        /// </summary>
        public bool slide;

        public bool dash;
        public bool wallJump;
        public bool gravitySwitch;
        public bool teleport;
        public bool timeSlow;
    }

    [Serializable]
    public class LevelConfig
    {
        public int levelCount = 1;
        /// <summary>Easy, Medium, or Hard.</summary>
        public string difficulty = "Medium";
        public bool procedural = true;
        /// <summary>Approximate level length in world units (Runner) or cells (Puzzle).</summary>
        public float length = 60f;
    }

    [Serializable]
    public class EnemyConfig
    {
        public bool enabled;
        public int types;
    }

    [Serializable]
    public class SpecialConfig
    {
        /// <summary>Name of a single headline gimmick module, e.g. "GravitySwitch", "FallingFloor".</summary>
        public string mechanic = string.Empty;
    }

    [Serializable]
    public class ThemeConfig
    {
        public string environment = string.Empty;
        public string character = string.Empty;
    }
}
