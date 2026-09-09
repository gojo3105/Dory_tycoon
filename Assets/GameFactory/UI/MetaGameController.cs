using System;
using System.Globalization;
using GameFactory.Core;
using GameFactory.Gameplay.Runner;
using UnityEngine;
using UnityEngine.UI;

namespace GameFactory.UI
{
    /// <summary>Connects stages, missions, progression, tutorial, settings, and rewarded ads to the generated UI.</summary>
    public class MetaGameController : MonoBehaviour
    {
        [Header("Title progression")]
        [SerializeField] private Text levelText;
        [SerializeField] private Text titleCurrencyText;
        [SerializeField] private Text stageTitleText;
        [SerializeField] private Text stageDetailsText;
        [SerializeField] private Text dailyRewardText;
        [SerializeField] private Button previousStageButton;
        [SerializeField] private Button nextStageButton;

        [Header("Missions")]
        [SerializeField] private GameObject missionPanel;
        [SerializeField] private Button[] missionOpenButtons;
        [SerializeField] private Button missionCloseButton;
        [SerializeField] private Text[] missionProgressTexts;
        [SerializeField] private Button[] missionClaimButtons;
        [SerializeField] private Text[] missionClaimLabels;

        [Header("Settings")]
        [SerializeField] private GameObject settingsPanel;
        [SerializeField] private Button[] settingsOpenButtons;
        [SerializeField] private Button settingsCloseButton;
        [SerializeField] private Button soundButton;
        [SerializeField] private Text soundLabel;
        [SerializeField] private Button vibrationButton;
        [SerializeField] private Text vibrationLabel;
        [SerializeField] private Button adsPrivacyButton;
        [SerializeField] private Text adsPrivacyLabel;

        [Header("Tutorial")]
        [SerializeField] private GameObject tutorialPanel;
        [SerializeField] private Text tutorialStepText;
        [SerializeField] private Text tutorialBodyText;
        [SerializeField] private Button tutorialNextButton;
        [SerializeField] private Text tutorialNextLabel;

        [Header("Run rewards")]
        [SerializeField] private Text runProgressText;
        [SerializeField] private Button rewardedCoinsButton;
        [SerializeField] private Text rewardedCoinsLabel;

        private string gameId;
        private int tutorialPage;
        private int lastRunCoins;
        private bool rewardedCoinsClaimed;
        private RunnerCombo combo;
        private MonetizationService monetization;

        private static readonly string[] TutorialBodies =
        {
            "왼쪽 점프 버튼을 누르세요.\n공중에서 한 번 더 누르면 이단 점프!",
            "오른쪽 슬라이드 버튼을 누르고 있으면\n낮은 통로를 안전하게 지나갑니다.",
            "젤리를 연속으로 모아 피버를 발동하세요.\n광고는 기본 비개인화로 설정하며 설정에서 바꿀 수 있습니다."
        };

        public void SetTitleReferences(Text level, Text currency, Text stageTitle, Text stageDetails, Text dailyReward,
            Button previousStage, Button nextStage)
        {
            levelText = level;
            titleCurrencyText = currency;
            stageTitleText = stageTitle;
            stageDetailsText = stageDetails;
            dailyRewardText = dailyReward;
            previousStageButton = previousStage;
            nextStageButton = nextStage;
        }

        public void SetMissionReferences(GameObject panel, Button[] openButtons, Button close,
            Text[] progress, Button[] claims, Text[] claimLabels)
        {
            missionPanel = panel;
            missionOpenButtons = openButtons;
            missionCloseButton = close;
            missionProgressTexts = progress;
            missionClaimButtons = claims;
            missionClaimLabels = claimLabels;
        }

