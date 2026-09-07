using System.Globalization;
using GameFactory.Core;
using GameFactory.Gameplay.Runner;
using UnityEngine;
using UnityEngine.UI;

namespace GameFactory.UI
{
    /// <summary>
    /// Single UI orchestrator for a generated game: the in-run HUD (distance,
    /// coins, pause), the game-over card, and the title screen. Reads only
    /// GameManager's public events/API - it holds no gameplay state.
    /// </summary>
    public class GameUIController : MonoBehaviour
    {
        /// <summary>
        /// The whole in-run HUD, shown and hidden as one. Individually toggling
        /// the score and the coin pill left them visible through the game-over
        /// scrim, where the score competed with the same number on the card.
        /// </summary>
        [SerializeField] private GameObject hudRoot;

        [SerializeField] private Text scoreText;
        [SerializeField] private Text coinText;
        [SerializeField] private Button pauseButton;
        [SerializeField] private Image energyFill;
        [SerializeField] private RunnerEnergy runnerEnergy;

        [SerializeField] private GameObject gameOverPanel;
        [SerializeField] private Text finalScoreText;
        [SerializeField] private Text bestScoreText;
        [SerializeField] private Text runCoinsText;
        [SerializeField] private GameObject newBestBadge;
        [SerializeField] private Button restartButton;
        [SerializeField] private Button homeButton;

        [SerializeField] private GameObject pausePanel;
        [SerializeField] private Button resumeButton;
        [SerializeField] private Button pauseHomeButton;

        [SerializeField] private GameObject titlePanel;
        [SerializeField] private Text titleBestScoreText;
        [SerializeField] private Text titleCurrencyText;
        [SerializeField] private Button playButton;

        private PanelTransition gameOverTransition;
        private PanelTransition titleTransition;
        private PanelTransition pauseTransition;

        /// <summary>Wires the in-run HUD. Called at edit time by SceneGenerator.</summary>
        public void SetHudReferences(GameObject root, Text score, Text coins, Button pause,
            Image energyGaugeFill, RunnerEnergy energy)
        {
            hudRoot = root;
            scoreText = score;
            coinText = coins;
            pauseButton = pause;
            energyFill = energyGaugeFill;
            runnerEnergy = energy;
        }

        /// <summary>Wires the game-over card. Called at edit time by SceneGenerator.</summary>
        public void SetGameOverReferences(GameObject panel, Text finalScore, Text bestScore, Text runCoins,
            GameObject newBest, Button restart, Button home)
        {
            gameOverPanel = panel;
            finalScoreText = finalScore;
            bestScoreText = bestScore;
            runCoinsText = runCoins;
            newBestBadge = newBest;
            restartButton = restart;
            homeButton = home;
        }

        /// <summary>Wires the pause overlay. Called at edit time by SceneGenerator.</summary>
        public void SetPauseReferences(GameObject panel, Button resume, Button home)
        {
            pausePanel = panel;
            resumeButton = resume;
            pauseHomeButton = home;
        }

        /// <summary>Wires the title screen. Called at edit time by SceneGenerator.</summary>
        public void SetTitleReferences(GameObject panel, Text bestScore, Text currency, Button play)
        {
            titlePanel = panel;
            titleBestScoreText = bestScore;
            titleCurrencyText = currency;
            playButton = play;
        }

        private void Start()
        {
            gameOverTransition = gameOverPanel != null ? gameOverPanel.GetComponent<PanelTransition>() : null;
            titleTransition = titlePanel != null ? titlePanel.GetComponent<PanelTransition>() : null;
            pauseTransition = pausePanel != null ? pausePanel.GetComponent<PanelTransition>() : null;

            if (gameOverPanel != null) gameOverPanel.SetActive(false);
            if (pausePanel != null) pausePanel.SetActive(false);
            if (newBestBadge != null) newBestBadge.SetActive(false);

            if (restartButton != null) restartButton.onClick.AddListener(HandleRestartClicked);
            if (homeButton != null) homeButton.onClick.AddListener(HandleHomeClicked);
            if (playButton != null) playButton.onClick.AddListener(HandlePlayClicked);
            if (pauseButton != null) pauseButton.onClick.AddListener(HandlePauseClicked);
            if (resumeButton != null) resumeButton.onClick.AddListener(HandleResumeClicked);
            if (pauseHomeButton != null) pauseHomeButton.onClick.AddListener(HandleHomeClicked);

            GameManager manager = GameManager.Instance;
            if (manager == null) return;

            manager.ScoreChanged += HandleScoreChanged;
            manager.CoinsChanged += HandleCoinsChanged;
            manager.GameOver += HandleGameOver;
            if (runnerEnergy != null)
            {
                runnerEnergy.EnergyChanged += HandleEnergyChanged;
                HandleEnergyChanged(runnerEnergy.NormalizedValue);
            }
            HandleScoreChanged(manager.Score);
            HandleCoinsChanged(manager.Coins);
            RefreshTitleStats();

            // Title starts on top and covers the HUD/gameplay behind it (it
            // is the last sibling added under Canvas by SceneGenerator, so it
            // draws last) until Play is pressed - the character still stands
            // on the start line since physics/ground-check keep running.
            bool onTitle = manager.CurrentState != GameManager.GameState.Playing;
            if (titlePanel != null) titlePanel.SetActive(onTitle);

            // The HUD belongs to the run, not to the title screen, which shows
            // its own coin balance and best score.
            if (hudRoot != null) hudRoot.SetActive(!onTitle);
        }

