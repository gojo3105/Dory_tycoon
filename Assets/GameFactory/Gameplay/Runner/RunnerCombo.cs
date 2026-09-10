using System;
using GameFactory.Core;
using GameFactory.Core.Spec;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>Turns uninterrupted jelly collection into coin multipliers and a short fever state.</summary>
    public class RunnerCombo : MonoBehaviour
    {
        public static RunnerCombo Instance { get; private set; }

        public int Combo { get; private set; }
        public int Multiplier { get; private set; } = 1;
        public float FeverProgress { get; private set; }
        public bool IsFever { get; private set; }
        public int PeakCombo { get; private set; }

        public event Action<int, int, float, bool> StateChanged;

        private float comboWindow = 2.4f;
        private int feverPickups = 24;
        private float feverDuration = 6f;
        private int maxCoinMultiplier = 5;
        private float comboTimer;
        private float feverTimer;

        private void Awake() => Instance = this;

        public void Configure(RunnerProgressionConfig config)
        {
            if (config != null)
            {
                comboWindow = Mathf.Max(0.5f, config.comboWindow);
                feverPickups = Mathf.Max(5, config.feverPickups);
                feverDuration = Mathf.Max(1f, config.feverDuration);
                maxCoinMultiplier = Mathf.Clamp(config.maxCoinMultiplier, 1, 10);
            }

            ResetRun();
        }

        /// <summary>Registers one jelly and returns the currency value after the current multiplier.</summary>
        public int RegisterPickup(int baseValue)
        {
            GameManager manager = GameManager.Instance;
            if (manager == null || manager.CurrentState != GameManager.GameState.Playing)
                return Mathf.Max(0, baseValue);

            Combo++;
            PeakCombo = Mathf.Max(PeakCombo, Combo);
            comboTimer = comboWindow;
            Multiplier = Mathf.Min(maxCoinMultiplier, 1 + Combo / 8);

            if (!IsFever)
            {
                FeverProgress = Mathf.Clamp01(FeverProgress + 1f / feverPickups);
                if (FeverProgress >= 1f)
                {
                    IsFever = true;
                    feverTimer = feverDuration;
                    Multiplier = maxCoinMultiplier;
                    VfxManager.Instance?.PlayBurst(transform.position, new Color(1f, 0.45f, 0.1f), 0.35f, 28);
                }
            }

            Notify();
            return Mathf.Max(0, baseValue) * Multiplier;
        }

        private void Update()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null || manager.CurrentState != GameManager.GameState.Playing) return;

            if (IsFever)
            {
                feverTimer = Mathf.Max(0f, feverTimer - Time.deltaTime);
                FeverProgress = feverTimer / feverDuration;
                if (feverTimer <= 0f)
                {
                    IsFever = false;
                    Combo = 0;
                    Multiplier = 1;
                }
                Notify();
                return;
            }

            if (Combo <= 0) return;
            comboTimer -= Time.deltaTime;
            if (comboTimer > 0f) return;

            Combo = 0;
            Multiplier = 1;
            Notify();
        }

        private void ResetRun()
        {
            Combo = 0;
            Multiplier = 1;
            FeverProgress = 0f;
            IsFever = false;
            comboTimer = 0f;
            feverTimer = 0f;
            PeakCombo = 0;
            Notify();
        }

        private void Notify() => StateChanged?.Invoke(Combo, Multiplier, FeverProgress, IsFever);

        private void OnDestroy()
        {
            if (Instance == this) Instance = null;
        }
    }
}