        public void SetSettingsReferences(GameObject panel, Button[] openButtons, Button close,
            Button sound, Text soundText, Button vibration, Text vibrationText,
            Button adsPrivacy, Text adsPrivacyText)
        {
            settingsPanel = panel;
            settingsOpenButtons = openButtons;
            settingsCloseButton = close;
            soundButton = sound;
            soundLabel = soundText;
            vibrationButton = vibration;
            vibrationLabel = vibrationText;
            adsPrivacyButton = adsPrivacy;
            adsPrivacyLabel = adsPrivacyText;
        }

        public void SetTutorialReferences(GameObject panel, Text step, Text body,
            Button next, Text nextLabel)
        {
            tutorialPanel = panel;
            tutorialStepText = step;
            tutorialBodyText = body;
            tutorialNextButton = next;
            tutorialNextLabel = nextLabel;
        }

        public void SetRunRewardReferences(Text progress, Button rewarded, Text rewardedLabel)
        {
            runProgressText = progress;
            rewardedCoinsButton = rewarded;
            rewardedCoinsLabel = rewardedLabel;
        }

        private void Start()
        {
            GameManager manager = GameManager.Instance;
            if (manager == null) return;
            gameId = manager.GameId;
            combo = RunnerCombo.Instance;
            monetization = MonetizationService.Instance;

            MissionSystem.PrepareDay(gameId, DateTime.UtcNow);
            int dailyReward = ProgressionSystem.TryClaimDailyReward(gameId, DateTime.UtcNow);
            if (dailyRewardText != null)
                dailyRewardText.text = dailyReward > 0 ? $"출석 보상 +{dailyReward}" : string.Empty;

            previousStageButton?.onClick.AddListener(() => ChangeStage(-1));
            nextStageButton?.onClick.AddListener(() => ChangeStage(1));
            WireOpenButtons(missionOpenButtons, () => ShowPanel(missionPanel));
            WireOpenButtons(settingsOpenButtons, () => ShowPanel(settingsPanel));
            missionCloseButton?.onClick.AddListener(() => HidePanel(missionPanel));
            settingsCloseButton?.onClick.AddListener(() => HidePanel(settingsPanel));
            soundButton?.onClick.AddListener(ToggleSound);
            vibrationButton?.onClick.AddListener(ToggleVibration);
            adsPrivacyButton?.onClick.AddListener(ToggleAdPrivacy);
            tutorialNextButton?.onClick.AddListener(AdvanceTutorial);
            rewardedCoinsButton?.onClick.AddListener(ShowRewardedCoins);

            if (missionClaimButtons != null)
            {
                for (int i = 0; i < missionClaimButtons.Length; i++)
                {
                    int index = i;
                    missionClaimButtons[i]?.onClick.AddListener(() => ClaimMission(index));
                }
            }

            manager.GameOver += HandleGameOver;
            if (monetization != null)
            {
                monetization.AvailabilityChanged += RefreshRewardedButton;
                monetization.PurchaseSucceeded += HandlePurchaseSucceeded;
            }

            if (missionPanel != null) missionPanel.SetActive(false);
            if (settingsPanel != null) settingsPanel.SetActive(false);
            bool needsTutorial = !ProgressionSystem.TutorialComplete(gameId);
            if (tutorialPanel != null) tutorialPanel.SetActive(needsTutorial);
            tutorialPage = 0;
            RefreshTutorial();
            RefreshAll();
        }

        private void HandleGameOver(int distance, int best)
        {
            int stage = ProgressionSystem.GetSelectedStage(gameId);
            int coins = GameManager.Instance != null ? GameManager.Instance.Coins : 0;
            int peakCombo = combo != null ? combo.PeakCombo : 0;
            MissionSystem.RecordRun(gameId, distance, coins, peakCombo);
            RunProgressResult result = ProgressionSystem.CompleteRun(gameId, stage, distance, coins);

            lastRunCoins = coins;
            rewardedCoinsClaimed = false;
            int completedRuns = SaveSystem.AddInt(gameId, "monetization.completed_runs", 1);
            monetization?.ShowInterstitialIfDue(gameId, completedRuns);
            if (runProgressText != null)
            {
                string clear = result.StageCleared ? " · 스테이지 클리어!" : string.Empty;
                string levelUp = result.LeveledUp ? " · 레벨 업!" : string.Empty;
                runProgressText.text = $"XP +{result.EarnedXp} · 별 {result.StageStars}/3{clear}{levelUp}";
            }
            RefreshAll();
        }

