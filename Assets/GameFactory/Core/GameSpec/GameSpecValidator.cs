using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace GameFactory.Core.Spec
{
    public readonly struct ValidationResult
    {
        public bool IsValid { get; }
        public IReadOnlyList<string> Errors { get; }

        public ValidationResult(IReadOnlyList<string> errors)
        {
            Errors = errors;
            IsValid = errors.Count == 0;
        }
    }

    /// <summary>
    /// Structural validation for a single GameSpec: does it contain enough
    /// well-formed data to generate a game? This does NOT check cross-file
    /// concerns (duplicate ids, bundle id collisions) - see
    /// Assets/GameFactory/Editor/GameValidator.cs for that.
    /// </summary>
    public static class GameSpecValidator
    {
        private static readonly Regex IdPattern = new Regex("^[a-z][a-z0-9_]*$", RegexOptions.Compiled);

        private static readonly HashSet<string> KnownDifficulties = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "Easy", "Medium", "Hard"
        };

        private static readonly HashSet<string> KnownMechanicModules = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "", "Jump", "DoubleJump", "Dash", "WallJump", "GravitySwitch", "Teleport",
            "TimeSlow", "FallingFloor", "MovingPlatform", "Enemy", "Boss", "Weapon",
            "Collectible", "Checkpoint"
        };

        public static ValidationResult Validate(GameSpec spec)
        {
            var errors = new List<string>();

            if (spec == null)
            {
                errors.Add("GameSpec is null.");
                return new ValidationResult(errors);
            }

            ValidateGame(spec.game, errors);
            ValidatePlayer(spec.player, spec.mechanics, errors);
            ValidateLevel(spec.level, errors);
            ValidateEnergy(spec.energy, errors);
            ValidateRunnerProgression(spec.runnerProgression, errors);
            ValidateEnemy(spec.enemy, errors);
            ValidateSpecial(spec.special, errors);

            return new ValidationResult(errors);
        }

        private static void ValidateGame(GameInfo game, List<string> errors)
        {
            if (game == null)
            {
                errors.Add("game: section is missing.");
                return;
            }

            if (string.IsNullOrWhiteSpace(game.id) || !IdPattern.IsMatch(game.id))
            {
                errors.Add($"game.id '{game.id}' must be lowercase snake_case, e.g. 'game01'.");
            }

            if (string.IsNullOrWhiteSpace(game.title))
            {
                errors.Add("game.title must not be empty.");
            }

            if (!Enum.TryParse<GameGenre>(game.genre, ignoreCase: true, out _))
            {
                errors.Add($"game.genre '{game.genre}' is not a known GameGenre value.");
            }
        }

        private static void ValidatePlayer(PlayerConfig player, MechanicsConfig mechanics, List<string> errors)
        {
            if (player == null)
            {
                errors.Add("player: section is missing.");
                return;
            }

            if (player.moveSpeed <= 0f)
            {
                errors.Add($"player.moveSpeed must be > 0 (got {player.moveSpeed}).");
            }

            if (mechanics != null && mechanics.jump && player.jumpPower <= 0f)
            {
                errors.Add($"player.jumpPower must be > 0 when mechanics.jump is true (got {player.jumpPower}).");
            }
        }

        private static void ValidateLevel(LevelConfig level, List<string> errors)
        {
            if (level == null)
            {
                errors.Add("level: section is missing.");
                return;
            }

            if (level.levelCount < 1)
            {
                errors.Add($"level.levelCount must be >= 1 (got {level.levelCount}).");
            }

            if (!KnownDifficulties.Contains(level.difficulty))
            {
                errors.Add($"level.difficulty '{level.difficulty}' must be one of Easy/Medium/Hard.");
            }

            if (level.length <= 0f)
            {
                errors.Add($"level.length must be > 0 (got {level.length}).");
            }
        }

        private static void ValidateRunnerProgression(RunnerProgressionConfig progression, List<string> errors)
        {
            if (progression == null)
            {
                errors.Add("runnerProgression: section is missing.");
                return;
            }

            if (progression.comboWindow <= 0f)
                errors.Add("runnerProgression.comboWindow must be > 0.");
            if (progression.feverPickups < 5)
                errors.Add("runnerProgression.feverPickups must be >= 5.");
            if (progression.feverDuration <= 0f)
                errors.Add("runnerProgression.feverDuration must be > 0.");
            if (progression.maxCoinMultiplier < 1)
                errors.Add("runnerProgression.maxCoinMultiplier must be >= 1.");
            if (progression.speedGainPer100m < 0f)
                errors.Add("runnerProgression.speedGainPer100m must be >= 0.");
            if (progression.maxSpeedMultiplier < 1f)
                errors.Add("runnerProgression.maxSpeedMultiplier must be >= 1.");
        }

        private static void ValidateEnemy(EnemyConfig enemy, List<string> errors)
        {
            if (enemy == null)
            {
                errors.Add("enemy: section is missing.");
                return;
            }

            if (enemy.enabled && enemy.types < 1)
            {
                errors.Add("enemy.types must be >= 1 when enemy.enabled is true.");
            }
        }

        private static void ValidateEnergy(EnergyConfig energy, List<string> errors)
        {
            if (energy == null)
            {
                errors.Add("energy: section is missing.");
                return;
            }

            if (!energy.enabled) return;

            if (energy.drainPerSecond <= 0f)
            {
                errors.Add($"energy.drainPerSecond must be > 0 when energy.enabled is true (got {energy.drainPerSecond}).");
            }

            if (energy.refillPerPickup <= 0f)
            {
                errors.Add($"energy.refillPerPickup must be > 0 when energy.enabled is true (got {energy.refillPerPickup}).");
            }
        }

        private static void ValidateSpecial(SpecialConfig special, List<string> errors)
        {
            if (special == null)
            {
                errors.Add("special: section is missing.");
                return;
            }

            if (!KnownMechanicModules.Contains(special.mechanic ?? string.Empty))
            {
                errors.Add($"special.mechanic '{special.mechanic}' does not match a known module under Assets/GameFactory/Modules/.");
            }
        }
    }
}