        private void OnDestroy()
        {
            if (runnerEnergy != null) runnerEnergy.EnergyChanged -= HandleEnergyChanged;

            GameManager manager = GameManager.Instance;
            if (manager == null) return;

            manager.ScoreChanged -= HandleScoreChanged;
            manager.CoinsChanged -= HandleCoinsChanged;
            manager.GameOver -= HandleGameOver;
        }

        /// <summary>
        /// Thousands-separated, invariant. Without the explicit culture a
        /// device set to a locale that groups with "." would show 2.431, which
        /// reads as a decimal.
        /// </summary>
        private static string Format(int value) => value.ToString("N0", CultureInfo.InvariantCulture);

        private void HandleScoreChanged(int score)
        {
            if (scoreText != null) scoreText.text = Format(score);
        }

        private void HandleCoinsChanged(int coins)
        {
            if (coinText != null) coinText.text = Format(coins);
        }

        private void HandleEnergyChanged(float normalizedEnergy)
        {
            if (energyFill != null) energyFill.fillAmount = Mathf.Clamp01(normalizedEnergy);
        }

        private void HandleGameOver(int finalScore, int bestScore)
        {
            // The HUD goes first: the run is over, so the live score would sit
            // through the scrim repeating the number on the card, and the pause
            // button would still be live behind it.
            if (hudRoot != null) hudRoot.SetActive(false);

            if (gameOverTransition != null) gameOverTransition.Show();
            else if (gameOverPanel != null) gameOverPanel.SetActive(true);

            GameManager manager = GameManager.Instance;

            if (finalScoreText != null) finalScoreText.text = Format(finalScore);
            if (runCoinsText != null) runCoinsText.text = manager != null ? $"+{Format(manager.Coins)}" : "+0";

            // On a record run the saved best IS this run's score, so showing it
            // next to the same number twice says nothing. The previous best is
            // the number that makes "신기록" mean something.
            bool isNewBest = manager != null && manager.IsNewBest;
            if (newBestBadge != null) newBestBadge.SetActive(isNewBest);
            if (bestScoreText != null)
            {
                bestScoreText.text = Format(isNewBest && manager != null ? manager.PreviousBest : bestScore);
            }
        }

        private void HandleRestartClicked()
        {
            GameManager.Instance?.RestartGame();
        }

        /// <summary>Reloads without requesting an auto-start, so the reload lands back on the title screen.</summary>
        private void HandleHomeClicked()
        {
            // Home is reachable from the pause overlay, which froze time.
            // ReloadCurrent would otherwise hand the fresh scene a timeScale
            // of 0 and the new run would start already frozen. (GameManager
            // .Awake resets it too; this keeps the fix local to the cause.)
            Time.timeScale = 1f;
            SceneController.ReloadCurrent();
        }

        private void HandlePlayClicked()
        {
            runnerEnergy?.SetPaused(false);

            if (titleTransition != null) titleTransition.Hide();
            else if (titlePanel != null) titlePanel.SetActive(false);

            if (hudRoot != null) hudRoot.SetActive(true);

            GameManager.Instance?.StartGame();
        }

        private void HandlePauseClicked()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null || manager.CurrentState != GameManager.GameState.Playing) return;

            runnerEnergy?.SetPaused(true);
            Time.timeScale = 0f;

            if (pauseTransition != null) pauseTransition.Show();
            else if (pausePanel != null) pausePanel.SetActive(true);
        }

        private void HandleResumeClicked()
        {
            runnerEnergy?.SetPaused(false);
            Time.timeScale = 1f;

            if (pauseTransition != null) pauseTransition.Hide();
            else if (pausePanel != null) pausePanel.SetActive(false);
        }

        private void RefreshTitleStats()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null) return;

            if (titleBestScoreText != null)
            {
                titleBestScoreText.text = Format(SaveSystem.GetBestScore(manager.GameId));
            }

            if (titleCurrencyText != null)
            {
                titleCurrencyText.text = Format(SaveSystem.GetInt(manager.GameId, ShopKeys.Currency));
            }
        }
    }
}
