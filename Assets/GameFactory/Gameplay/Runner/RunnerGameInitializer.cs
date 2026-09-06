using GameFactory.Core;
using GameFactory.Core.Spec;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>
    /// The single place where a Runner scene's GameSpec (Resources/GameSpecs/&lt;id&gt;.json)
    /// becomes concrete gameplay tuning. Nothing else in the Runner genre should
    /// hardcode moveSpeed/jumpPower/difficulty numbers.
    /// </summary>
    public class RunnerGameInitializer : MonoBehaviour
    {
        [SerializeField] private RunnerPlayerController player;
        [SerializeField] private ObstacleSpawner obstacleSpawner;
        [SerializeField] private CoinSpawner coinSpawner;

        /// <summary>Wires structural references. Called at edit time by SceneGenerator.</summary>
        public void SetTargets(RunnerPlayerController playerController, ObstacleSpawner obstacles, CoinSpawner coins)
        {
            player = playerController;
            obstacleSpawner = obstacles;
            coinSpawner = coins;
        }

        private void Start()
        {
            if (GameManager.Instance == null)
            {
                Debug.LogError("[RunnerGameInitializer] No GameManager found in scene; using inspector defaults.");
                return;
            }

            GameSpec spec;
            try
            {
                spec = GameSpecParser.LoadFromResources(GameManager.Instance.GameId);
            }
            catch (GameSpecException e)
            {
                Debug.LogError($"[RunnerGameInitializer] {e.Message} Using inspector defaults.");
                return;
            }

            if (player != null)
            {
                player.Configure(spec.player.moveSpeed, spec.player.jumpPower,
                                 spec.mechanics.gravitySwitch, spec.player.gravityScale,
                                 spec.mechanics.doubleJump, spec.mechanics.slide);
            }

            if (obstacleSpawner != null)
            {
                // The spawner is told how far a jump reaches, not just how hard
                // the level should be. Spacing that ignores the arc is what
                // made obstacles unclearable however the difficulty was set.
                // Overhead bars are gated on the same flag that gives the
                // player the slide: an obstacle with no verb to answer it is
                // not difficulty, it is a wall.
                obstacleSpawner.Configure(
                    spec.level.length, spec.level.difficulty,
                    RunnerPlayerController.JumpDistance(
                        spec.player.moveSpeed, spec.player.jumpPower, spec.player.gravityScale),
                    spec.mechanics.slide);
            }

            if (coinSpawner != null)
            {
                coinSpawner.Configure(spec.level.length);
            }
        }
    }
}