        private void ChangeStage(int delta)
        {
            int current = ProgressionSystem.GetSelectedStage(gameId);
            ProgressionSystem.SelectStage(gameId, current + delta);
            FindFirstObjectByType<RunnerGameInitializer>()?.ApplySelectedStage();
            RefreshProgression();
        }

        private void ClaimMission(int index)
        {
            if (index < 0 || index >= MissionSystem.DailyMissions.Length) return;
            MissionSystem.Claim(gameId, MissionSystem.DailyMissions[index]);
            RefreshAll();
        }

        private void ShowRewardedCoins()
        {
            if (rewardedCoinsClaimed || lastRunCoins <= 0 || monetization == null) return;
            monetization.ShowRewarded(success =>
            {
                if (!success || rewardedCoinsClaimed) return;
                rewardedCoinsClaimed = true;
                SaveSystem.AddInt(gameId, ShopKeys.Currency, lastRunCoins);
                RefreshAll();
            });
        }

        private void ToggleSound()
        {
            SettingsSystem.SoundEnabled = !SettingsSystem.SoundEnabled;
            RefreshSettings();
        }

        private void ToggleVibration()
        {
            SettingsSystem.VibrationEnabled = !SettingsSystem.VibrationEnabled;
            RefreshSettings();
        }

        private void ToggleAdPrivacy()
        {
            bool personalized = SettingsSystem.AdsConsentSet && !SettingsSystem.PersonalizedAds;
            monetization?.ApplyAdConsent(personalized);
            if (monetization == null) SettingsSystem.PersonalizedAds = personalized;
            RefreshSettings();
        }

        private void AdvanceTutorial()
        {
            tutorialPage++;
            if (tutorialPage < TutorialBodies.Length)
            {
                RefreshTutorial();
                return;
            }

            ProgressionSystem.CompleteTutorial(gameId);
            if (!SettingsSystem.AdsConsentSet)
                monetization?.ApplyAdConsent(false);
            HidePanel(tutorialPanel);
        }

        private void RefreshAll()
        {
            RefreshProgression();
            RefreshMissions();
            RefreshSettings();
            RefreshRewardedButton();
        }

        private void RefreshProgression()
        {
            int level = ProgressionSystem.GetLevel(gameId);
            int xp = ProgressionSystem.GetXp(gameId);
            if (levelText != null) levelText.text = $"LV.{level}  {xp}/{ProgressionSystem.XpToNextLevel(level)} XP";
            if (titleCurrencyText != null)
                titleCurrencyText.text = SaveSystem.GetInt(gameId, ShopKeys.Currency).ToString("N0", CultureInfo.InvariantCulture);

            int selected = ProgressionSystem.GetSelectedStage(gameId);
            int unlocked = ProgressionSystem.GetUnlockedStage(gameId);
            StageDefinition stage = ProgressionSystem.GetStage(selected);
            int stars = ProgressionSystem.GetStageStars(gameId, selected);
            if (stageTitleText != null) stageTitleText.text = $"스테이지 {selected} · {stage.Name}";
            if (stageDetailsText != null)
                stageDetailsText.text = $"목표 {stage.TargetDistance}m  ·  최고 {ProgressionSystem.GetStageBest(gameId, selected)}m  ·  별 {stars}/3";
            if (previousStageButton != null) previousStageButton.interactable = selected > 1;
            if (nextStageButton != null) nextStageButton.interactable = selected < unlocked;
        }

