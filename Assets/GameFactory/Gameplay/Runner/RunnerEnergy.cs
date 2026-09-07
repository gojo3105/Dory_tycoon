using System;
using GameFactory.Core;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>Drains the runner's energy during play and ends the run when it is exhausted.</summary>
    public class RunnerEnergy : MonoBehaviour
    {
        [SerializeField] private float drainPerSecond;
        [SerializeField] private float refillPerPickup;

        private float normalizedValue = 1f;
        private bool gameOverTriggered;
        private bool paused;

        public static RunnerEnergy Instance { get; private set; }
        public float NormalizedValue => normalizedValue;

        /// <summary>Raised when the normalized value changes.</summary>
        public event Action<float> EnergyChanged;

        private void Awake()
        {
            Instance = this;
        }

        /// <summary>Applies GameSpec-driven tuning. Called at runtime by RunnerGameInitializer.</summary>
        public void Configure(float drainRate, float pickupRefill)
        {
            drainPerSecond = drainRate;
            refillPerPickup = pickupRefill;
            normalizedValue = 1f;
            gameOverTriggered = false;
            paused = false;
            EnergyChanged?.Invoke(normalizedValue);
        }

        /// <summary>Mirrors the HUD pause state so draining never depends on timeScale.</summary>
        public void SetPaused(bool isPaused) => paused = isPaused;

        /// <summary>Refills energy for one collected pickup without exceeding a full gauge.</summary>
        public void RefillFromPickup()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null || manager.CurrentState != GameManager.GameState.Playing) return;

            float refilled = Mathf.Clamp01(normalizedValue + refillPerPickup);
            if (refilled == normalizedValue) return;

            normalizedValue = refilled;
            EnergyChanged?.Invoke(normalizedValue);
        }

        private void Update()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null || manager.CurrentState != GameManager.GameState.Playing || paused || gameOverTriggered)
            {
                return;
            }

            float drained = Mathf.Clamp01(normalizedValue - drainPerSecond * Time.deltaTime);
            if (drained == normalizedValue) return;

            normalizedValue = drained;
            EnergyChanged?.Invoke(normalizedValue);

            if (normalizedValue > 0f) return;

            // Latch before notifying GameManager so zero can never request game over twice,
            // even if another listener changes state during the callback chain.
            gameOverTriggered = true;
            manager.TriggerGameOver();
        }

        private void OnDestroy()
        {
            if (Instance == this) Instance = null;
        }
    }
}