        private void RefreshMissions()
        {
            MissionSystem.PrepareDay(gameId, DateTime.UtcNow);
            for (int i = 0; i < MissionSystem.DailyMissions.Length; i++)
            {
                MissionDefinition mission = MissionSystem.DailyMissions[i];
                int progress = MissionSystem.GetProgress(gameId, mission);
                bool claimed = MissionSystem.IsClaimed(gameId, mission);
                if (missionProgressTexts != null && i < missionProgressTexts.Length && missionProgressTexts[i] != null)
                    missionProgressTexts[i].text = $"{progress.ToString("N0", CultureInfo.InvariantCulture)} / {mission.Target}";
                if (missionClaimLabels != null && i < missionClaimLabels.Length && missionClaimLabels[i] != null)
                    missionClaimLabels[i].text = claimed ? "완료" : MissionSystem.CanClaim(gameId, mission) ? $"받기 +{mission.Reward}" : $"+{mission.Reward}";
                if (missionClaimButtons != null && i < missionClaimButtons.Length && missionClaimButtons[i] != null)
                    missionClaimButtons[i].interactable = MissionSystem.CanClaim(gameId, mission);
            }
        }

        private void RefreshSettings()
        {
            if (soundLabel != null) soundLabel.text = $"효과음  {(SettingsSystem.SoundEnabled ? "켜짐" : "꺼짐")}";
            if (vibrationLabel != null) vibrationLabel.text = $"진동  {(SettingsSystem.VibrationEnabled ? "켜짐" : "꺼짐")}";
            if (adsPrivacyLabel != null)
            {
                adsPrivacyLabel.text = !SettingsSystem.AdsConsentSet
                    ? "광고 개인정보 선택"
                    : $"맞춤 광고  {(SettingsSystem.PersonalizedAds ? "허용" : "비허용")}";
            }
        }

        private void RefreshRewardedButton()
        {
            bool ready = lastRunCoins > 0 && !rewardedCoinsClaimed && monetization != null && monetization.IsRewardedReady;
            if (rewardedCoinsButton != null) rewardedCoinsButton.interactable = ready;
            if (rewardedCoinsLabel != null)
            {
                rewardedCoinsLabel.text = rewardedCoinsClaimed ? "보상 받음" : ready ? $"광고 보고 +{lastRunCoins}" : "광고 준비 중";
            }
        }

        private void HandlePurchaseSucceeded(string productId) => RefreshAll();

        private void RefreshTutorial()
        {
            if (tutorialStepText != null) tutorialStepText.text = $"처음 달리기  {tutorialPage + 1}/{TutorialBodies.Length}";
            if (tutorialBodyText != null) tutorialBodyText.text = TutorialBodies[Mathf.Clamp(tutorialPage, 0, TutorialBodies.Length - 1)];
            if (tutorialNextLabel != null) tutorialNextLabel.text = tutorialPage == TutorialBodies.Length - 1 ? "시작 준비" : "다음";
        }

        private static void WireOpenButtons(Button[] buttons, UnityEngine.Events.UnityAction action)
        {
            if (buttons == null) return;
            foreach (Button button in buttons) button?.onClick.AddListener(action);
        }

        private static void ShowPanel(GameObject panel)
        {
            PanelTransition transition = panel != null ? panel.GetComponent<PanelTransition>() : null;
            if (transition != null) transition.Show();
            else if (panel != null) panel.SetActive(true);
        }

        private static void HidePanel(GameObject panel)
        {
            PanelTransition transition = panel != null ? panel.GetComponent<PanelTransition>() : null;
            if (transition != null) transition.Hide();
            else if (panel != null) panel.SetActive(false);
        }

        private void OnDestroy()
        {
            if (GameManager.Instance != null) GameManager.Instance.GameOver -= HandleGameOver;
            if (monetization != null)
            {
                monetization.AvailabilityChanged -= RefreshRewardedButton;
                monetization.PurchaseSucceeded -= HandlePurchaseSucceeded;
            }
        }
    }
}
